"""
局域网临时文件传输 —— 手机、电脑在同一 WiFi 下互传文件。

定位（2026-10 调整）：
  原本叫「临时聊天传输」，是独立的一个服务（端口 38620）+ 独立的一套认证
  （房间码 + 密码）。现在**并入手机控制台**：同一个端口（38610）、同一个
  访问码，手机打开控制台就能传文件，电脑之间互传也走同一条路。

  调整的理由：两个功能都是"同一局域网内用手机/电脑做一件事"，却要记两个码、
  开两个页面，老师和电教记不住。而它们的价值本来就不对等 —— 「巡课看状态」
  和「传个课件」在同一个场景里几乎总是一起来。

与「机房管理」的边界（刻意保持不同）：
  · 机房管理是「教师机 → 学生机」的**管控通道**，需要配对与授权；
  · 这里是**平等的临时传输**：谁开接收谁就是主机，其他设备直连它传文件，
    关掉程序就结束，不依赖任何服务器。

设计取舍：
  1. **一个凭证**：访问码即传输凭证。原先"访问码 + 房间码"两个码合并成一个
     ——代价是**权限统一**（能管这台机器的人就能往它传文件），这是有意接受的。
  2. **临时性**：收到的文件放在 `data/transfer/` 下，可一键清空。
     （存储路径历史上叫 `chat`，改名会导致已有文件"消失"，故保留不动。）
  3. **安全**：只接受局域网来源；文件名强制清洗；单文件与总占用都有上限。
  4. **聊天已从界面移除**，但后端数据结构与接口**原样保留**（`send_text` 等），
     以后想恢复只要把界面加回来。

⚠️ 这是**明文**局域网传输（与教室内网场景匹配），不要用来传敏感信息。
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
ROOM_QUOTA = 1024 * 1024 * 1024     # 传输总量上限 1 GB
MAX_TEXT = 2000                     # 单条消息字数（聊天已下线，保留以备恢复）
MAX_MESSAGES = 2000                 # 内存里保留的消息条数
POLL_INTERVAL = 2.0                 # 成员端拉取间隔（秒）

# 传输路由前缀。与手机控制台共用一个 HTTP 服务，路径必须带前缀避免撞车。
TRANSFER_PREFIX = "/api/transfer"


def _console_port() -> int:
    """手机控制台的端口（传输已并入它）。

    延迟导入而不是写死数字：webconsole 会 import 本模块拿路由函数，
    模块级再 import 回去就成环了。端口号保持单一来源 —— 将来控制台换端口，
    这里自动跟着变。
    """
    from .webconsole import DEFAULT_PORT

    return int(DEFAULT_PORT)


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
    """传输落盘目录。

    目录名历史上是 ``chat``（那时还带聊天）。改名会让已收到的文件看起来
    "凭空消失"，而这里的收益只是命名好看 —— 不值得，所以保留原路径。
    """
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
        # 传输开关：传输已并入手机控制台，"开房间"这个动作拆成两件事 ——
        # 访问码解决"你是谁"，本开关解决"我现在愿不愿意收文件"。
        # 两者都通过才放行上传。
        self._accepting = False
        # 成员端视角：对方（主机）当前是否开着接收。由轮询刷新，
        # 让"对方没开"这件事立刻可见，而不是让人对着进度条干等。
        self._remote_accepting = False

    @property
    def remote_accepting(self) -> bool:
        """加入方看到的"对方是否开着接收"。"""
        with self._lock:
            return self._remote_accepting

    # ── 传输开关与成员 ──────────────────────────────────

    @property
    def accepting(self) -> bool:
        """本机当前是否开放接收文件。"""
        with self._lock:
            return self._accepting

    def set_accepting(self, value: bool) -> dict[str, Any]:
        """开启 / 关闭接收。关闭时已收到的文件保留（要清空另走 clear）。"""
        with self._lock:
            self._accepting = bool(value)
        return {
            "ok": True,
            "accepting": self.accepting,
            "message": (
                "已开放接收，其他设备可用访问码传文件进来"
                if self.accepting
                else "已关闭接收"
            ),
        }

    def member_by_token(self, token: str) -> dict[str, Any] | None:
        """按传输 token 找成员（供外部 HTTP 层做认证用）。"""
        if not token:
            return None
        with self._lock:
            for one in self.members:
                if secrets.compare_digest(str(one.get("id") or ""), token):
                    return dict(one)
        return None

    def touch_member(self, ip: str, nickname: str = "") -> dict[str, Any]:
        """把某个来源登记为传输成员，**不要求显式"加入房间"**。

        访问码已经是唯一凭证，再让人点一次"加入房间"只是多一道没意义的动作。
        按来源 IP 归并，同一台设备重复上传不会刷出一堆成员。
        """
        who = str(ip or "")
        with self._lock:
            for one in self.members:
                if one.get("ip") == who:
                    if nickname:
                        one["nickname"] = nickname[:24]
                    return dict(one)
            member = {
                "id": secrets.token_urlsafe(12),
                "nickname": (nickname or f"设备 {who}")[:24],
                "host": False,
                "ip": who,
            }
            self.members.append(member)
            return dict(member)

    # ── 公共状态（前端只读这个）────────────────────────

    def received_files(self) -> list[dict[str, Any]]:
        """已收到的文件清单（最新在前）。

        从消息流里取元信息（谁传的、什么时候），再核对文件是否还在磁盘上 ——
        用户可能已经手动删掉，这时不该让界面显示一个点开就报错的条目。
        """
        out: list[dict[str, Any]] = []
        with self._lock:
            items = [m for m in self.messages if isinstance(m.get("file"), dict)]
            items.reverse()
            for message in items:
                info = message["file"]
                path = self._files.get(str(info.get("id") or ""))
                if path is None or not Path(path).is_file():
                    continue
                try:
                    stat = Path(path).stat()
                except OSError:
                    continue
                out.append(
                    {
                        "id": str(info.get("id") or ""),
                        "name": str(info.get("name") or Path(path).name),
                        "size": int(stat.st_size),
                        "from": str(message.get("sender") or ""),
                        "at": time.strftime("%H:%M:%S", time.localtime(stat.st_mtime)),
                    }
                )
        return out

    def received_dir(self) -> str:
        """接收目录（界面上告诉用户"文件放哪了"，省得满桌面找）。"""
        if not self._accepting:
            return ""
        return str(_room_dir(self.room_code or "local"))

    def received_bytes(self) -> int:
        """已接收文件占用总大小（用于配额显示与超限判断）。"""
        try:
            folder = _room_dir(self.room_code or "local")
            return sum(f.stat().st_size for f in folder.glob("*") if f.is_file())
        except OSError:
            return 0

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

    # ── 主机：开放接收 ────────────────────────────────

    def ensure_host(self, nickname: str = "") -> dict[str, Any]:
        """准备本机作为接收方，**不再自己开 HTTP 服务**。

        原先"开房间"要起一个 38620 端口的服务；现在传输挂在手机控制台上，
        而控制台本来就在监听 —— 再开一个端口既多余，又多一处要放行、
        要记地址的地方。所以这里只做两件事：准备好会话身份、打开接收开关。
        """
        if self.role == "member":
            return {"ok": False, "message": "当前是加入方，先退出再改为接收"}

        from .monitor import local_ip

        if nickname:
            self.nickname = nickname[:24]
        self.nickname = self.nickname or "我"
        self.role = "host"
        if not self.room_code:
            self.room_code = f"{secrets.randbelow(1000000):06d}"
        if not self.token:
            self.token = secrets.token_urlsafe(16)

        ip = local_ip() or "127.0.0.1"
        self.host_url = f"http://{ip}:{_console_port()}"
        if not self.members:
            self.members = [
                {"id": self.token[:8], "nickname": self.nickname, "host": True, "ip": ip}
            ]
        self._system("已开放接收，其他设备可用访问码传文件进来")
        return self.set_accepting(True)

    def host_room(self, nickname: str = "", room_name: str = "", password: str = "") -> dict[str, Any]:
        """兼容旧调用：等价于「开放接收」。

        参数保留是为了不打断已经写好的调用方，但 room_name / password
        已随"两个码合并成一个"而失去意义 —— 不再参与判断。
        """
        if room_name:
            self.room_name = room_name[:40]
        if password:
            self.password = password
        return self.ensure_host(nickname)

    # ── 成员：加入房间 ────────────────────────────────

    def join_room(
        self, host: str, code: str, nickname: str, password: str = ""
    ) -> dict[str, Any]:
        """加入另一台机器的传输（电脑 ↔ 电脑）。

        ``code`` 现在是对方手机控制台的**访问码**（原先是房间码）。
        端口走控制台的 38610，不再是独立的 38620 —— 一个地址、一个码。
        ``password`` 参数保留只为兼容旧调用，不参与判断。
        """
        if self.role:
            return {"ok": False, "message": "已经连接着一台，先退出再换一台"}

        code = (code or "").strip()
        if not code:
            return {"ok": False, "message": "请填写对方的访问码"}
        base = (host or "").strip().rstrip("/")
        if not base.startswith("http"):
            ip = base.split(":")[0] or "127.0.0.1"
            port = base.split(":")[1] if ":" in base else str(_console_port())
            base = f"http://{ip}:{port}"

        payload = {"code": code, "nickname": (nickname or "同事")[:24]}
        try:
            response = _post(f"{base}{TRANSFER_PREFIX}/join", payload, timeout=8)
        except Exception as exc:
            return {"ok": False, "message": f"连接失败（检查访问码、地址与网络）：{exc}"}
        if not response.get("ok"):
            return {"ok": False, "message": str(response.get("message") or "连接失败")}

        self.role = "member"
        self.room_code = str(response.get("roomCode") or "local")
        self.room_name = str(response.get("roomName") or "")
        self.nickname = (nickname or "同事")[:24]
        self.host_url = base
        self.token = str(response.get("token") or "")
        self._last_id = 0
        self._remote_accepting = bool(response.get("accepting"))

        self._stop.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="oc-transfer-poll")
        self._thread.start()
        return {
            "ok": True,
            "message": "已连接，可以传文件了"
            if self._remote_accepting
            else "已连接，但对方还没开启接收",
            "roomCode": self.room_code,
            "accepting": self._remote_accepting,
        }

    def _poll_loop(self) -> None:
        """轮询对方状态：主要是知道"对方还开着没"。

        原先轮的是消息流（聊天）。现在聊天已下线，改为查传输状态 ——
        对方关掉接收时能立刻提示"对方已关闭"，而不是让人对着进度条干等。
        """
        while not self._stop.wait(POLL_INTERVAL):
            try:
                data = _post(
                    f"{self.host_url}{TRANSFER_PREFIX}/state",
                    {"token": self.token},
                    timeout=8,
                )
            except Exception as exc:
                self._error = f"与主机断开：{exc}"
                continue
            if not data.get("ok"):
                self._error = str(data.get("message") or "对方已断开")
                continue
            self._error = ""
            with self._lock:
                self._remote_accepting = bool(data.get("accepting"))
                files = [
                    one for one in (data.get("files") or []) if isinstance(one, dict)
                ]
                known = {m.get("id") for m in self.messages if isinstance(m.get("file"), dict)}
                for one in files:
                    if one.get("id") not in known:
                        self.messages.append(
                            {
                                "id": len(self.messages) + 1,
                                "kind": "file",
                                "sender": one.get("from", ""),
                                "text": "",
                                "file": {
                                    "id": one.get("id"),
                                    "name": one.get("name"),
                                    "size": one.get("size"),
                                },
                                "incoming": True,
                            }
                        )
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

        # 对方没开接收就早点说清楚，别让几百 MB 白传一遍
        if not self.remote_accepting:
            return {"ok": False, "message": "对方还没有开启接收（对方电脑：文件传输 → 开启接收）"}

        # 流式上传。这里用 http.client 而不是 urllib.request：后者要求
        # body 是 bytes，会把整个文件读进内存 —— 上限是 200 MB，一个文件
        # 就能把内存吃光（大文件时表现为"程序卡住没反应"）。
        import http.client

        try:
            parts = urllib.parse.urlparse(self.host_url)
            connection = http.client.HTTPConnection(
                parts.hostname or "127.0.0.1", parts.port or 80, timeout=300
            )
            path = (
                f"{TRANSFER_PREFIX}/upload"
                f"?token={urllib.parse.quote(self.token)}"
                f"&name={urllib.parse.quote(name)}"
            )
            with source.open("rb") as handle:
                connection.request(
                    "POST",
                    path,
                    body=handle,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(size),
                    },
                )
                response = connection.getresponse()
                data = json.loads(response.read().decode("utf-8", "ignore"))
            connection.close()
        except Exception as exc:
            return {"ok": False, "message": f"发送失败：{exc}"}
        if not data.get("ok"):
            return {"ok": False, "message": str(data.get("message") or "对方拒绝了文件")}
        return {"ok": True, "message": f"已发送 {name}（{size / 1048576:.1f} MB）"}

    # ── 退出 ──────────────────────────────────────────

    def leave(self, clear_files: bool = False) -> dict[str, Any]:
        was_host = self.role == "host"
        code = self.room_code
        # 原先要 POST /api/leave 通知主机摘掉成员。现在成员是"认证通过即登记"，
        # 主机那边的成员列表只用于显示，留着一条过期记录不影响任何判断 ——
        # 与其发一个注定 404 的请求，不如不发。

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
        url = (
            f"{self.host_url}{TRANSFER_PREFIX}/download/{urllib.parse.quote(file_id)}"
            f"?token={urllib.parse.quote(self.token)}"
        )
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


def route_transfer(
    session: ChatSession,
    handler: Any,
    *,
    auth: Any,
    join: Any = None,
    is_local: Any = None,
) -> bool:
    """把传输相关路由挂到**外部** HTTP 服务上，返回 True 表示请求已处理。

    为什么要做成"可挂载"而不是自己开一个服务：传输已经并入手机控制台，
    必须是同一个端口、同一套认证（访问码）。所以这里不创建任何服务器，
    只借用宿主 handler 的连接，把 ``/api/transfer/*`` 这几件事做掉。

    宿主 handler 需要提供：``path`` / ``client_address`` / ``headers`` /
    ``rfile`` / ``wfile`` / ``_json(data, status)``。

    注入的能力：
      · ``auth(handler)``       → 通过返回成员 dict，未通过返回 None
      · ``join(handler, data)`` → 校验访问码并换传输 token（由宿主实现，
                                   因为访问码只有宿主知道）
      · ``is_local(handler)``   → 是否来自本机（只有本机能开关"是否接收"）
    """
    # self.path 是**带 query 的原始请求行**（如 /api/transfer/upload?name=a.txt），
    # 必须先拆开 —— 直接拿它跟 "/api/transfer/upload" 比永远不相等，
    # 请求会一路落回宿主的 404，而且越查越觉得莫名其妙。
    parsed = urllib.parse.urlparse(str(getattr(handler, "path", "") or ""))
    path = parsed.path
    query = urllib.parse.parse_qs(parsed.query)
    if not path.startswith(TRANSFER_PREFIX):
        return False

    def _from_local() -> bool:
        try:
            return bool(is_local(handler)) if is_local else False
        except Exception:
            return False

    # ── GET ────────────────────────────────────────────
    if path == f"{TRANSFER_PREFIX}/state" and "GET" in handler.command:
        member = auth(handler)
        if member is None:
            handler._json({"ok": False, "message": "未授权"}, 401)
            return True
        used = session.received_bytes()
        handler._json(
            {
                "ok": True,
                "accepting": session.accepting,
                "files": session.received_files(),
                "quotaUsed": used,
                "quotaTotal": ROOM_QUOTA,
                "dir": session.received_dir(),
                "maxFile": MAX_FILE,
                "you": {"ip": member.get("ip", ""), "nickname": member.get("nickname", "")},
            }
        )
        return True

    if path.startswith(f"{TRANSFER_PREFIX}/download/") and "GET" in handler.command:
        member = auth(handler)
        if member is None:
            handler._json({"ok": False, "message": "未授权"}, 401)
            return True
        file_id = path[len(f"{TRANSFER_PREFIX}/download/"):]
        with session._lock:
            target = session._files.get(file_id)
        if target is None or not Path(target).is_file():
            handler._json({"ok": False, "message": "文件不存在或已被清理"}, 404)
            return True
        try:
            size = Path(target).stat().st_size
            handler.send_response(200)
            handler.send_header("Content-Type", "application/octet-stream")
            handler.send_header("Content-Length", str(size))
            handler.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{urllib.parse.quote(Path(target).name)}",
            )
            handler.end_headers()
            # 分块转发：大文件不能整个读进内存（几百 MB 会把内存吃光）
            with Path(target).open("rb") as source:
                shutil.copyfileobj(source, handler.wfile, 256 * 1024)
        except OSError:
            pass
        return True

    # ── POST ───────────────────────────────────────────
    if path == f"{TRANSFER_PREFIX}/join" and "POST" in handler.command:
        if join is None:
            handler._json({"ok": False, "message": "本机不支持加入传输"}, 501)
            return True
        try:
            data = json.loads(handler.rfile.read(_read_len(handler)) or b"{}")
        except (ValueError, OSError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        result = join(handler, data)
        # 状态码要跟结果一致：访问码不对就得是 401/403。返回 200 会让只看
        # 状态码的客户端以为"连上了"，然后在后面某处莫名其妙地失败。
        handler._json(result, 200 if result.get("ok") else 401)
        return True

    if path in (f"{TRANSFER_PREFIX}/open", f"{TRANSFER_PREFIX}/close") and "POST" in handler.command:
        member = auth(handler)
        if member is None:
            handler._json({"ok": False, "message": "未授权"}, 401)
            return True
        # 开关"是否接收"只能本机操作：局域网里的手机不该能替这台机器开门
        if not _from_local():
            handler._json({"ok": False, "message": "只有本机能开启或关闭接收"}, 403)
            return True
        handler._json(session.set_accepting(path.endswith("/open")))
        return True

    if path == f"{TRANSFER_PREFIX}/upload" and "POST" in handler.command:
        member = auth(handler)
        if member is None:
            handler._json({"ok": False, "message": "未授权"}, 401)
            return True
        if not session.accepting:
            # 说清楚是"对方没开"，别让传文件的人以为是网络问题
            handler._json({"ok": False, "message": "对方还没有开启接收"}, 403)
            return True
        name = _clean_name((query.get("name") or ["file.bin"])[0])
        try:
            length = int(handler.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            handler._json({"ok": False, "message": "文件为空"}, 400)
            return True
        if length > MAX_FILE:
            handler._json(
                {"ok": False, "message": f"文件超过 {MAX_FILE // 1048576} MB 上限"}, 413
            )
            return True
        if session.received_bytes() + length > ROOM_QUOTA:
            handler._json({"ok": False, "message": "接收空间已满，请先清理"}, 507)
            return True

        target = _room_dir(session.room_code or "local") / f"{int(time.time())}_{name}"
        remaining = length
        try:
            with target.open("wb") as out:
                while remaining > 0:
                    chunk = handler.rfile.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    out.write(chunk)
                    remaining -= len(chunk)
        except OSError as exc:
            handler._json({"ok": False, "message": f"写入失败：{exc}"}, 500)
            return True

        file_id = secrets.token_urlsafe(10)
        with session._lock:
            session._files[file_id] = target
        # 认证通过即成员：按来源 IP 归并，重复上传不会刷出一堆"成员"
        member = session.touch_member(
            str(handler.client_address[0]), str(member.get("nickname") or "")
        )
        session._add(
            kind="file",
            sender=member.get("nickname", ""),
            text="",
            file={"id": file_id, "name": name, "size": length},
        )
        handler._json(
            {
                "ok": True,
                "message": "已接收",
                "file": {"id": file_id, "name": name, "size": length},
            }
        )
        return True

    # 落在 /api/transfer 下但不认识的具体路径 → 交回宿主继续处理，
    # 别把"没这个接口"和"处理成功"混为一谈。
    return False


def _read_len(handler: Any) -> int:
    """按 Content-Length 读取请求体（带上限）。"""
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        length = 0
    return max(0, min(length, 8 * 1024 * 1024))


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


def transfer_state() -> dict[str, Any]:
    """传输状态（桌面端「文件传输」页与手机控制台共用同一份）。"""
    # 成员端看到的"对方发来的文件"：轮询时把对方列表转成了消息项，
    # 这里再提取出来，界面上就能和"我收到的"一样按文件列出。
    incoming: list[dict[str, Any]] = []
    with chat._lock:
        for message in chat.messages:
            info = message.get("file")
            if isinstance(info, dict) and message.get("incoming"):
                incoming.append(
                    {
                        "id": str(info.get("id") or ""),
                        "name": str(info.get("name") or ""),
                        "size": int(info.get("size") or 0),
                        "from": str(message.get("sender") or ""),
                    }
                )
    return {
        "ok": True,
        "role": chat.role or "",
        "accepting": chat.accepting,
        "remoteAccepting": chat.remote_accepting,
        "files": chat.received_files(),
        "incoming": incoming,
        "dir": chat.received_dir(),
        "quotaUsed": chat.received_bytes(),
        "quotaTotal": ROOM_QUOTA,
        "maxFile": MAX_FILE,
        "hostUrl": chat.host_url,
        "roomCode": chat.room_code,
        "error": chat._error,
    }


def set_accepting(value: bool) -> dict[str, Any]:
    """开启 / 关闭本机接收（不再需要"开房间"那一步）。"""
    if value:
        return chat.ensure_host()
    result = chat.set_accepting(False)
    with chat._lock:
        chat.role = ""
    result.update(transfer_state())
    return result


def host_room(nickname: str = "我", room_name: str = "", password: str = "") -> dict[str, Any]:
    """兼容旧调用：等价于「开启接收」。"""
    result = chat.ensure_host(nickname)
    result.update(transfer_state())
    return result


def join_room(host: str, code: str, nickname: str = "同事", password: str = "") -> dict[str, Any]:
    """连接另一台机器（电脑 ↔ 电脑）。``code`` 为对方的访问码。"""
    return chat.join_room(host, code, nickname, password)


def send_text(text: str) -> dict[str, Any]:
    """聊天消息。界面已下线，后端保留以便恢复。"""
    return chat.send_text(text)


def send_file(path: str) -> dict[str, Any]:
    return chat.send_file(path)


def leave(clear_files: bool = False) -> dict[str, Any]:
    result = chat.leave(clear_files)
    result.update(transfer_state())
    return result


def save_file(file_id: str, name: str) -> dict[str, Any]:
    return chat.save_file_from(file_id, name)


def clear_received() -> dict[str, Any]:
    """清空已收到 / 暂存的文件。"""
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
