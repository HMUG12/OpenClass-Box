"""
临时聊天传输 —— 局域网里「用完即走」的聊天与文件互传。

定位（和「机房管理」刻意分开）：
  · 机房管理是「教师机 → 学生机」的**管控通道**，需要配对与授权；
  · 这里是**平等的临时会话**：几位老师/电教在同一个 WiFi 下，输入同一个
    房间码就能聊天、互传课件，关掉程序房间就消失，不依赖任何服务器。

设计取舍：
  1. **去中心化**：谁开房间谁当主机（承担消息中转与文件中转），
     其他成员直连它 —— 局域网内零配置，不联网也能用；
  2. **临时性**：聊天记录只在内存里，程序一关就没了；收到的文件放在
     `data/chat/` 下，可一键清空；
  3. **安全**：只接受局域网来源；房间码 + 可选密码；文件名强制清洗；
     单文件大小与房间总占用都有上限，避免被塞爆。

⚠️ 这是**明文**局域网通信（与教室内网场景匹配），不要用来传敏感信息。
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .paths import config_dir

DEFAULT_PORT = 38620
MAX_FILE = 200 * 1024 * 1024        # 单文件上限 200 MB
ROOM_QUOTA = 1024 * 1024 * 1024     # 房间文件总量上限 1 GB
MAX_TEXT = 2000                     # 单条消息字数
MAX_MESSAGES = 2000                 # 内存里保留的消息条数
POLL_INTERVAL = 2.0                 # 成员端拉取间隔（秒）


def _is_local(host: str) -> bool:
    import ipaddress

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local)


def _clean_name(name: str) -> str:
    """文件名清洗：去掉路径与非法字符，防止写穿目录。"""
    base = Path(str(name or "file.bin")).name
    safe = "".join(ch for ch in base if ch not in '<>:"/\\|?*').strip()
    return (safe or "file.bin")[:120]


def _room_dir(code: str) -> Path:
    folder = config_dir() / "chat" / code
    folder.mkdir(parents=True, exist_ok=True)
    return folder


class ChatSession:
    """一次临时会话（同一时刻只会有一个：要么自己当主机，要么加入别人）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.role = ""              # "" | "host" | "member"
        self.room_code = ""
        self.room_name = ""
        self.nickname = ""
        self.password = ""
        self.token = ""
        self.host_url = ""
        self.members: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self._seq = 0
        self._files: dict[str, Path] = {}
        self._httpd: ThreadingHTTPServer | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error = ""

    # ── 公共状态（前端只读这个）────────────────────────

    def state(self) -> dict[str, Any]:
        from .monitor import local_ip

        with self._lock:
            return {
                "active": bool(self.role),
                "role": self.role,
                "roomCode": self.room_code,
                "roomName": self.room_name,
                "nickname": self.nickname,
                "hostUrl": self.host_url,
                "myIp": local_ip() or "",
                "port": DEFAULT_PORT,
                "members": list(self.members),
                "messages": list(self.messages[-300:]),
                "receivedDir": str(self._received_dir()) if self.role else "",
                "error": self._error,
            }

    def _received_dir(self) -> Path:
        return _room_dir(self.room_code or "local")

    # ── 内部：消息 ────────────────────────────────────

    def _add(self, **message: Any) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            item = {"id": self._seq, "ts": int(time.time() * 1000), **message}
            self.messages.append(item)
            if len(self.messages) > MAX_MESSAGES:
                del self.messages[:-MAX_MESSAGES]
            return item

    def _system(self, text: str) -> None:
        self._add(kind="system", sender="", text=text)

    # ── 主机：开房间 ──────────────────────────────────

    def host_room(self, nickname: str, room_name: str = "", password: str = "") -> dict[str, Any]:
        if self.role:
            return {"ok": False, "message": "已经在一个房间里了，先退出再开新房间"}

        self.role = "host"
        self.nickname = (nickname or "我")[:24]
        self.room_name = (room_name or "")[:40]
        self.password = password or ""
        self.room_code = f"{secrets.randbelow(1000000):06d}"
        self.token = secrets.token_urlsafe(16)
        from .monitor import local_ip

        ip = local_ip() or "127.0.0.1"
        self.host_url = f"http://{ip}:{DEFAULT_PORT}"

        handler = _make_handler(self)
        httpd: ThreadingHTTPServer | None = None
        try:
            httpd = ThreadingHTTPServer(("0.0.0.0", DEFAULT_PORT), handler)
        except OSError:
            try:
                httpd = ThreadingHTTPServer(("0.0.0.0", 0), handler)
            except OSError as exc:
                self.role = ""
                return {"ok": False, "message": f"端口无法监听：{exc}"}
        httpd.daemon_threads = True
        self._httpd = httpd
        port = httpd.server_address[1]
        self.host_url = f"http://{ip}:{port}"

        threading.Thread(target=httpd.serve_forever, daemon=True, name="oc-chat-host").start()
        self.members = [
            {"id": self.token[:8], "nickname": self.nickname, "host": True, "ip": ip}
        ]
        self._system(f"{self.nickname} 创建了房间 {self.room_code}")
        return {
            "ok": True,
            "message": f"房间已创建：{self.room_code}（把房间码告诉同事即可加入）",
            "roomCode": self.room_code,
            "url": self.host_url,
        }

    # ── 成员：加入房间 ────────────────────────────────

    def join_room(
        self, host: str, code: str, nickname: str, password: str = ""
    ) -> dict[str, Any]:
        if self.role:
            return {"ok": False, "message": "已经在一个房间里了，先退出再加入"}

        code = (code or "").strip()
        if not code:
            return {"ok": False, "message": "请填写房间码"}
        base = (host or "").strip().rstrip("/")
        if not base.startswith("http"):
            ip = base.split(":")[0] or "127.0.0.1"
            port = base.split(":")[1] if ":" in base else str(DEFAULT_PORT)
            base = f"http://{ip}:{port}"

        payload = {"code": code, "nickname": (nickname or "同事")[:24], "password": password}
        try:
            response = _post(f"{base}/api/join", payload, timeout=8)
        except Exception as exc:
            return {"ok": False, "message": f"加入失败（检查房间码与网络）：{exc}"}
        if not response.get("ok"):
            return {"ok": False, "message": str(response.get("message") or "加入失败")}

        self.role = "member"
        self.room_code = str(response.get("roomCode") or code)
        self.room_name = str(response.get("roomName") or "")
        self.nickname = (nickname or "同事")[:24]
        self.host_url = base
        self.token = str(response.get("token") or "")
        self._last_id = 0

        self._stop.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="oc-chat-poll")
        self._thread.start()
        return {"ok": True, "message": f"已加入房间 {self.room_code}", "roomCode": self.room_code}

    def _poll_loop(self) -> None:
        while not self._stop.wait(POLL_INTERVAL):
            try:
                data = _post(
                    f"{self.host_url}/api/messages",
                    {"token": self.token, "since": self._last_id},
                    timeout=8,
                )
            except Exception as exc:
                self._error = f"与主机断开：{exc}"
                continue
            self._error = ""
            with self._lock:
                self.members = list(data.get("members") or [])
                for item in data.get("messages") or []:
                    if isinstance(item, dict) and item.get("id") not in [m.get("id") for m in self.messages]:
                        self.messages.append(item)
                        self._last_id = max(self._last_id, int(item.get("id") or 0))
                if len(self.messages) > MAX_MESSAGES:
                    del self.messages[:-MAX_MESSAGES]

    # ── 发送 ──────────────────────────────────────────

    def send_text(self, text: str) -> dict[str, Any]:
        text = (text or "").strip()[:MAX_TEXT]
        if not text:
            return {"ok": False, "message": "消息不能为空"}
        if not self.role:
            return {"ok": False, "message": "还没有加入房间"}

        if self.role == "host":
            self._add(kind="text", sender=self.nickname, text=text)
            return {"ok": True, "message": ""}
        try:
            _post(f"{self.host_url}/api/send", {"token": self.token, "text": text}, timeout=8)
            return {"ok": True, "message": ""}
        except Exception as exc:
            return {"ok": False, "message": f"发送失败：{exc}"}

    def send_file(self, path: str) -> dict[str, Any]:
        if not self.role:
            return {"ok": False, "message": "还没有加入房间"}
        source = Path((path or "").strip().strip('"'))
        if not source.is_file():
            return {"ok": False, "message": "文件不存在"}
        size = source.stat().st_size
        if size > MAX_FILE:
            return {"ok": False, "message": f"文件超过 {MAX_FILE // 1048576} MB 上限"}

        name = _clean_name(source.name)

        if self.role == "host":
            target = _room_dir(self.room_code) / f"{int(time.time())}_{name}"
            try:
                shutil.copy2(source, target)
            except OSError as exc:
                return {"ok": False, "message": f"保存失败：{exc}"}
            file_id = secrets.token_urlsafe(10)
            with self._lock:
                self._files[file_id] = target
            self._add(
                kind="file",
                sender=self.nickname,
                text="",
                file={"id": file_id, "name": name, "size": size, "local": str(target)},
            )
            return {"ok": True, "message": f"已发送 {name}（{size / 1048576:.1f} MB）"}

        try:
            with source.open("rb") as handle:
                raw = handle.read()
            url = f"{self.host_url}/api/upload?token={urllib.parse.quote(self.token)}&name={urllib.parse.quote(name)}"
            request = urllib.request.Request(
                url, data=raw, method="POST", headers={"Content-Type": "application/octet-stream"}
            )
            with urllib.request.urlopen(request, timeout=300) as response:
                data = json.loads(response.read().decode("utf-8", "ignore"))
        except Exception as exc:
            return {"ok": False, "message": f"发送失败：{exc}"}
        if not data.get("ok"):
            return {"ok": False, "message": str(data.get("message") or "主机拒绝了文件")}
        return {"ok": True, "message": f"已发送 {name}（{size / 1048576:.1f} MB）"}

    # ── 退出 ──────────────────────────────────────────

    def leave(self, clear_files: bool = False) -> dict[str, Any]:
        was_host = self.role == "host"
        code = self.room_code
        if self.role == "member" and self.token:
            try:
                _post(f"{self.host_url}/api/leave", {"token": self.token}, timeout=4)
            except Exception:
                pass

        self._stop.set()
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
        self._httpd = None
        self._thread = None

        with self._lock:
            self.role = ""
            self.room_code = ""
            self.room_name = ""
            self.token = ""
            self.host_url = ""
            self.members = []
            self.messages = []
            self._files = {}
            self._seq = 0
            self._error = ""

        removed = ""
        if was_host and code:
            folder = _room_dir(code)
            if clear_files and folder.exists():
                shutil.rmtree(folder, ignore_errors=True)
                removed = str(folder)
        return {"ok": True, "message": "已退出房间（聊天记录已清空）", "removed": removed}

    # ── 收取别人发来的文件（成员端）────────────────────

    def save_file_from(self, file_id: str, name: str) -> dict[str, Any]:
        """把主机上的文件下载到本机 data/chat。"""
        if self.role != "member":
            return {"ok": False, "message": "只有成员需要下载"}
        target = _room_dir(self.room_code) / _clean_name(name)
        url = f"{self.host_url}/api/file/{urllib.parse.quote(file_id)}?token={urllib.parse.quote(self.token)}"
        try:
            with urllib.request.urlopen(url, timeout=300) as response, target.open("wb") as out:
                shutil.copyfileobj(response, out, 256 * 1024)
        except Exception as exc:
            return {"ok": False, "message": f"下载失败：{exc}"}
        return {"ok": True, "message": f"已保存到 {target}", "path": str(target)}


