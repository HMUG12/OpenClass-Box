"""
插件市场（轻量版）—— 一个 JSON 索引 + 一键下载，刻意不做「应用商店」。

为什么保持轻量：完整应用商店（审核、版本管理、依赖解析、账号体系）会把
「社区分享」变成「平台运营」，也背离了零配置插件的初衷。这里只要三件事：

  1. **官方索引**：仓库里的一个 JSON，列出社区插件、下载地址、适用角色；
  2. **一键安装**：下载 zip → 校验 sha256 → 解压到 tools/ → 自动重新扫描；
  3. **离线可用**：索引与插件包都能手动下载后导入（教室经常没有外网）。

安全考虑（插件是可执行内容，必须谨慎）：
  · 只接受 https 下载地址，拒绝 http 与本地协议；
  · 索引声明了 sha256 就必须校验一致，不一致直接拒绝安装；
  · 解压前逐条检查路径，拒绝目录穿越（..）与绝对路径，并限制解压总量；
  · 安装位置固定在 tools/ 下；卸载只允许删除 tools/ 下带 tool.json 的目录。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from .config import config
from .paths import config_dir, tools_dir

DEFAULT_INDEX_URL = (
    "https://raw.githubusercontent.com/HMUG12/OpenClass-Box/main/plugins/index.json"
)
_TIMEOUT = 15
_MAX_DOWNLOAD = 300 * 1024 * 1024   # 单个插件包上限 300 MB
_MAX_EXTRACT = 800 * 1024 * 1024    # 解压后总量上限 800 MB
_CACHE_TTL = 3600                   # 索引缓存 1 小时


def index_url() -> str:
    return str(config.get("market_index_url", DEFAULT_INDEX_URL))


def set_index_url(url: str) -> dict[str, Any]:
    url = (url or "").strip()
    if url and not url.lower().startswith(("https://", "http://")):
        return {"ok": False, "message": "索引地址必须是 http(s) 链接"}
    ok = config.set("market_index_url", url or DEFAULT_INDEX_URL)
    return {"ok": ok, "message": "已保存" if ok else "保存失败（数据目录不可写）", "url": index_url()}


# ══════════════════════════════════════════════════════════════
# 索引
# ══════════════════════════════════════════════════════════════

def _cache_file() -> Path:
    folder = config_dir() / "market"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "index.json"


def _read_cache() -> tuple[float, dict[str, Any]] | None:
    path = _cache_file()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        stamp = float(data.get("_cachedAt") or 0)
        return stamp, data
    except (OSError, ValueError):
        return None


def _write_cache(data: dict[str, Any]) -> None:
    try:
        _cache_file().write_text(
            json.dumps({**data, "_cachedAt": time.time()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def fetch_index(force: bool = False) -> dict[str, Any]:
    """获取插件索引：优先走网络，失败时回落到本地缓存（离线也能看目录）。"""
    if not force:
        cached = _read_cache()
        if cached and time.time() - cached[0] < _CACHE_TTL:
            return {"ok": True, "source": "cache", "index": cached[1], "message": ""}

    url = index_url()
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "OpenClass-Box"})
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            raw = response.read(4 * 1024 * 1024)
        data = json.loads(raw.decode("utf-8"))
        _write_cache(data)
        return {"ok": True, "source": "network", "index": data, "message": ""}
    except Exception as exc:
        cached = _read_cache()
        if cached:
            return {
                "ok": True,
                "source": "cache",
                "index": cached[1],
                "message": f"网络不可用，使用上次获取的索引（{exc}）",
            }
        return {
            "ok": False,
            "source": "none",
            "index": {"plugins": []},
            "message": f"获取插件索引失败（可能未联网或地址不可达）：{exc}",
        }


# ══════════════════════════════════════════════════════════════
# 已安装状态
# ══════════════════════════════════════════════════════════════

def installed_plugins() -> dict[str, dict[str, Any]]:
    """扫描 tools/ 下带 tool.json 的插件目录。"""
    base = tools_dir()
    result: dict[str, dict[str, Any]] = {}
    if not base.is_dir():
        return result
    for folder in base.iterdir():
        if not folder.is_dir():
            continue
        manifest = folder / "tool.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        plugin_id = str(data.get("id") or folder.name)
        result[plugin_id] = {
            "id": plugin_id,
            "name": str(data.get("name") or folder.name),
            "version": str(data.get("version") or ""),
            "author": str(data.get("author") or ""),
            "dir": str(folder),
        }
    return result


def list_plugins(refresh: bool = False) -> dict[str, Any]:
    """市场列表：索引条目 + 本机安装状态。"""
    fetched = fetch_index(refresh)
    index = fetched.get("index") or {}
    plugins = index.get("plugins") or []
    installed = installed_plugins()

    items: list[dict[str, Any]] = []
    for entry in plugins:
        if not isinstance(entry, dict):
            continue
        plugin_id = str(entry.get("id") or "")
        if not plugin_id:
            continue
        have = installed.get(plugin_id)
        items.append(
            {
                **entry,
                "installed": bool(have),
                "installedVersion": (have or {}).get("version", ""),
                "updatable": bool(have) and bool(entry.get("version"))
                and str(entry.get("version")) != (have or {}).get("version", ""),
            }
        )

    # 本机已装但不在索引里的（自己放进去的插件）也列出来，方便管理
    known = {str(e.get("id")) for e in plugins if isinstance(e, dict)}
    for plugin_id, info in installed.items():
        if plugin_id in known:
            continue
        items.append(
            {
                "id": plugin_id,
                "name": info["name"],
                "desc": "本机插件（不在官方索引中）",
                "author": info.get("author") or "",
                "version": info.get("version") or "",
                "roles": ["本地"],
                "url": "",
                "installed": True,
                "installedVersion": info.get("version") or "",
                "local": True,
            }
        )

    return {
        "ok": fetched.get("ok", False),
        "source": fetched.get("source", "none"),
        "message": fetched.get("message", ""),
        "indexVersion": str(index.get("version") or ""),
        "indexUrl": index_url(),
        "items": items,
        "installedCount": len(installed),
    }


# ══════════════════════════════════════════════════════════════
# 安装 / 卸载
# ══════════════════════════════════════════════════════════════

def _check_url(url: str) -> str:
    url = (url or "").strip()
    if not url.lower().startswith("https://"):
        raise ValueError("出于安全考虑，只允许从 https 地址下载插件")
    return url


def _download(url: str, target: Path) -> int:
    request = urllib.request.Request(url, headers={"User-Agent": "OpenClass-Box"})
    total = 0
    with urllib.request.urlopen(request, timeout=_TIMEOUT * 4) as response, target.open("wb") as out:
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_DOWNLOAD:
                raise ValueError("插件包过大，已中止下载")
            out.write(chunk)
    return total


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(512 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_extract(archive: zipfile.ZipFile, target: Path) -> None:
    """解压前逐条校验路径与总量，防止目录穿越与 zip 炸弹。"""
    members = archive.infolist()
    total = sum(m.file_size for m in members)
    if total > _MAX_EXTRACT:
        raise ValueError("压缩包解压后过大，已中止")

    target_root = target.resolve()
    for member in members:
        name = member.filename.replace("\\", "/")
        if not name or name.endswith("/"):
            continue
        parts = Path(name).parts
        if name.startswith("/") or ".." in parts or (parts and ":" in parts[0]):
            raise ValueError(f"压缩包内含不安全路径：{member.filename}")
        destination = (target_root / name).resolve()
        if target_root not in destination.parents and destination != target_root:
            raise ValueError(f"压缩包内含越权路径：{member.filename}")
    archive.extractall(target)


def _peek_id(archive: zipfile.ZipFile) -> str:
    """从包内 tool.json 读出插件 id（包可能多套一层目录）。

    用包内声明的 id 作为安装目录名，避免出现 classroom-timer-1.0.0 这种
    带版本号的目录 —— 那会让「卸载 / 升级」找不到同一个插件。
    """
    for name in archive.namelist():
        if not name.replace("\\", "/").endswith("tool.json"):
            continue
        try:
            data = json.loads(archive.read(name).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, KeyError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        pid = str(data.get("id") or "").strip()
        if pid and not any(ch in pid for ch in "/\\:"):
            return pid
    return ""


def _find_plugin_dir(plugin_id: str) -> Path | None:
    """按插件 id 找到 tools/ 下的目录（兼顾目录名与清单 id 不一致的情况）。"""
    direct = tools_dir() / plugin_id
    if (direct / "tool.json").is_file():
        return direct
    base = tools_dir()
    if not base.is_dir():
        return None
    for folder in base.iterdir():
        manifest = folder / "tool.json"
        if not folder.is_dir() or not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and str(data.get("id") or "") == plugin_id:
            return folder
    return None


def _normalize_layout(folder: Path) -> None:
    """压缩包若多套了一层目录（GitHub 打包常见），把内容提上来。"""
    if (folder / "tool.json").is_file():
        return
    children = [c for c in folder.iterdir() if c.is_dir()]
    files = [c for c in folder.iterdir() if c.is_file()]
    if len(children) == 1 and not files:
        inner = children[0]
        for item in inner.iterdir():
            shutil.move(str(item), str(folder / item.name))
        inner.rmdir()


def install(
    plugin_id: str,
    url: str = "",
    sha256: str = "",
    version: str = "",
) -> dict[str, Any]:
    """安装（或升级）一个插件。url 为空时从索引里查。"""
    plugin_id = (plugin_id or "").strip()
    if not plugin_id:
        return {"ok": False, "message": "缺少插件标识"}

    if not url:
        fetched = fetch_index()
        index = fetched.get("index") or {}
        spec = next(
            (p for p in (index.get("plugins") or []) if isinstance(p, dict) and p.get("id") == plugin_id),
            None,
        )
        if spec is None:
            return {"ok": False, "message": "索引里没有这个插件"}
        url = str(spec.get("url") or "")
        sha256 = str(spec.get("sha256") or "")
        version = str(spec.get("version") or version)

    try:
        url = _check_url(url)
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}

    temp_dir = config_dir() / "market"
    temp_dir.mkdir(parents=True, exist_ok=True)
    package = temp_dir / f"download-{plugin_id}.zip"

    try:
        size = _download(url, package)
    except Exception as exc:
        return {"ok": False, "message": f"下载失败：{exc}"}

    if sha256:
        actual = _sha256(package)
        if actual.lower() != sha256.strip().lower():
            package.unlink(missing_ok=True)
            return {"ok": False, "message": "校验失败：文件与索引声明的不一致，已拒绝安装"}

    # 目录名以包内声明的 id 为准（索引里的 id 与之不一致时仍能正确升级）
    try:
        with zipfile.ZipFile(package) as probe:
            inner_id = _peek_id(probe)
    except Exception as exc:
        package.unlink(missing_ok=True)
        return {"ok": False, "message": f"无法读取压缩包：{exc}"}
    if inner_id:
        plugin_id = inner_id

    target = tools_dir() / plugin_id
    try:
        if target.exists():
            backup = target.with_name(f"{target.name}.bak")
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            target.rename(backup)
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(package) as archive:
            _safe_extract(archive, target)
        _normalize_layout(target)
    except Exception as exc:
        shutil.rmtree(target, ignore_errors=True)
        return {"ok": False, "message": f"解压失败：{exc}"}
    finally:
        package.unlink(missing_ok=True)

    if not (target / "tool.json").is_file():
        shutil.rmtree(target, ignore_errors=True)
        return {"ok": False, "message": "这个包里没有 tool.json，不是有效的插件包"}

    try:
        from .registry import tool_registry

        tool_registry.scan()
    except Exception:
        pass

    return {
        "ok": True,
        "message": f"已安装插件「{plugin_id}」{(f' v{version}' if version else '')}"
        + f"（{size / 1024:.0f} KB）",
        "path": str(target),
    }


def import_local(path: str) -> dict[str, Any]:
    """从本地 zip 安装（离线导入）。"""
    source = Path((path or "").strip().strip('"'))
    if not source.is_file():
        return {"ok": False, "message": "文件不存在"}
    if source.suffix.lower() != ".zip":
        return {"ok": False, "message": "请选择插件的 zip 包"}

    try:
        with zipfile.ZipFile(source) as probe:
            plugin_id = _peek_id(probe) or source.stem
            target = tools_dir() / plugin_id
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True, exist_ok=True)
            _safe_extract(probe, target)
        _normalize_layout(target)
    except Exception as exc:
        shutil.rmtree(target, ignore_errors=True)
        return {"ok": False, "message": f"导入失败：{exc}"}

    if not (target / "tool.json").is_file():
        shutil.rmtree(target, ignore_errors=True)
        return {"ok": False, "message": "这个压缩包里没有 tool.json，不是有效的插件包"}

    try:
        from .registry import tool_registry

        tool_registry.scan()
    except Exception:
        pass

    return {
        "ok": True,
        "message": f"已从本地导入插件（{plugin_id}）",
        "path": str(target),
        "id": plugin_id,
    }


def uninstall(plugin_id: str) -> dict[str, Any]:
    """卸载插件：只允许删除 tools/ 下带 tool.json 的目录。"""
    plugin_id = (plugin_id or "").strip()
    if not plugin_id or any(ch in plugin_id for ch in "/\\:"):
        return {"ok": False, "message": "插件标识不合法"}

    target = _find_plugin_dir(plugin_id)
    if target is None:
        return {"ok": False, "message": "没有找到这个插件的目录"}
    if not (target / "tool.json").is_file():
        return {"ok": False, "message": "这个目录不是插件（缺少 tool.json），已拒绝删除"}

    try:
        shutil.rmtree(target)
    except OSError as exc:
        return {"ok": False, "message": f"删除失败：{exc}"}

    try:
        from .registry import tool_registry

        tool_registry.scan()
    except Exception:
        pass

    return {"ok": True, "message": f"已卸载插件（{plugin_id}）"}


def index_homepage() -> str:
    """索引地址对应的网页（用于「在浏览器打开索引」）。"""
    parsed = urllib.parse.urlparse(index_url())
    if not parsed.scheme:
        return index_url()
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rsplit('/', 1)[0]}"
