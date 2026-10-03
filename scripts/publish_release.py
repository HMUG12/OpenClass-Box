"""发布 GitHub Release：创建（或复用）Release 并上传安装包。

用法
----
    python scripts/publish_release.py              # 发布 version.json 里的版本
    python scripts/publish_release.py --dry-run    # 只做检查，不改任何远端状态
    python scripts/publish_release.py --tag v0.2.4 # 临时指定 tag

设计要点（每条都对应一个真实踩过的坑）
----------------------------------------
**幂等**：脚本会因网络中断被重跑。所以先按 tag 查 Release，已存在就复用而不是
新建（否则会出现两个同 tag 的 Release）；同名资产也会先删掉再传 —— 半截的旧
文件比没有更糟。

**网络路径要先探明**：这台机器上，六个代理环境变量全都指向 ``127.0.0.1:44444``，
而那个端口常常没有代理在监听。requests 默认照环境变量走，会一路撞在这个失效
代理上报 ``ProxyError``，看起来像"直连不通"，实际只是变量没清。所以按
"系统代理 → 环境变量 → 纯直连"三种方式依次实测，选第一个真能通的。

**上传端点必须用 Release 自带的 upload_url**：它指向 ``uploads.github.com``。
自己拼 ``api.github.com`` 会拿到 404 —— 那个主机的 assets 端点只支持列出与
删除，不接受 POST。

**token 从 Windows 凭据管理器直读**（而不是起 ``git credential fill`` 子进程），
且只留在内存里，绝不打印。
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.sync_version import load_version  # noqa: E402

OWNER = "HMUG12"
REPO = "OpenClass-Box"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"
# 探测用的端点。注意 rate_limit 是**顶级**端点，不在 /repos/{owner}/{repo} 下面 ——
# 拼成仓库下的路径会返回 404，而 404 看起来像"网络不通"，很容易误判。
PING = "https://api.github.com/rate_limit"
CRED_TARGET = "git:https://github.com"

# 上传的资产类型：安装包 + 校验文件。其余一律不发
ASSET_SUFFIXES = (".exe", ".txt")


# ══════════════════════════════════════════════════════════════
# 凭据
# ══════════════════════════════════════════════════════════════


class _Credential(ctypes.Structure):
    """Windows CREDENTIALW 的最小结构（字段顺序与官方一致）。"""

    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def read_credential(target: str = CRED_TARGET) -> str:
    """从 Windows 凭据管理器读取 git 保存的 token（非 Windows 返回空串）。"""
    try:
        advapi = ctypes.windll.advapi32
    except (AttributeError, OSError):
        return ""
    pointer = ctypes.POINTER(_Credential)()
    if not advapi.CredReadW(target, 1, 0, ctypes.byref(pointer)):
        return ""
    try:
        blob = pointer.contents
        size = int(blob.CredentialBlobSize)
        if size <= 0:
            return ""
        return ctypes.string_at(blob.CredentialBlob, size).decode("utf-16-le", "ignore").strip()
    finally:
        advapi.CredFree(pointer)


def load_token() -> str:
    token = read_credential()
    if token:
        print(f"  已从凭据管理器取得 token（长度 {len(token)}，不显示内容）")
        return token
    raise SystemExit(
        "未找到 GitHub token。\n"
        "请先正常执行一次 git push（凭据管理器里要有 GitHub 凭据），\n"
        "或设置环境变量 GITHUB_TOKEN。"
    )


# ══════════════════════════════════════════════════════════════
# 网络
# ══════════════════════════════════════════════════════════════


def with_https_fallback(proxies: dict[str, str]) -> dict[str, str]:
    """只配了 http 时补上 https。

    Windows 的系统代理语义是"一个地址管所有协议"（除非用了 PAC），注册表里
    常常只写着 http://…。而 requests 是按协议名取键的：只有 http 键时，
    https 请求能否走代理全靠它的内部回退。显式补齐更稳，也更符合用户在
    系统设置里填写的本意。
    """
    if "http" in proxies and "https" not in proxies:
        return {**proxies, "https": proxies["http"]}
    return dict(proxies)


def system_proxy() -> dict[str, str]:
    """读注册表里的系统代理 —— 比环境变量可靠，那是"实际在用的"配置。"""
    proxies: dict[str, str] = {}
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
            server = winreg.QueryValueEx(key, "ProxyServer")[0]
        if enabled and server:
            for part in str(server).split(";"):
                part = part.strip()
                if not part:
                    continue
                scheme, addr = part.split("=", 1) if "=" in part else ("http", part)
                addr = addr if "://" in addr else f"http://{addr}"
                proxies[scheme.strip().lower()] = addr
    except (OSError, ImportError):
        pass

    return with_https_fallback(proxies)


def _session(proxies: dict[str, str] | None) -> requests.Session:
    """构造一个不受环境变量影响的 session。

    ``trust_env = False`` 是关键：否则 requests 会去用那些指向失效端口的
    代理变量，表现为"网络不通"而实际是可直连的。
    """
    session = requests.Session()
    session.trust_env = False
    if proxies:
        session.proxies.update(proxies)
    return session


def pick_network(token: str) -> tuple[requests.Session, dict[str, str] | None]:
    """按"系统代理 → 环境变量 → 纯直连"依次实测，选第一个真能通的。"""
    import os

    # 环境变量这一项要真的把代理取出来用，不能只置 None（那等于重复"纯直连"）。
    # 键名必须转成 requests 认识的 http / https —— 直接沿用 HTTP_PROXY 这种
    # 原名会被静默忽略（requests 的 proxies 字典只认 http、https 两个键），
    # 结果是"选中了环境代理"但实际仍然在直连。
    env_proxies: dict[str, str] = {}
    for key, value in os.environ.items():
        lowered = key.lower()
        if value and lowered in ("http_proxy", "https_proxy"):
            env_proxies["http" if lowered == "http_proxy" else "https"] = value

    candidates: list[tuple[str, dict[str, str] | None]] = [
        ("系统代理", system_proxy() or None),
        ("环境变量", env_proxies or None),
        ("纯直连", {}),
    ]

    print("探测可用网络…", flush=True)
    for label, proxies in candidates:
        session = _session(proxies)
        session.headers["Authorization"] = f"Bearer {token}"
        try:
            probe = session.get(PING, timeout=8)
        except Exception as error:
            print(f"  {label}不通（{type(error).__name__}）", flush=True)
            continue
        if probe.status_code == 200:
            shown = (proxies or {}).get("https") or (proxies or {}).get("http") or "（不走代理）"
            print(f"  可用：{label} → {shown}")
            return session, proxies
        print(f"  {label}不通（HTTP {probe.status_code}）", flush=True)

    raise SystemExit("系统代理 / 环境变量 / 直连三种方式均不通，无法发布。")


# ══════════════════════════════════════════════════════════════
# 资产准备
# ══════════════════════════════════════════════════════════════


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def collect_assets(release_dir: Path, notes_name: str = "RELEASE_NOTES.md") -> list[Path]:
    """要上传的文件 = 安装包 + 自动生成/更新的 SHA256SUMS.txt。

    校验文件自动生成是有原因的：发布说明里引用了它，如果实际不存在，
    那就是一句指向空处的承诺（0.2.0 就是这样）。
    """
    if not release_dir.is_dir():
        raise SystemExit(f"找不到目录：{release_dir}")

    packages = sorted(
        one for one in release_dir.iterdir()
        if one.is_file() and one.suffix.lower() in ASSET_SUFFIXES
        and one.name.upper() != "SHA256SUMS.TXT"
    )
    if not packages:
        raise SystemExit(f"{release_dir} 里没有可发布的文件（{', '.join(ASSET_SUFFIXES)}）")

    sums = release_dir / "SHA256SUMS.txt"
    lines = [f"{sha256_of(one)}  {one.name}" for one in packages]
    content = "\n".join(lines) + "\n"
    # utf-8 无 BOM：带 BOM 会让某些校验工具把第一行的哈希认成文件名的一部分
    sums.write_text(content, encoding="utf-8", newline="\n")
    print(f"\n已生成 {sums.name}（{len(lines)} 个安装包）", flush=True)
    for one, line in zip(packages, lines):
        print(f"  {line[:16]}…  {one.name}")

    notes = ROOT / notes_name
    if notes.is_file():
        print(f"  发布说明：{notes_name}（{len(notes.read_text(encoding='utf-8'))} 字符）")

    return [*packages, sums]


# ══════════════════════════════════════════════════════════════
# Release 操作
# ══════════════════════════════════════════════════════════════


def list_assets(session: requests.Session, release_id: int) -> list[dict[str, Any]]:
    """列出 Release 下的资产，显式校验响应类型。

    URL 一旦拼错（比如把完整 upload_url 当 id 塞进去），GitHub 返回的是**错误
    对象**而不是数组；直接迭代会得到字符串键，报出
    ``'str' object has no attribute 'get'`` —— 一句完全指不出问题的错。
    """
    response = session.get(f"{API}/releases/{release_id}/assets", timeout=60)
    if response.status_code != 200:
        raise SystemExit(
            f"读取资产列表失败：HTTP {response.status_code} {response.text[:300]}"
        )
    data = response.json()
    if not isinstance(data, list):
        raise SystemExit(f"资产列表返回的不是数组：{str(data)[:300]}")
    return data


def find_or_create(
    session: requests.Session, tag: str, name: str, body: str
) -> dict[str, Any]:
    """按 tag 找 Release；没有才创建（幂等的关键）。"""
    existing = session.get(f"{API}/releases/tags/{tag}", timeout=30)
    if existing.status_code == 200:
        data = existing.json()
        print(f"  已存在 Release id={data['id']}，将复用", flush=True)
        return data

    response = session.post(
        f"{API}/releases",
        json={
            "tag_name": tag,
            "name": name,
            "body": body,
            "draft": False,
            "prerelease": False,
        },
        timeout=60,
    )
    if response.status_code not in (200, 201):
        raise SystemExit(f"创建 Release 失败：HTTP {response.status_code} {response.text[:400]}")
    data = response.json()
    print(f"  Release 已创建 id={data['id']}", flush=True)
    return data


def upload_asset(
    session: requests.Session, release_id: int, upload_url: str, path: Path
) -> None:
    """上传单个资产；同名旧资产先删（半截文件比没有更糟）。"""
    for asset in list_assets(session, release_id):
        if asset.get("name") == path.name:
            print(f"  删除同名旧资产 id={asset['id']}", flush=True)
            session.delete(f"{API}/releases/assets/{asset['id']}", timeout=60)

    target = upload_url.replace("{?name,label}", f"?name={path.name}")
    size = path.stat().st_size
    print(f"\n上传 {path.name}（{size / 1024 / 1024:.1f} MB）…", flush=True)

    with path.open("rb") as handle:
        response = session.post(
            target,
            data=handle,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(size),
            },
            timeout=(30, 1800),
        )
    if response.status_code not in (200, 201):
        raise SystemExit(
            f"上传 {path.name} 失败：HTTP {response.status_code} {response.text[:300]}"
        )
    print(f"  完成（HTTP {response.status_code}）", flush=True)


def verify(session: requests.Session, release_id: int, files: list[Path]) -> None:
    """核对远端字节数 —— "上传成功"不等于"传全了"。"""
    print("\n校验远端资产…", flush=True)
    remote = {one["name"]: one for one in list_assets(session, release_id)}

    problems: list[str] = []
    for path in files:
        asset = remote.get(path.name)
        if not asset:
            problems.append(f"{path.name}：远端缺失")
            print(f"  {path.name}：远端缺失 ✗")
            continue
        local_size = path.stat().st_size
        if asset["size"] != local_size:
            problems.append(f"{path.name}：大小 {asset['size']} ≠ 本地 {local_size}")
            print(f"  {path.name}：大小不符 ✗（远端 {asset['size']} / 本地 {local_size}）")
        elif asset.get("state") != "uploaded":
            problems.append(f"{path.name}：状态 {asset.get('state')}")
            print(f"  {path.name}：状态 {asset.get('state')} ✗")
        else:
            print(f"  {path.name}：{asset['size']} 字节 · {asset['state']} OK")

    if problems:
        raise SystemExit(
            "\n校验未通过：\n  - " + "\n  - ".join(problems)
            + "\n\n可安全重跑本脚本（幂等：会复用 Release 并覆盖同名资产）。"
        )


# ══════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════


def main() -> int:
    parser = argparse.ArgumentParser(description="发布 OpenClass-Box 的 GitHub Release")
    parser.add_argument("--tag", default="", help="覆盖 version.json 里的 tag")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只做检查（版本、文件、哈希），不访问 GitHub 也不改远端",
    )
    args = parser.parse_args()

    info = load_version()
    tag = args.tag or info["tag"]
    version = tag.lstrip("v")
    release_dir = ROOT / "release" / tag.removeprefix("v")
    notes_path = ROOT / "RELEASE_NOTES.md"

    print(f"准备发布 {tag}")
    print(f"  版本目录：{release_dir.relative_to(ROOT)}")
    print(f"  发布说明：{notes_path.name}"
          f"{'' if notes_path.is_file() else '（缺失！）'}")

    files = collect_assets(release_dir)
    total = sum(one.stat().st_size for one in files) / 1024 / 1024
    print(f"\n待发布 {len(files)} 个文件，合计 {total:.0f} MB")

    if args.dry_run:
        print("\n[dry-run] 未访问 GitHub，远端状态未改动。")
        return 0

    if not notes_path.is_file():
        raise SystemExit(f"缺少发布说明：{notes_path}")

    token = load_token()
    session, _ = pick_network(token)
    retry = HTTPAdapter(
        max_retries=requests.packages.urllib3.util.Retry(
            total=4, connect=4, backoff_factor=2, status_forcelist=(502, 503, 504)
        )
    )
    session.mount("https://", retry)
    session.headers.update(
        {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "OpenClass-Box-release",
        }
    )

    print("\n检查 Release…", flush=True)
    release = find_or_create(
        session, tag, f"OpenClass-Box {version}", notes_path.read_text(encoding="utf-8")
    )

    # 关键：必须用 Release 自带的 upload_url（uploads.github.com），
    # 自己拼 api.github.com 会被 404 —— 那个端点不接受 POST 上传。
    upload_url = release["upload_url"]
    if "uploads.github.com" not in upload_url:
        raise SystemExit(f"upload_url 不含 uploads.github.com，疑似 API 变更：{upload_url}")

    for path in files:
        upload_asset(session, release["id"], upload_url, path)

    verify(session, release["id"], files)

    print(f"\n发布完成：https://github.com/{OWNER}/{REPO}/releases/tag/{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