# ══════════════════════════════════════════════════════════════
# 主机端 HTTP
# ══════════════════════════════════════════════════════════════

def _post(url: str, payload: dict[str, Any], timeout: float = 8) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "ignore"))


def _make_handler(session: ChatSession):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "OpenClassBox-Chat/1.0"

        def log_message(self, *_args: Any) -> None:
            pass

        # ── 基础 ──────────────────────────────────────

        def _json(self, data: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self, limit: int = 8 * 1024 * 1024) -> Any:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > limit:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except ValueError:
                return {}

        def _guard(self) -> bool:
            if _is_local(self.client_address[0]):
                return True
            self._json({"ok": False, "message": "仅允许局域网访问"}, 403)
            return False

        def _auth(self, token: str) -> dict[str, Any] | None:
            with session._lock:
                for member in session.members:
                    if member.get("id") == token[:8]:
                        return member
            return None

        # ── 路由 ──────────────────────────────────────

        def do_POST(self) -> None:  # noqa: N802
            if not self._guard():
                return
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)

            if parsed.path == "/api/join":
                data = self._body()
                if str(data.get("code") or "") != session.room_code:
                    self._json({"ok": False, "message": "房间码不正确"}, 403)
                    return
                if session.password and str(data.get("password") or "") != session.password:
                    self._json({"ok": False, "message": "房间密码不正确"}, 403)
                    return
                token = secrets.token_urlsafe(16)
                member = {
                    "id": token[:8],
                    "nickname": str(data.get("nickname") or "同事")[:24],
                    "host": False,
                    "ip": self.client_address[0],
                }
                with session._lock:
                    session.members = [m for m in session.members if m.get("id") != member["id"]]
                    session.members.append(member)
                session._system(f"{member['nickname']} 加入了房间")
                self._json(
                    {
                        "ok": True,
                        "token": token,
                        "roomCode": session.room_code,
                        "roomName": session.room_name,
                    }
                )
                return

            if parsed.path == "/api/send":
                data = self._body()
                member = self._auth(str(data.get("token") or ""))
                if member is None:
                    self._json({"ok": False, "message": "未授权"}, 401)
                    return
                text = str(data.get("text") or "").strip()[:MAX_TEXT]
                if text:
                    session._add(kind="text", sender=member["nickname"], text=text)
                self._json({"ok": True})
                return

            if parsed.path == "/api/messages":
                data = self._body()
                member = self._auth(str(data.get("token") or ""))
                if member is None:
                    self._json({"ok": False, "message": "未授权"}, 401)
                    return
                try:
                    since = int(data.get("since") or 0)
                except (TypeError, ValueError):
                    since = 0
                with session._lock:
                    fresh = [m for m in session.messages if int(m.get("id") or 0) > since]
                    members = list(session.members)
                self._json({"ok": True, "messages": fresh, "members": members})
                return

            if parsed.path == "/api/leave":
                data = self._body()
                member = self._auth(str(data.get("token") or ""))
                if member:
                    with session._lock:
                        session.members = [m for m in session.members if m.get("id") != member["id"]]
                    session._system(f"{member['nickname']} 离开了房间")
                self._json({"ok": True})
                return

            if parsed.path == "/api/upload":
                token = (query.get("token") or [""])[0]
                member = self._auth(token)
                if member is None:
                    self._json({"ok": False, "message": "未授权"}, 401)
                    return
                name = _clean_name((query.get("name") or ["file.bin"])[0])
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                if length <= 0 or length > MAX_FILE:
                    self._json({"ok": False, "message": "文件为空或超过大小上限"}, 413)
                    return
                folder = _room_dir(session.room_code)
                used = sum(f.stat().st_size for f in folder.glob("*") if f.is_file())
                if used + length > ROOM_QUOTA:
                    self._json({"ok": False, "message": "房间文件空间已满，请先清理"}, 507)
                    return
                target = folder / f"{int(time.time())}_{name}"
                remaining = length
                try:
                    with target.open("wb") as out:
                        while remaining > 0:
                            chunk = self.rfile.read(min(256 * 1024, remaining))
                            if not chunk:
                                break
                            out.write(chunk)
                            remaining -= len(chunk)
                except OSError as exc:
                    self._json({"ok": False, "message": f"写入失败：{exc}"}, 500)
                    return

                file_id = secrets.token_urlsafe(10)
                with session._lock:
                    session._files[file_id] = target
                session._add(
                    kind="file",
                    sender=member["nickname"],
                    text="",
                    file={"id": file_id, "name": name, "size": length},
                )
                self._json({"ok": True, "message": "已接收"})
                return

            self._json({"ok": False, "message": "未找到"}, 404)

        def do_GET(self) -> None:  # noqa: N802
            if not self._guard():
                return
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)

            if parsed.path.startswith("/api/file/"):
                token = (query.get("token") or [""])[0]
                if self._auth(token) is None:
                    self._json({"ok": False, "message": "未授权"}, 401)
                    return
                file_id = parsed.path.rsplit("/", 1)[-1]
                with session._lock:
                    path = session._files.get(file_id)
                if path is None or not path.is_file():
                    self._json({"ok": False, "message": "文件不存在"}, 404)
                    return
                try:
                    size = path.stat().st_size
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(size))
                    self.send_header(
                        "Content-Disposition",
                        f"attachment; filename*=UTF-8''{urllib.parse.quote(path.name)}",
                    )
                    self.end_headers()
                    with path.open("rb") as handle:
                        shutil.copyfileobj(handle, self.wfile, 256 * 1024)
                except OSError:
                    pass
                return

            if parsed.path == "/api/ping":
                self._json({"ok": True, "room": session.room_code, "name": session.room_name})
                return

            self._json({"ok": False, "message": "未找到"}, 404)

    return Handler


