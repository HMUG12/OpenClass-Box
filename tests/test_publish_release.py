"""发布脚本的离线测试。

只测不联网的部分：资产筛选、校验文件生成、错误提示。
网络相关（找路、上传、校验远端）依赖真实 GitHub，不适合放进单测 ——
但那些逻辑在 0.2.3 发布时已实测通过（688 MB 三个资产字节数逐一对齐）。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import publish_release as pr  # noqa: E402


def _make_package(folder: Path, name: str, size: int = 1024) -> Path:
    path = folder / name
    path.write_bytes(b"\0" * size)
    return path


# ── 资产筛选 ────────────────────────────────────────────────


def test_collect_assets_picks_packages_and_sums(tmp_path):
    """安装包 + 自动生成的校验文件，都应进入上传列表。"""
    _make_package(tmp_path, "App-A_Setup.exe")
    _make_package(tmp_path, "App-B_Setup.exe")

    files = pr.collect_assets(tmp_path)

    names = [one.name for one in files]
    assert "App-A_Setup.exe" in names
    assert "App-B_Setup.exe" in names
    assert "SHA256SUMS.txt" in names
    assert len(files) == 3


def test_collect_assets_excludes_unrelated_files(tmp_path):
    """目录里可能还有压缩包、内部说明之类，不该一股脑传上去。"""
    _make_package(tmp_path, "App-A_Setup.exe")
    (tmp_path / "notes.md").write_text("内部说明", encoding="utf-8")
    (tmp_path / "App.zip").write_bytes(b"PK")

    names = [one.name for one in pr.collect_assets(tmp_path)]

    assert names == ["App-A_Setup.exe", "SHA256SUMS.txt"]


def test_collect_assets_does_not_include_itself(tmp_path):
    """已有的 SHA256SUMS.txt 会被重新生成，不能把自己当成待上传的安装包。"""
    _make_package(tmp_path, "App-A_Setup.exe")
    (tmp_path / "SHA256SUMS.txt").write_text("旧的、可能过期的内容", encoding="utf-8")

    files = pr.collect_assets(tmp_path)
    sums = tmp_path / "SHA256SUMS.txt"

    assert files.count(sums) == 1
    # 内容必须是被重新算出来的，不是残留的旧内容
    expected = hashlib.sha256((tmp_path / "App-A_Setup.exe").read_bytes()).hexdigest()
    assert expected in sums.read_text(encoding="utf-8")


# ── 校验文件内容 ────────────────────────────────────────────


def test_sums_use_two_space_separator_and_no_bom(tmp_path):
    """格式要符合 sha256sum 惯例（两个空格），且不能带 BOM。

    带 BOM 时某些校验工具会把第一行的哈希当成文件名的一部分，于是报
    "校验失败" —— 而实际文件没坏。
    """
    package = _make_package(tmp_path, "App-A_Setup.exe", 2048)
    pr.collect_assets(tmp_path)

    raw = (tmp_path / "SHA256SUMS.txt").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "不应带 UTF-8 BOM"

    text = raw.decode("utf-8")
    expected = hashlib.sha256(package.read_bytes()).hexdigest()
    assert text == f"{expected}  App-A_Setup.exe\n"


def test_sums_are_regenerated_when_package_changes(tmp_path):
    """安装包换了，校验文件必须跟着变 —— 否则用户按旧哈希校验会失败。"""
    package = _make_package(tmp_path, "App-A_Setup.exe", 1024)
    pr.collect_assets(tmp_path)
    first = (tmp_path / "SHA256SUMS.txt").read_text(encoding="utf-8")

    package.write_bytes(b"\1" * 4096)
    pr.collect_assets(tmp_path)
    second = (tmp_path / "SHA256SUMS.txt").read_text(encoding="utf-8")

    assert first != second
    assert hashlib.sha256(package.read_bytes()).hexdigest() in second


def test_sha256_of_reads_in_blocks_not_whole_file(tmp_path):
    """大文件（几百 MB）不能一次性读进内存 —— 确认分块读取的结果正确。"""
    import hashlib as _h

    path = _make_package(tmp_path, "big.bin", 1024 * 1024 + 7)
    assert pr.sha256_of(path) == _h.sha256(path.read_bytes()).hexdigest()


# ── 错误提示要能让人知道下一步做什么 ───────────────────────


def test_missing_directory_is_reported_clearly(tmp_path):
    with pytest.raises(SystemExit) as caught:
        pr.collect_assets(tmp_path / "没有这个目录")
    assert "找不到目录" in str(caught.value)


def test_empty_directory_is_reported_clearly(tmp_path):
    """空目录时报"没有可发布的文件"，而不是列出空清单让人以为成功了。"""
    with pytest.raises(SystemExit) as caught:
        pr.collect_assets(tmp_path)
    assert "没有可发布的文件" in str(caught.value)


# ── 凭据与网络配置：不能崩，且要说人话 ──────────────────────


def test_read_credential_returns_string_without_raising():
    """读不到就返回空串，绝不能抛异常 —— 否则发布流程会死在第一步。"""
    assert isinstance(pr.read_credential("绝对不存在的目标"), str)


def test_load_token_gives_actionable_message(monkeypatch):
    """拿不到 token 时，提示要告诉人怎么办，而不只是"失败"。"""
    monkeypatch.setattr(pr, "read_credential", lambda *a, **k: "")
    with pytest.raises(SystemExit) as caught:
        pr.load_token()
    message = str(caught.value)
    assert "GITHUB_TOKEN" in message
    assert "git push" in message


def test_system_proxy_returns_dict():
    """读注册表失败（Linux / 无键）都应返回空 dict，不抛异常。"""
    assert isinstance(pr.system_proxy(), dict)


def test_https_fallback_fills_missing_scheme():
    """注册表里常常只写 http://…，而 requests 按协议名取键。

    不补 https 的话，https 请求能否走代理全靠 requests 的内部回退 ——
    显式补齐才符合"用户在系统设置里填了这个地址"的本意。
    """
    filled = pr.with_https_fallback({"http": "http://127.0.0.1:44444"})
    assert filled["https"] == "http://127.0.0.1:44444"


def test_https_fallback_keeps_explicit_https():
    """分开配置时（http 与 https 不同址）不能覆盖用户填的 https。"""
    original = {"http": "http://a:1", "https": "http://b:2"}
    assert pr.with_https_fallback(original) == original


def test_https_fallback_leaves_other_shapes_alone():
    """没有 http 键（如只有 https 或 socks）时不该瞎补。"""
    assert pr.with_https_fallback({"https": "http://b:2"}) == {"https": "http://b:2"}
    assert pr.with_https_fallback({}) == {}


def test_session_ignores_environment_proxy(monkeypatch):
    """这是今天踩的坑：环境变量里六个代理变量都指向一个没监听的端口。

    requests 默认会照它们走，表现为"网络不通"而实际是可直连的。
    所以 session 必须 trust_env=False。
    """
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:44444")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:44444")

    assert pr._session(None).trust_env is False
    assert pr._session({}).trust_env is False


def test_pick_network_falls_through_to_working_option(monkeypatch):
    """前两种方式不通、纯直连能通时，必须能选到第三种而不是直接失败。"""
    attempts: list[str] = []

    class FakeResponse:
        def __init__(self, code: int) -> None:
            self.status_code = code

    class FakeSession:
        def __init__(self, code: int) -> None:
            self._code = code
            self.trust_env = False
            self.proxies: dict[str, str] = {}
            self.headers: dict[str, str] = {}

        def get(self, url: str, timeout: int = 0) -> FakeResponse:
            label = self.proxies.get("https") or "直连"
            attempts.append(label)
            if label == "直连":
                return FakeResponse(200)
            raise OSError("连不上")

    def fake_session(proxies: dict[str, str] | None) -> FakeSession:
        session = FakeSession(403)
        if proxies:
            session.proxies.update(proxies)
        return session

    monkeypatch.setattr(pr, "system_proxy", lambda: {"https": "http://127.0.0.1:44444"})
    monkeypatch.setattr(pr, "_session", fake_session)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")

    session, proxies = pr.pick_network("token")

    assert proxies == {}          # 选中了纯直连
    assert session is not None
    assert len(attempts) == 3     # 三种都试过了


def test_pick_network_reports_when_all_fail(monkeypatch):
    """三条路都不通时，错误信息要说清试过什么。"""
    class FakeSession:
        trust_env = False

        def __init__(self) -> None:
            self.proxies: dict[str, str] = {}
            self.headers: dict[str, str] = {}

        def get(self, url: str, timeout: int = 0):
            raise OSError("连不上")

    monkeypatch.setattr(pr, "system_proxy", lambda: {"https": "http://127.0.0.1:44444"})
    monkeypatch.setattr(pr, "_session", lambda proxies=None: FakeSession())
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")

    with pytest.raises(SystemExit) as caught:
        pr.pick_network("token")
    assert "均不通" in str(caught.value)


def test_ping_url_is_the_top_level_endpoint():
    """探测端点不能带仓库路径。

    ``/repos/{owner}/{repo}/rate_limit`` 不存在，会返回 404 —— 而 404 在
    「探测是否连通」的逻辑里看起来和网络不通一模一样，很容易被误判成
    "这台机器连不上 GitHub"。这个坑真实发生过一次。
    """
    assert pr.PING == "https://api.github.com/rate_limit"
    assert "/repos/" not in pr.PING
