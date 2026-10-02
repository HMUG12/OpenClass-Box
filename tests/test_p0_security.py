"""P0 安全修复的回归测试。

覆盖三件事：
  1. **凭据只进 httpOnly cookie**，不再出现在 URL、localStorage 里；
  2. **输出转义**：来自 B 端上报的字段（设备名、分组、审计内容）拼进 HTML 前必须转义；
  3. **公网可达时只保留只读能力**（电源 / 文件下发 / 清理等一律拦下）。

第 1 条用真实 HTTP 请求验证（端到端），第 2、3 条既有行为断言也有源码级断言 ——
注入面这种东西，光看行为测不出来，得盯着"这段文本是不是被包进了 esc(...)"。
"""
from __future__ import annotations

import json
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from backend.core import remote_admin as ra
from backend.core import webconsole as wc


@pytest.fixture()
def console():
    """启动一个真实的手机控制台服务（随机端口），测完关掉。"""
    wc.stop()
    result = wc.start(port=0)
    if not result.get("ok"):
        pytest.skip(f"手机控制台未能启动：{result.get('message')}")
    yield result
    wc.stop()


def _fake_lan() -> types.SimpleNamespace:
    """替身：够 dispatch / summary 用，不做任何真实下发。"""
    return types.SimpleNamespace(
        send=lambda *a, **k: {"ok": True},
        dispatch=lambda *a, **k: {"ok": True},
        nodes=lambda: [],
        running=True,
        nodeCount=0,
        onlineCount=0,
        pairing_code="",
        port=38900,
    )


def _get(url: str, cookie: str | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(url)
    if cookie:
        request.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _post(url: str, payload: dict) -> tuple[int, bytes, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


# ── 1. 凭据只在 cookie 里 ──────────────────────────────────


def test_status_requires_authorization(console):
    base = f"http://127.0.0.1:{console['port']}"
    status, _ = _get(f"{base}/api/status")
    assert status == 401


def test_token_in_url_no_longer_works(console):
    """旧做法是把 token 放在 ?token= 里 —— 现在必须失效。"""
    base = f"http://127.0.0.1:{console['port']}"
    status, _ = _get(f"{base}/api/status?token=forged-token-value")
    assert status == 401, "URL 里的 token 不该再被接受"


def test_login_sets_httponly_cookie(console):
    base = f"http://127.0.0.1:{console['port']}"
    status, _, headers = _post(f"{base}/api/auth", {"code": console["code"]})
    assert status == 200
    cookie = headers.get("Set-Cookie", "")
    assert "oc_token=" in cookie
    # 这两条是这次修复的核心：HttpOnly 让 JS 读不到，SameSite 缓解 CSRF
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie


def test_cookie_grants_access(console):
    base = f"http://127.0.0.1:{console['port']}"
    _, _, headers = _post(f"{base}/api/auth", {"code": console["code"]})
    raw = headers.get("Set-Cookie", "").split(";")[0]
    status, body = _get(f"{base}/api/status", cookie=raw)
    assert status == 200
    assert b"cpu" in body


def test_login_response_does_not_leak_token(console):
    """登录响应里不再返回 token（否则前端又会把它存进 JS 上下文）。"""
    base = f"http://127.0.0.1:{console['port']}"
    _, body, _ = _post(f"{base}/api/auth", {"code": console["code"]})
    assert "token" not in json.loads(body)


def test_console_source_has_no_stored_token():
    src = Path(wc.__file__).read_text(encoding="utf-8")
    assert "localStorage.setItem" not in src
    assert "localStorage.getItem" not in src
    # 注释里会解释"不再接受 ?token="，所以只查真正的请求拼接
    assert "'/api/status?token=" not in src
    assert "'/api/health?token=" not in src


def test_remote_admin_source_has_no_stored_token():
    src = Path(ra.__file__).read_text(encoding="utf-8")
    assert "localStorage.setItem" not in src
    assert "ocb-remote-token" not in src
    assert "'&token='" not in src


def test_security_headers_are_present(console):
    base = f"http://127.0.0.1:{console['port']}"
    with urllib.request.urlopen(urllib.request.Request(f"{base}/"), timeout=5) as resp:
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("Referrer-Policy") == "no-referrer"


# ── 2. 输出转义 ────────────────────────────────────────────


def test_escape_function_defined():
    for module in (wc, ra):
        src = Path(module.__file__).read_text(encoding="utf-8")
        assert "function esc(" in src


def test_escaped_fields_in_remote_page():
    """来自 B 端上报的字段必须包进 esc(...)。

    这是源码级断言：注入面没法靠行为测试覆盖，只能盯着拼接处。
    """
    src = Path(ra.__file__).read_text(encoding="utf-8")
    for fragment in (
        "esc(node.displayName",
        "esc(node.group",
        "esc(node.ip",
        "esc(item.detail)",
        "esc(item.action)",
    ):
        assert fragment in src, f"{fragment} 应当被转义"


def test_escaped_fields_in_console_page():
    src = Path(wc.__file__).read_text(encoding="utf-8")
    for fragment in ("esc(it.title)", "esc(it.summary)", "esc(d.headline", "esc(it.name)"):
        assert fragment in src, f"{fragment} 应当被转义"


# ── 3. 公网模式只读 ────────────────────────────────────────


def test_public_mode_blocks_dangerous_actions(monkeypatch):
    monkeypatch.setattr(ra, "allow_public", lambda: True)
    monkeypatch.setattr(ra, "_lan", _fake_lan)

    for action in ("power", "repair", "cleanup", "kill", "wallpaper", "push_file"):
        result = ra.dispatch(action, ["n1"])
        assert result["ok"] is False, f"{action} 在公网模式下必须被拦"
        assert "只读" in result["message"]


def test_public_mode_allows_readonly_actions(monkeypatch):
    monkeypatch.setattr(ra, "allow_public", lambda: True)
    monkeypatch.setattr(ra, "_lan", _fake_lan)
    for action in ra.PUBLIC_ALLOWED_ACTIONS:
        assert ra.dispatch(action, ["n1"])["ok"] is True, action


def test_public_mode_blocks_file_push(monkeypatch, tmp_path):
    monkeypatch.setattr(ra, "allow_public", lambda: True)
    sample = tmp_path / "note.txt"
    sample.write_text("x", encoding="utf-8")
    result = ra.push_file(["n1"], str(sample))
    assert result["ok"] is False
    assert "只读" in result["message"]


def test_lan_mode_keeps_full_capability(monkeypatch):
    """内网模式下功能不受影响 —— 这是绝大多数用户的使用方式。"""
    monkeypatch.setattr(ra, "allow_public", lambda: False)
    assert ra._public_block("power") is None
    assert ra._public_block("push_file") is None

    monkeypatch.setattr(ra, "_lan", _fake_lan)
    assert ra.dispatch("kill", ["n1"])["ok"] is True


def test_summary_reports_public_mode(monkeypatch):
    """前端要靠这个字段把界面标成只读，否则用户以为按钮坏了。"""
    monkeypatch.setattr(ra, "allow_public", lambda: True)
    monkeypatch.setattr(ra, "_lan", _fake_lan)
    assert ra.summary()["publicMode"] is True