# ══════════════════════════════════════════════════════════════
# 全局单例 + 对外函数
# ══════════════════════════════════════════════════════════════

chat = ChatSession()


def state() -> dict[str, Any]:
    return chat.state()


def host_room(nickname: str = "我", room_name: str = "", password: str = "") -> dict[str, Any]:
    return chat.host_room(nickname, room_name, password)


def join_room(host: str, code: str, nickname: str = "同事", password: str = "") -> dict[str, Any]:
    return chat.join_room(host, code, nickname, password)


def send_text(text: str) -> dict[str, Any]:
    return chat.send_text(text)


def send_file(path: str) -> dict[str, Any]:
    return chat.send_file(path)


def leave(clear_files: bool = False) -> dict[str, Any]:
    return chat.leave(clear_files)


def save_file(file_id: str, name: str) -> dict[str, Any]:
    return chat.save_file_from(file_id, name)


def clear_received() -> dict[str, Any]:
    """清空临时聊天收到/暂存的文件。"""
    folder = config_dir() / "chat"
    if not folder.exists():
        return {"ok": True, "message": "没有需要清理的文件"}
    removed = 0
    total = 0
    for path in folder.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
                path.unlink()
                removed += 1
            except OSError:
                continue
    return {
        "ok": True,
        "message": f"已清理 {removed} 个文件（{total / 1048576:.1f} MB）" if removed else "没有需要清理的文件",
    }
