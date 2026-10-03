"""文件传输并入手机控制台后的端到端测试（真实 HTTP，非 mock）。"""

import http.client
import json
import socket
import sys
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core import chat as chat_mod
from backend.core import webconsole


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _request(port, method, path, *, body=None, cookie="", ctype="application/json"):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    headers: dict[str, str] = {}
    payload = None
    if body is not None:
        payload = body.encode("utf-8") if isinstance(body, str) else bytes(body)
        headers["Content-Type"] = ctype
        headers["Content-Length"] = str(len(payload))
    if cookie:
        headers["Cookie"] = cookie
    connection.request(method, path, body=payload, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    set_cookie = response.getheader("Set-Cookie") or ""
    connection.close()
    try:
        return response.status, json.loads(raw.decode("utf-8", "ignore")), set_cookie
    except ValueError:
        return response.status, {}, set_cookie


@pytest.fixture
def console():
    """起一个真实的手机控制台（随机端口），结束后停掉并清理。"""
    port = _free_port()
    original = webconsole.DEFAULT_PORT
    webconsole.DEFAULT_PORT = port
    assert webconsole.start(port).get("ok")
    session = chat_mod.chat
    session.set_accepting(False)
    try:
        yield port, webconsole.status().get("code") or webconsole.regenerate()
    finally:
        webconsole.stop()
        webconsole.DEFAULT_PORT = original
        session.set_accepting(False)
        with session._lock:
            session.members.clear()
            session.messages.clear()
            session._files.clear()
        try:
            chat_mod.clear_received()
        except Exception:
            pass


def _login(port, code):
    status, _data, raw = _request(port, "POST", "/api/auth", body=json.dumps({"code": code}))
    assert status == 200, f"登录失败 HTTP {status}"
    return raw.split(";")[0]


# ── 认证：一个访问码就够 ─────────────────────────────────


def test_transfer_requires_login(console):
    port, _code = console
    status, _data, _ = _request(port, "GET", "/api/transfer/state")
    assert status == 401


def test_wrong_access_code_is_rejected(console):
    port, _code = console
    status, _data, _ = _request(port, "POST", "/api/auth", body=json.dumps({"code": "000000"}))
    assert status == 401


def test_access_code_also_grants_transfer(console):
    """一个码通行：能管这台机器的人就能往它传文件（这是合并的既定代价）。"""
    port, code = console
    cookie = _login(port, code)
    status, data, _ = _request(port, "GET", "/api/transfer/state", cookie=cookie)
    assert status == 200 and data.get("ok")


# ── 开关与上传 ───────────────────────────────────────────


def test_upload_works_and_file_lands(console):
    port, code = console
    cookie = _login(port, code)
    assert _request(port, "POST", "/api/transfer/open", cookie=cookie)[0] == 200

    status, data, _ = _request(
        port, "POST", "/api/transfer/upload?name=%E6%B5%8B%E8%AF%95.txt",
        body="内容" * 500, cookie=cookie, ctype="application/octet-stream",
    )
    assert status == 200 and data.get("ok"), data
    assert data["file"]["name"] == "测试.txt"

    status, state, _ = _request(port, "GET", "/api/transfer/state", cookie=cookie)
    names = [one["name"] for one in state.get("files") or []]
    assert names == ["测试.txt"]


def test_query_string_does_not_break_routing(console):
    """`self.path` 是带 query 的原始请求行（…/upload?name=a.txt）。

    不先 urlparse 就拿去和路径字面量比，永远不相等 —— 请求会一路落回
    宿主的 404，而且越查越莫名其妙。这条就是防这个。
    """
    port, code = console
    cookie = _login(port, code)
    _request(port, "POST", "/api/transfer/open", cookie=cookie)
    status, data, _ = _request(
        port, "POST", "/api/transfer/upload?name=%E6%96%87%E4%BB%B6.txt",
        body="x" * 100, cookie=cookie, ctype="application/octet-stream",
    )
    assert status == 200, f"带 query 的上传不该 404：HTTP {status} {data}"


def test_upload_rejected_when_not_accepting(console):
    """没开启接收就说清楚是"对方没开"，而不是含糊的网络错误。"""
    port, code = console
    cookie = _login(port, code)
    status, data, _ = _request(
        port, "POST", "/api/transfer/upload?name=a.txt",
        body="x", cookie=cookie, ctype="application/octet-stream",
    )
    assert status == 403
    assert "还没有开启接收" in str(data.get("message"))


def test_empty_file_is_rejected(console):
    port, code = console
    cookie = _login(port, code)
    _request(port, "POST", "/api/transfer/open", cookie=cookie)
    status, data, _ = _request(
        port, "POST", "/api/transfer/upload?name=%E7%A9%BA.txt",
        body=b"", cookie=cookie, ctype="application/octet-stream",
    )
    assert status == 400
    assert "为空" in str(data.get("message"))


def test_turning_off_stops_receiving(console):
    port, code = console
    cookie = _login(port, code)
    _request(port, "POST", "/api/transfer/open", cookie=cookie)
    assert _request(port, "POST", "/api/transfer/close", cookie=cookie)[0] == 200
    status, _data, _ = _request(
        port, "POST", "/api/transfer/upload?name=b.txt",
        body="y", cookie=cookie, ctype="application/octet-stream",
    )
    assert status == 403


# ── 电脑 ↔ 电脑：用访问码换传输令牌 ───────────────────────


def test_join_with_wrong_code_is_rejected_with_401(console):
    """状态码必须跟结果一致。

    访问码不对却回 200，只看状态码的客户端会以为"连上了"，然后在后面
    某处莫名其妙失败 —— 这种错最难查。
    """
    port, _code = console
    status, data, _ = _request(
        port, "POST", "/api/transfer/join", body=json.dumps({"code": "000000"})
    )
    assert status == 401, f"应返回 401，实际 {status}"
    assert "访问码" in str(data.get("message"))


def test_pc_to_pc_transfer(console):
    """电脑 ↔ 电脑：换令牌 → 传文件 → 对方收得到。"""
    port, code = console
    cookie = _login(port, code)
    _request(port, "POST", "/api/transfer/open", cookie=cookie)

    status, data, _ = _request(
        port, "POST", "/api/transfer/join",
        body=json.dumps({"code": code, "nickname": "虚拟机"}),
    )
    assert status == 200 and data.get("token"), data

    token = data["token"]
    status, data, _ = _request(
        port, f"POST", f"/api/transfer/upload?name=%E4%BB%8E-PC.txt&token={token}",
        body="PC to PC", ctype="application/octet-stream",
    )
    assert status == 200 and data.get("ok"), data

    status, state, _ = _request(port, "GET", "/api/transfer/state", cookie=cookie)
    names = [one["name"] for one in state.get("files") or []]
    assert names == ["从-PC.txt"]


def test_token_cannot_upload_when_host_not_accepting(console):
    """令牌是"能连上"，不等于"对方在收" —— 后者由开关决定。"""
    port, code = console
    status, data, _ = _request(
        port, "POST", "/api/transfer/join",
        body=json.dumps({"code": code, "nickname": "虚拟机"}),
    )
    assert status == 200
    token = data["token"]
    status, data, _ = _request(
        port, f"POST", f"/api/transfer/upload?name=x.txt&token={token}",
        body="x", ctype="application/octet-stream",
    )
    assert status == 403
    assert "还没有开启接收" in str(data.get("message"))


def test_bogus_token_is_rejected(console):
    port, code = console
    cookie = _login(port, code)
    _request(port, "POST", "/api/transfer/open", cookie=cookie)
    # 令牌只能放 ASCII：它走 URL 查询参数，而 http.client 不接受非 ASCII 路径
    # （这条测的是"令牌不对被拒"，编码本身由 join 那两条覆盖）
    status, _data, _ = _request(
        port, "POST", "/api/transfer/upload?name=a.txt&token=not-a-real-token",
        body="x", ctype="application/octet-stream",
    )
    assert status == 401


# ── 只有本机能开关接收 ───────────────────────────────────


class _FakeHandler:
    """最小 handler 替身：_transfer_is_local 只看 client_address。"""

    def __init__(self, ip: str) -> None:
        self.client_address = (ip, 50000)


def test_localhost_counts_as_local():
    assert webconsole._transfer_is_local(_FakeHandler("127.0.0.1")) is True


def test_any_of_own_ips_counts_as_local():
    """本机可能不止一个地址（这台机器就有 .103 和 .105 两个）。

    只跟 local_ip() 那一个比的话，从另一个地址访问自己会被当成
    "局域网里的其他设备"，本机想开接收都被拒。
    """
    own = sorted(x for x in webconsole._local_ipv4() if not x.startswith("127."))
    assert own, "至少应能列出一个本机 IPv4"
    for ip in own[:3]:
        assert webconsole._transfer_is_local(_FakeHandler(ip)) is True, ip


def test_other_device_on_lan_is_not_local():
    """局域网里的另一台设备不能替这台机器开关接收。"""
    own = webconsole._local_ipv4()
    for candidate in ("192.0.2.77", "198.51.100.13", "10.99.99.99"):
        if candidate not in own:
            assert webconsole._transfer_is_local(_FakeHandler(candidate)) is False
            return
    pytest.skip("候选地址恰好都在本机上，换个环境再验")
    port, code = console
    cookie = _login(port, code)
    _request(port, "POST", "/api/transfer/open", cookie=cookie)
    # 令牌只能放 ASCII：它走 URL 查询参数，而 http.client 不接受非 ASCII 路径
    # （这条测的是"令牌不对被拒"，编码本身由 join 那两条覆盖）
    status, _data, _ = _request(
        port, "POST", "/api/transfer/upload?name=a.txt&token=not-a-real-token",
        body="x", ctype="application/octet-stream",
    )
    assert status == 401
