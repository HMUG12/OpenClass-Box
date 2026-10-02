"""
A 端远程管理服务 —— 通过内网穿透在公网访问的完整管理端（Web 版）。

定位（和「手机 Web 控制台」不是一回事）：
  · 手机控制台：面向**本机**，局域网内看这台机器的状态、跑本机体检与修复，仅限局域网来源；
  · 远程管理：面向**整个机房**（A 端视角），设备列表 / 下发指令 / 电源控制 / 消息 /
    文件下发，配合内网穿透可在教室外访问，因此鉴权与审计要求高得多。

安全设计（暴露到公网必须做的六件事）：
  1. **默认关闭**，必须由用户在设置里显式开启；
  2. **访问码强制自设且至少 8 位** —— 不提供默认码，杜绝「忘了改」变成公开后门；
  3. **失败锁定**：同一 IP 连续 5 次输错锁 5 分钟（比局域网版更严）；
  4. **来源限制**：默认只接受私有 / 回环地址；确需公网访问时必须显式打开
     「允许公网来源」，界面上同步给出风险提示；
  5. **危险动作二次确认 + 延迟执行 + 全程审计**：电源类动作必须带确认字段，
     默认延迟 60 秒，给误操作留出撤销窗口；
  6. **凭据只存散列**（PBKDF2-HMAC-SHA256 + 随机盐），token 只在内存、重启即失效。

项目红线：
  ⚠️ 电源类动作**只下发给 B 端设备**，A 端自己永不执行。
  自动化测试与演示可用环境变量 `OPENCLASS_SAFE_TEST=1` 强制「只记录、不下发」。
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .paths import config_dir

try:
    from .config import config
except ImportError:  # pragma: no cover
    config = None

DEFAULT_PORT = 38630
MAX_FAILS = 5
LOCK_SECONDS = 300
SESSION_TTL = 12 * 3600
MIN_CODE_LEN = 8
AUDIT_LIMIT = 300
ITERATIONS = 200_000

# 需要二次确认的动作：值必须原样等于这个字符串才算确认
CONFIRM_TOKEN = "POWER"

# 允许「公网来源访问」时**只保留只读能力**。
#
# 理由不是"怕用户误点"这么轻：这台服务的鉴权强度是**局域网工具级别**的
# （访问码 + 会话过期 + 失败锁定），HTTP 明文、没有 TLS。它一旦被推到公网，
# 就等于把"批量关机""下发文件""清磁盘"这些能力挂在了一个可能被猜到或被撞库的
# 入口上。所以选择：宁可少功能，也不把这份信任押在用户的自觉上。
#
# 只读的两项：体检（读本机数据）与弹消息（不落盘、不改系统状态）。
PUBLIC_ALLOWED_ACTIONS = ("checkup", "message")

# 电源动作白名单（与 net/proto.py 的 power payload.mode 对齐）
POWER_MODES = {
    "shutdown": "关机",
    "restart": "重启",
    "sleep": "睡眠",
    "hibernate": "休眠",
    "cancel": "取消关机",
}

_server: ThreadingHTTPServer | None = None
_port = 0
_tokens: dict[str, float] = {}
_fails: dict[str, list[float]] = {}
_audit: list[dict[str, Any]] = []
_lock = threading.RLock()


# ══════════════════════════════════════════════════════════════
# 凭据与来源
# ══════════════════════════════════════════════════════════════

def _hash(code: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", code.encode("utf-8"), bytes.fromhex(salt), ITERATIONS).hex()


def _credential() -> dict[str, Any]:
    if config is None:
        return {}
    data = config.get("remote_admin_code", {})
    return data if isinstance(data, dict) else {}


def has_code() -> bool:
    data = _credential()
    return bool(data.get("hash") and data.get("salt"))


def set_code(code: str) -> dict[str, Any]:
    """设置访问码（至少 8 位）。远程管理不提供默认码。"""
    code = (code or "").strip()
    if len(code) < MIN_CODE_LEN:
        return {"ok": False, "message": f"访问码至少 {MIN_CODE_LEN} 位（公网可达的服务不能用短码）"}
    if len(code) > 64:
        return {"ok": False, "message": "访问码最多 64 位"}
    salt = secrets.token_hex(16)
    payload = {"salt": salt, "hash": _hash(code, salt), "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S")}
    if config is None or not config.set("remote_admin_code", payload):
        return {"ok": False, "message": "保存失败（数据目录不可写）"}
    with _lock:
        _tokens.clear()          # 改码后所有会话立即失效
    return {"ok": True, "message": "访问码已更新，已登录的设备需要重新登录"}


def allow_public() -> bool:
    if config is None:
        return False
    return bool(config.get("remote_admin_allow_public", False))


def set_allow_public(value: bool) -> dict[str, Any]:
    if config is None or not config.set("remote_admin_allow_public", bool(value)):
        return {"ok": False, "message": "保存失败（数据目录不可写）"}
    return {
        "ok": True,
        "message": "已允许公网来源访问（请务必设置足够长的访问码）" if value else "已恢复为仅内网来源可访问",
    }


def _is_private(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local)


def _source_allowed(host: str) -> bool:
    if _is_private(host):
        return True
    return allow_public()


def _allow_attempt(host: str) -> bool:
    with _lock:
        stamps = _fails.get(host)
        if not stamps:
            return True
        now = time.time()
        recent = [s for s in stamps if now - s < LOCK_SECONDS]
        _fails[host] = recent
        return len(recent) < MAX_FAILS


def _note_fail(host: str) -> None:
    with _lock:
        _fails.setdefault(host, []).append(time.time())


def _new_token() -> str:
    token = secrets.token_urlsafe(24)
    with _lock:
        _tokens[token] = time.time() + SESSION_TTL
    return token


def _authorized(token: str) -> bool:
    if not token:
        return False
    with _lock:
        expire = _tokens.get(token)
        if not expire:
            return False
        if expire < time.time():
            _tokens.pop(token, None)
            return False
        return True


# ══════════════════════════════════════════════════════════════
# 审计与安全测试开关
# ══════════════════════════════════════════════════════════════

def safe_test() -> bool:
    """安全测试模式：电源类动作只记录、不下发。

    开发 / 演示环境用 `OPENCLASS_SAFE_TEST=1` 打开，避免任何误触发。
    """
    return str(os.environ.get("OPENCLASS_SAFE_TEST", "")).strip() in ("1", "true", "yes")


def _note(action: str, detail: str, ok: bool, source: str = "", extra: str = "") -> None:
    item = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "detail": detail,
        "ok": bool(ok),
        "source": source,
        "extra": extra,
    }
    with _lock:
        _audit.append(item)
        if len(_audit) > AUDIT_LIMIT:
            del _audit[: len(_audit) - AUDIT_LIMIT]
    try:
        from .applog import log

        log(f"远程管理：{action}｜{detail}", "INFO" if ok else "WARN", source=source)
    except Exception:
        pass


def audit(n: int = 100) -> list[dict[str, Any]]:
    with _lock:
        return list(_audit[-max(1, min(300, int(n))):])[::-1]


# ══════════════════════════════════════════════════════════════
# 机房数据与指令下发
# ══════════════════════════════════════════════════════════════

def _lan():
    """取局域网服务单例（兼容「包内导入」与「以 backend 为路径导入」两种方式）。"""
    try:
        from ..net.server import server

        return server
    except ImportError:
        from net.server import server

        return server


def _truthy(value: Any) -> bool:
    """兼容属性与方法的取值（LanServer.running 是 property）。"""
    return bool(value() if callable(value) else value)


def summary() -> dict[str, Any]:
    """管理端首页数据：A 端服务状态 + 设备列表 + 统计。"""
    server = _lan()
    nodes = server.nodes()
    online = [n for n in nodes if n.get("online")]
    # B 端上报的内容存在 info 里（cpu / memory / disk / health），而页面按
    # metrics 读 —— 补齐这个别名，否则设备列表的 CPU、内存永远显示 "-"
    enriched = [{**node, "metrics": node.get("info") or {}} for node in nodes]
    return {
        "serverRunning": _truthy(getattr(server, "running", False)),
        "pairingCode": getattr(server, "pairing_code", ""),
        "port": getattr(server, "port", 0),
        "nodes": enriched,
        "counts": {"total": len(nodes), "online": len(online), "offline": len(nodes) - len(online)},
        "powerAllowed": _power_allowed(),
        "powerModes": POWER_MODES,
        "actions": list(_actions()),
        "safeTest": safe_test(),
        # 公网模式下前端要把界面标成"只读"，否则用户会以为按钮坏了
        "publicMode": allow_public(),
        "serverTime": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _actions() -> tuple[str, ...]:
    """支持的远程指令（来自 net/proto.py，避免两边各写一份而跑偏）。"""
    try:
        from ..net.proto import ACTIONS

        return tuple(ACTIONS)
    except ImportError:
        try:
            from net.proto import ACTIONS

            return tuple(ACTIONS)
        except Exception:
            return ()
    except Exception:
        return ()


def _power_allowed() -> bool:
    try:
        from .power import is_allowed

        return _truthy(is_allowed)
    except Exception:
        return False


def _public_block(action: str) -> dict[str, Any] | None:
    """公网可达时拦下非只读动作；不需要拦则返回 None。

    集中在一处，避免"dispatch 拦了、push_file 忘了"这种漏网 —— 后者恰好
    是危害最大的一项（把文件送到任意一台机器上）。
    """
    if not allow_public() or action in PUBLIC_ALLOWED_ACTIONS:
        return None
    return {
        "ok": False,
        "message": (
            "已允许公网访问，此时只能执行只读操作（体检 / 弹消息）。"
            "电源控制、文件下发、磁盘清理等请回到局域网内操作。"
        ),
    }


def dispatch(
    action: str,
    node_ids: list[str],
    payload: dict[str, Any] | None = None,
    confirm: str = "",
    source: str = "",
) -> dict[str, Any]:
    """下发一条指令（电源类需要 confirm + 默认延迟 + 审计）。"""
    action = str(action or "").strip()
    node_ids = [str(n) for n in (node_ids or []) if str(n).strip()]
    payload = dict(payload or {})

    if action not in _actions():
        return {"ok": False, "message": f"不支持的指令：{action}"}
    if not node_ids:
        return {"ok": False, "message": "请先选择要操作的设备"}

    blocked = _public_block(action)
    if blocked:
        _note(action, f"{len(node_ids)} 台", False, source, "公网模式下已拦截")
        return blocked

    if action == "power":
        mode = str(payload.get("mode") or "").lower()
        if mode not in POWER_MODES:
            return {"ok": False, "message": f"不支持的电源操作：{mode or '（空）'}"}
        if str(confirm or "") != CONFIRM_TOKEN:
            return {"ok": False, "message": "电源操作需要二次确认（界面里勾选确认后再提交）"}
        # 安全测试模式：完全不下发，避免在开发 / 演示环境误触真实机器
        if safe_test():
            _note(
                "power（安全测试模式）",
                f"{POWER_MODES[mode]} · {len(node_ids)} 台",
                True,
                source,
                "OPENCLASS_SAFE_TEST=1，未真正下发",
            )
            return {
                "ok": True,
                "message": f"【安全测试模式】已拦截，未真正下发「{POWER_MODES[mode]}」",
                "safeTest": True,
            }
        if mode != "cancel" and not _power_allowed():
            return {
                "ok": False,
                "message": "B 端尚未允许远程电源控制（需要在被控机器的设置里开启）",
            }
        delay = payload.get("delay")
        try:
            payload["delay"] = max(0, min(600, int(delay if delay is not None else 60)))
        except (TypeError, ValueError):
            payload["delay"] = 60

    server = _lan()
    result = server.send(node_ids, action, payload)
    detail = f"{action} → {len(node_ids)} 台"
    if action == "power":
        detail = f"电源·{POWER_MODES.get(str(payload.get('mode')), '')} → {len(node_ids)} 台"
    elif action == "message":
        detail = f"消息「{str(payload.get('text') or '')[:40]}」→ {len(node_ids)} 台"
    _note(action, detail, bool(result.get("ok")), source, str(result.get("message") or "")[:120])
    return result


def push_file(node_ids: list[str], path: str, source: str = "") -> dict[str, Any]:
    """把 A 端本机（或刚上传的临时文件）下发给选中的设备。"""
    target = Path(path)
    if not target.is_file():
        return {"ok": False, "message": "文件不存在"}

    blocked = _public_block("push_file")
    if blocked:
        _note("push_file", target.name, False, source, "公网模式下已拦截")
        return blocked
    server = _lan()
    result = server.push_file([str(n) for n in (node_ids or [])], str(target))
    _note("push_file", f"{target.name} → {len(node_ids or [])} 台", bool(result.get("ok")), source)
    return result


def receive_upload(name: str, data: bytes, source: str = "") -> dict[str, Any]:
    """接收浏览器上传的文件，暂存到数据目录等待下发。"""
    safe_name = Path(str(name or "")).name or f"upload-{int(time.time())}.bin"
    folder = config_dir() / "remote_upload"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / safe_name
    try:
        target.write_bytes(data)
    except OSError as exc:
        return {"ok": False, "message": f"保存失败：{exc}"}
    _note("upload", f"{safe_name}（{len(data) / 1024:.0f} KB）", True, source)
    return {"ok": True, "message": f"已接收 {safe_name}，选择设备后即可下发", "path": str(target)}


# ══════════════════════════════════════════════════════════════
# 服务生命周期
# ══════════════════════════════════════════════════════════════

def _saved_port() -> int:
    if config is None:
        return DEFAULT_PORT
    try:
        return int(config.get("remote_admin_port", DEFAULT_PORT) or DEFAULT_PORT)
    except (TypeError, ValueError):
        return DEFAULT_PORT


def start(port: int = 0, allow_public_source: bool | None = None) -> dict[str, Any]:
    """启动远程管理服务。"""
    global _server, _port

    if not has_code():
        return {"ok": False, "message": "请先设置访问码（至少 8 位）再启动"}

    with _lock:
        if _server is not None:
            return {"ok": True, "message": "远程管理已在运行", "port": _port}

    port = int(port or _saved_port())
    if allow_public_source is not None:
        set_allow_public(bool(allow_public_source))

    handler = _make_handler()
    try:
        httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
    except OSError as exc:
        return {"ok": False, "message": f"端口 {port} 无法监听：{exc}"}

    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True, name="oc-remote-admin")
    thread.start()

    with _lock:
        _server = httpd
        _port = port
    if config is not None:
        config.set("remote_admin_port", port)
    _note("start", f"远程管理服务启动于端口 {port}", True)
    return {"ok": True, "message": f"远程管理已启动（端口 {port}）", "port": port, "url": url()}


def stop() -> dict[str, Any]:
    global _server, _port
    with _lock:
        httpd = _server
        _server = None
        _port = 0
    if httpd is None:
        return {"ok": True, "message": "远程管理未在运行"}
    try:
        httpd.shutdown()
        httpd.server_close()
    except OSError:
        pass
    with _lock:
        _tokens.clear()
    _note("stop", "远程管理服务已停止", True)
    return {"ok": True, "message": "远程管理已停止"}


def running() -> bool:
    with _lock:
        return _server is not None


def local_url(port: int = 0) -> str:
    from .monitor import local_ip

    ip = local_ip() or "127.0.0.1"
    return f"http://{ip}:{int(port or _saved_port())}"


def url() -> str:
    """对外访问地址：优先用穿透给出的公网地址，否则用局域网地址。"""
    try:
        from .tunnel import status as tunnel_status

        public = (tunnel_status() or {}).get("url") or ""
        if public:
            return public
    except Exception:
        pass
    return local_url(_port or _saved_port())


def status() -> dict[str, Any]:
    with _lock:
        httpd = _server
        port = _port or _saved_port()
        sessions = len(_tokens)
    return {
        "running": httpd is not None,
        "port": port,
        "url": url() if httpd is not None else "",
        "localUrl": local_url(port),
        "hasCode": has_code(),
        "allowPublic": allow_public(),
        "minCodeLen": MIN_CODE_LEN,
        "sessions": sessions,
        "safeTest": safe_test(),
        "audit": audit(20),
        "powerAllowed": _power_allowed(),
        "dangerNote": "电源操作只会下发给 B 端设备；A 端自身永不执行",
    }


# ══════════════════════════════════════════════════════════════
# HTTP
# ══════════════════════════════════════════════════════════════

def _page() -> bytes:
    return PAGE.encode("utf-8")


def _make_handler():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "OpenClassBox-Remote/1.0"

        def log_message(self, *_args: Any) -> None:  # 静音默认日志
            pass

        # ── 工具 ──
        def _send(
            self,
            code: int,
            body: bytes,
            content_type: str = "application/json; charset=utf-8",
            headers: list[tuple[str, str]] | None = None,
        ):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            # 这台服务可以被推到公网：禁掉 MIME 嗅断、不外泄 Referer
            # （后者在凭据走 URL 时是实打实的泄露渠道）
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in headers or ():
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

        def _json(self, code: int, data: dict[str, Any], headers: list[tuple[str, str]] | None = None):
            self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), headers=headers)

        def _source(self) -> str:
            return str(self.client_address[0] if self.client_address else "")

        def _query(self) -> dict[str, str]:
            if "?" not in self.path:
                return {}
            raw = self.path.split("?", 1)[1]
            import urllib.parse

            return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

        def _body_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return {}
            if length <= 0 or length > 4 * 1024 * 1024:
                return {}
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return {}
            return data if isinstance(data, dict) else {}

        def _token(self) -> str:
            """访问令牌：Cookie 优先，其次 X-OCB-Token 头；**不再接受 URL 参数**。

            URL 里的凭据会进浏览器历史、Referer 和网关日志 —— 这台服务可以
            被推到公网，所以不能让"钥匙"出现在地址栏里。
            """
            for part in (self.headers.get("Cookie") or "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == "ocb_token" and value:
                    return value
            return str(self.headers.get("X-OCB-Token") or "")

        # ── 路由 ──
        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]

            if path in ("/", "/index.html"):
                if not _source_allowed(self._source()):
                    self._send(403, b"forbidden", "text/plain; charset=utf-8")
                    return
                self._send(200, _page(), "text/html; charset=utf-8")
                return

            if path == "/api/ping":
                self._json(200, {"ok": True, "service": "OpenClass-Box 远程管理", "safeTest": safe_test()})
                return

            if path == "/api/summary":
                if not self._guard():
                    return
                try:
                    self._json(200, {"ok": True, **summary(), "tunnel": self._tunnel_status()})
                except Exception as exc:
                    self._json(500, {"ok": False, "message": f"读取机房状态失败：{exc}"})
                return

            if path == "/api/audit":
                if not self._guard():
                    return
                self._json(200, {"ok": True, "items": audit(120)})
                return

            self._json(404, {"ok": False, "message": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            source = self._source()

            if path == "/api/login":
                self._login(source)
                return

            if not _source_allowed(source):
                self._json(403, {"ok": False, "message": "来源不被允许（未开启公网访问）"})
                return

            if not self._guard():
                return

            if path == "/api/send":
                data = self._body_json()
                result = dispatch(
                    str(data.get("action") or ""),
                    [str(x) for x in (data.get("nodes") or [])],
                    data.get("payload") if isinstance(data.get("payload"), dict) else {},
                    str(data.get("confirm") or ""),
                    source,
                )
                self._json(200 if result.get("ok") else 400, result)
                return

            if path == "/api/upload":
                self._upload(source)
                return

            if path == "/api/pushfile":
                data = self._body_json()
                result = push_file(
                    [str(x) for x in (data.get("nodes") or [])],
                    str(data.get("path") or ""),
                    source,
                )
                self._json(200 if result.get("ok") else 400, result)
                return

            if path == "/api/logout":
                with _lock:
                    _tokens.pop(self._token(), None)
                # 同时把 cookie 清掉：只删服务端会话、却把凭据留在浏览器里没有意义
                self._json(
                    200,
                    {"ok": True, "message": "已退出"},
                    [("Set-Cookie", "ocb_token=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")],
                )
                return

            self._json(404, {"ok": False, "message": "not found"})

        # ── 内部 ──
        def _guard(self) -> bool:
            source = self._source()
            if not _source_allowed(source):
                self._json(403, {"ok": False, "message": "来源不被允许（未开启公网访问）"})
                return False
            if not _authorized(self._token()):
                self._json(401, {"ok": False, "message": "未登录或会话已过期"})
                return False
            return True

        def _login(self, source: str) -> None:
            if not _source_allowed(source):
                self._json(403, {"ok": False, "message": "来源不被允许"})
                return
            data = _credential()
            if not data.get("hash"):
                self._json(400, {"ok": False, "message": "尚未设置访问码"})
                return
            if not _allow_attempt(source):
                self._json(429, {"ok": False, "message": f"输错次数过多，请 {LOCK_SECONDS // 60} 分钟后再试"})
                return
            body = self._body_json()
            code = str(body.get("code") or "")
            if not code:
                self._json(400, {"ok": False, "message": "请输入访问码"})
                return
            if hmac.compare_digest(_hash(code, str(data.get("salt"))), str(data.get("hash"))):
                _fails.pop(source, None)
                token = _new_token()
                _note("login", "管理端登录", True, source)
                # 凭据只进 httpOnly cookie：页面 JS 拿不到，XSS 也偷不走；
                # SameSite=Strict 让它不会随跨站请求发出（缓解 CSRF）
                self._json(
                    200,
                    {"ok": True, "ttl": SESSION_TTL},
                    [("Set-Cookie", f"ocb_token={token}; HttpOnly; SameSite=Strict; Path=/")],
                )
                return
            _note_fail(source)
            _note("login", "访问码错误", False, source)
            self._json(401, {"ok": False, "message": "访问码不正确"})

        def _upload(self, source: str) -> None:
            name = self._query().get("name", "")
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > 512 * 1024 * 1024:
                self._json(400, {"ok": False, "message": "文件大小不合法（上限 512MB）"})
                return
            chunks: list[bytes] = []
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            result = receive_upload(name, b"".join(chunks), source)
            self._json(200 if result.get("ok") else 400, result)

        def _tunnel_status(self) -> dict[str, Any]:
            try:
                from .tunnel import status as tunnel_status

                data = tunnel_status()
                return {"running": data.get("running"), "url": data.get("url"), "provider": data.get("providerName")}
            except Exception:
                return {}

    return Handler


# ══════════════════════════════════════════════════════════════
# 内嵌页面（移动端优先的单页管理端）
# ══════════════════════════════════════════════════════════════

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<title>OpenClass-Box 远程管理</title>
<style>
  :root {
    --bg: #0f1116; --card: #191c24; --line: #262b36; --text: #e8ecf3; --dim: #97a0b3;
    --brand: #4c8dff; --ok: #35c07a; --warn: #ffb02e; --danger: #ff5d5d; --radius: 16px;
  }
  @media (prefers-color-scheme: light) {
    :root { --bg:#f4f6fb; --card:#ffffff; --line:#e3e8f0; --text:#1a1f2b; --dim:#66708a; }
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  body { margin:0; background:var(--bg); color:var(--text); font:15px/1.6 -apple-system,"Segoe UI",system-ui,"Microsoft YaHei",sans-serif; }
  .wrap { max-width:820px; margin:0 auto; padding:16px 14px 48px; }
  h1 { font-size:19px; margin:6px 0 2px; }
  .sub { color:var(--dim); font-size:12.5px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:var(--radius); padding:14px; margin-top:12px; }
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .grow { flex:1; min-width:120px; }
  input, select, textarea, button { font:inherit; color:inherit; }
  input, select, textarea { background:transparent; border:1px solid var(--line); border-radius:10px; padding:9px 11px; width:100%; }
  button { background:var(--brand); color:#fff; border:0; border-radius:10px; padding:10px 14px; font-weight:600; cursor:pointer; }
  button.ghost { background:transparent; border:1px solid var(--line); color:var(--text); font-weight:500; }
  button.danger { background:var(--danger); }
  button:disabled { opacity:.5; cursor:not-allowed; }
  .node { display:flex; gap:10px; align-items:center; padding:10px 0; border-top:1px solid var(--line); }
  .node:first-child { border-top:0; }
  .dot { width:9px; height:9px; border-radius:50%; background:var(--dim); flex:0 0 auto; }
  .dot.on { background:var(--ok); box-shadow:0 0 0 4px rgba(53,192,122,.16); }
  .mono { font-family:ui-monospace,Consolas,monospace; font-size:12.5px; }
  .tag { font-size:11.5px; color:var(--dim); }
  .kv { display:flex; justify-content:space-between; gap:10px; padding:4px 0; font-size:13px; }
  .kv span:last-child { color:var(--dim); text-align:right; word-break:break-all; }
  .statgrid { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; margin-top:6px; }
  .stat { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px; text-align:center; }
  .stat b { display:block; font-size:20px; }
  .stat span { font-size:11.5px; color:var(--dim); }
  .msg { margin-top:10px; font-size:12.5px; padding:8px 10px; border-radius:10px; border:1px solid var(--line); }
  .msg.ok { color:var(--ok); border-color:rgba(53,192,122,.35); }
  .msg.err { color:var(--danger); border-color:rgba(255,93,93,.35); }
  .msg.warn { color:var(--warn); border-color:rgba(255,176,46,.35); }
  details { margin-top:10px; } summary { cursor:pointer; color:var(--dim); font-size:13px; }
  .hide { display:none !important; }
  .log { max-height:260px; overflow:auto; }
  .log div { font-size:12px; padding:6px 0; border-top:1px solid var(--line); }
</style>
</head>
<body>
<div class="wrap">
  <h1>OpenClass-Box 远程管理</h1>
  <div class="sub" id="sub">A 端 · 机房集中管理</div>

  <!-- 登录 -->
  <div class="card" id="loginCard">
    <div class="row"><div class="grow"><input id="code" type="password" placeholder="请输入访问码（至少 8 位）" autocomplete="current-password" /></div>
    <button id="loginBtn">登录</button></div>
    <div class="sub" style="margin-top:8px">访问码在 A 端「设置 → 远程管理」里设置与查看。</div>
    <div class="msg hide" id="loginMsg"></div>
  </div>

  <!-- 主体 -->
  <div id="main" class="hide">
    <div class="statgrid">
      <div class="stat"><b id="sTotal">0</b><span>设备总数</span></div>
      <div class="stat"><b id="sOnline">0</b><span>在线</span></div>
      <div class="stat"><b id="sOffline">0</b><span>离线</span></div>
    </div>

    <div class="card">
      <div class="row"><b class="grow">设备</b><button class="ghost" id="selAll">全选在线</button><button class="ghost" id="refresh">刷新</button></div>
      <div id="nodes"></div>
      <div class="sub" id="pairInfo" style="margin-top:8px"></div>
    </div>

    <div class="card">
      <b>下发指令</b>
      <div class="row" style="margin-top:8px">
        <select id="action" class="grow"></select>
      </div>
      <div class="row" style="margin-top:8px">
        <textarea id="text" class="grow" rows="2" placeholder="消息内容（选择「弹消息」时填写）"></textarea>
      </div>
      <div class="row" style="margin-top:8px">
        <input id="filePath" class="grow" placeholder="要下发的文件路径（A 端本机，可先上传）" />
        <button class="ghost" id="uploadBtn">上传文件</button>
        <input type="file" id="fileInput" class="hide" />
      </div>
      <div class="msg hide" id="actionMsg"></div>
    </div>

    <div class="card">
      <b style="color:var(--danger)">电源控制</b>
      <div class="sub" style="margin-top:4px">
        操作对象是选中的 <b>B 端设备</b>，A 端自身永不执行；默认延迟 60 秒，期间可在被控机上取消。
      </div>
      <div class="row" style="margin-top:8px">
        <select id="powerMode" class="grow"></select>
        <input id="powerDelay" type="number" min="0" max="600" value="60" style="width:96px" />
        <span class="tag">秒后执行</span>
      </div>
      <label class="row" style="margin-top:8px; font-size:13px">
        <input type="checkbox" id="powerConfirm" style="width:auto" />
        我确认这些机器上没有未保存的课件 / 作业
      </label>
      <div class="row" style="margin-top:8px">
        <button class="danger" id="powerBtn">执行电源操作</button>
        <span class="tag" id="powerHint"></span>
      </div>
      <div class="msg hide" id="powerMsg"></div>
    </div>

    <div class="card">
      <div class="row"><b class="grow">操作记录</b><button class="ghost" id="auditBtn">刷新</button></div>
      <div class="log" id="audit"></div>
    </div>

    <div class="card">
      <div class="row"><button class="ghost" id="logout">退出登录</button><span class="tag" id="statusLine"></span></div>
    </div>
  </div>
</div>

<script>
(function () {
  /* 凭据由服务端以 httpOnly cookie 下发：页面 JS 拿不到、也不需要 token ——
     即使页面里出现 XSS，也偷不走凭据（token 放 localStorage 时是可以的）。 */
  function esc(v) {
    return String(v === null || v === undefined ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  var nodes = [];
  var selected = {};

  function q(id) { return document.getElementById(id); }
  function show(el, text, kind) {
    el.className = 'msg ' + (kind || '');
    el.textContent = text;
    el.classList.remove('hide');
  }
  function api(path, options) {
    options = options || {};
    options.headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
    return fetch(path, options).then(function (r) {
      return r.json().then(function (data) { return { status: r.status, data: data }; });
    });
  }

  function login() {
    var code = q('code').value.trim();
    if (!code) { show(q('loginMsg'), '请输入访问码', 'err'); return; }
    api('/api/login', { method: 'POST', body: JSON.stringify({ code: code }) }).then(function (res) {
      if (res.data && res.data.ok) {
        enter();
      } else {
        show(q('loginMsg'), (res.data && res.data.message) || '登录失败', 'err');
      }
    });
  }

  function enter() {
    q('loginCard').classList.add('hide');
    q('main').classList.remove('hide');
    loadSummary();
    loadAudit();
    window.setInterval(loadSummary, 5000);
  }

  function loadSummary() {
    api('/api/summary').then(function (res) {
      if (res.status === 401) { logout(); return; }
      var d = res.data || {};
      if (!d.ok) { return; }
      nodes = d.nodes || [];
      q('sTotal').textContent = d.counts.total;
      q('sOnline').textContent = d.counts.online;
      q('sOffline').textContent = d.counts.offline;
      q('pairInfo').textContent = 'A 端服务：' + (d.serverRunning ? '运行中' : '未启动')
        + (d.pairingCode ? ' · 配对码 ' + d.pairingCode : '')
        + (d.tunnel && d.tunnel.running ? ' · 穿透 ' + (d.tunnel.url || '已连接') : '');
      q('statusLine').textContent =
        (d.safeTest ? '⚠ 安全测试模式：电源操作不会真正下发' : '')
        + (d.publicMode ? ' · 公网访问已开启：当前仅只读（体检 / 弹消息），电源控制与文件下发已锁定'
           : '');
      renderNodes();
      renderActions(d);
      renderPower(d);
    });
  }

  function renderNodes() {
    var box = q('nodes');
    box.innerHTML = '';
    if (!nodes.length) { box.innerHTML = '<div class="sub" style="padding:10px 0">还没有已配对的设备</div>'; return; }
    nodes.forEach(function (node) {
      var row = document.createElement('label');
      row.className = 'node';
      var cb = document.createElement('input');
      cb.type = 'checkbox'; cb.style.width = 'auto';
      cb.checked = !!selected[node.id];
      cb.disabled = !node.online;
      cb.onchange = function () { selected[node.id] = cb.checked; };
      var dot = document.createElement('span');
      dot.className = 'dot' + (node.online ? ' on' : '');
      var info = document.createElement('div');
      info.className = 'grow';
      var metrics = node.metrics || {};
      // 健康摘要由 B 端上报（磁盘 / 内存 / 还原）。有问题直接标出来 ——
      // 电教巡楼时不用逐台点开，扫一眼列表就知道该去哪一间
      var health = metrics.health || {};
      var bad = node.online && health.level && health.level !== 'ok';
      var healthLine = bad
        ? '<div class="tag" style="color:var(--danger)">' + esc(health.headline || health.label || '') + '</div>'
        : '';
      // 设备名 / 分组 / 健康摘要都来自 B 端上报 —— 属于**不可信输入**，
      // 必须转义后再拼进 HTML，否则一台被改过名字的机器就能往管理页里注入脚本
      info.innerHTML = '<div>' + esc(node.displayName || node.name || '未命名')
        + (node.group ? ' <span class="tag">[' + esc(node.group) + ']</span>' : '')
        + (bad ? ' <span class="tag" style="color:var(--danger)">' + esc(health.label || '') + '</span>' : '')
        + '</div>'
        + '<div class="tag mono">' + esc(node.ip || '') + (node.online
          ? ' · CPU ' + (metrics.cpu === undefined ? '-' : metrics.cpu + '%')
            + ' · 内存 ' + (metrics.memory === undefined ? '-' : metrics.memory + '%')
          : ' · 离线') + '</div>'
        + healthLine;
      row.appendChild(cb); row.appendChild(dot); row.appendChild(info);
      box.appendChild(row);
    });
  }

  var actionLabels = {
    checkup: '一键体检', repair: '一键修复', cleanup: '磁盘清理', kill: '结束进程',
    message: '弹消息', push_file: '下发文件', pull_file: '回收文件', wallpaper: '统一换壁纸', power: '电源（下方单独操作）'
  };

  function renderActions(d) {
    var sel = q('action');
    if (sel.options.length) return;
    (d.actions || []).forEach(function (key) {
      if (key === 'power') return;
      var opt = document.createElement('option');
      opt.value = key; opt.textContent = actionLabels[key] || key;
      sel.appendChild(opt);
    });
  }

  function renderPower(d) {
    var sel = q('powerMode');
    if (!sel.options.length) {
      Object.keys(d.powerModes || {}).forEach(function (key) {
        var opt = document.createElement('option');
        opt.value = key; opt.textContent = d.powerModes[key];
        sel.appendChild(opt);
      });
    }
    q('powerHint').textContent = d.powerAllowed ? '' : '（B 端尚未允许电源控制）';
  }

  function selectedNodes() {
    return Object.keys(selected).filter(function (k) { return selected[k]; });
  }

  function send(action, payload, confirm) {
    return api('/api/send', {
      method: 'POST',
      body: JSON.stringify({ action: action, nodes: selectedNodes(), payload: payload || {}, confirm: confirm || '' })
    });
  }

  function loadAudit() {
    api('/api/audit').then(function (res) {
      if (res.status === 401) { logout(); return; }
      var box = q('audit');
      box.innerHTML = '';
      (res.data.items || []).forEach(function (item) {
        var div = document.createElement('div');
        div.innerHTML = '<span class="tag mono">' + esc(item.time) + '</span> '
          + (item.ok ? '✅' : '⚠️') + ' ' + esc(item.action) + ' · ' + esc(item.detail)
          + (item.source ? ' <span class="tag">(' + esc(item.source) + ')</span>' : '');
        box.appendChild(div);
      });
    });
  }

  function logout() {
    q('main').classList.add('hide'); q('loginCard').classList.remove('hide');
  }

  q('loginBtn').onclick = login;
  q('code').onkeydown = function (e) { if (e.key === 'Enter') login(); };
  q('logout').onclick = function () {
    api('/api/logout', { method: 'POST' }).then(logout);
  };
  q('refresh').onclick = loadSummary;
  q('auditBtn').onclick = loadAudit;
  q('selAll').onclick = function () {
    nodes.forEach(function (n) { selected[n.id] = !!n.online; });
    renderNodes();
  };
  q('action').onchange = function () {
    var value = q('action').value;
    q('text').parentElement.classList.toggle('hide', value !== 'message');
    q('filePath').parentElement.classList.toggle('hide', value !== 'push_file');
  };
  q('uploadBtn').onclick = function () { q('fileInput').click(); };
  q('fileInput').onchange = function () {
    var file = q('fileInput').files[0];
    if (!file) return;
    show(q('actionMsg'), '正在上传 ' + file.name + ' …');
    fetch('/api/upload?name=' + encodeURIComponent(file.name),
      { method: 'POST', body: file }).then(function (r) { return r.json(); }).then(function (data) {
        show(q('actionMsg'), data.message || (data.ok ? '上传完成' : '上传失败'), data.ok ? 'ok' : 'err');
        if (data.ok) q('filePath').value = data.path;
      });
  };
  q('powerBtn').onclick = function () {
    var mode = q('powerMode').value;
    if (!q('powerConfirm').checked) { show(q('powerMsg'), '请先勾选确认项', 'warn'); return; }
    if (!selectedNodes().length) { show(q('powerMsg'), '请先选择设备', 'warn'); return; }
    var label = q('powerMode').selectedOptions[0].textContent;
    if (!window.confirm('确定对 ' + selectedNodes().length + ' 台设备执行「' + label + '」吗？')) return;
    send('power', { mode: mode, delay: Number(q('powerDelay').value) || 60 }, 'POWER').then(function (res) {
      show(q('powerMsg'), (res.data && res.data.message) || '已提交', res.data && res.data.ok ? 'ok' : 'err');
      loadAudit();
    });
  };

  document.querySelectorAll('#main button:not(.ghost):not(.danger)').forEach(function () {});
  // 通用指令提交：选择动作后点「下发指令」
  var sendBtn = document.createElement('button');
  sendBtn.textContent = '下发指令';
  sendBtn.style.marginTop = '8px';
  sendBtn.onclick = function () {
    var action = q('action').value;
    var payload = {};
    if (action === 'message') payload.text = q('text').value;
    if (action === 'push_file') payload.path = q('filePath').value;
    if (!selectedNodes().length) { show(q('actionMsg'), '请先选择设备', 'warn'); return; }
    if (action === 'kill' && !payload.pid) { payload.pid = Number(window.prompt('要结束的进程 PID') || 0); }
    if (action === 'push_file' && !payload.path) { show(q('actionMsg'), '请先上传或填写文件路径', 'warn'); return; }
    send(action, payload).then(function (res) {
      show(q('actionMsg'), (res.data && res.data.message) || '已提交', res.data && res.data.ok ? 'ok' : 'err');
      loadAudit();
    });
  };
  q('action').parentElement.parentElement.appendChild(sendBtn);

  /* 不再靠本地 token 判断登录态：直接问服务端一次 —— cookie 在就进主界面。
   这也是"凭据该不该存在 JS 里"的一个自检：页面完全不需要知道它。 */
  api('/api/summary').then(function (res) {
    if (res.status === 401) { q('loginCard').classList.remove('hide'); } else { enter(); }
  });
})();
</script>
</body>
</html>
"""
