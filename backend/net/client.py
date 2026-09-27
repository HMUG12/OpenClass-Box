"""
学生机客户端 —— 主动连接老师机：心跳上报 + 取指令 + 执行 + 回报。

运行方式（全部在后台守护线程，不干扰界面操作）：
  1. 优先使用已保存的服务器地址；否则局域网广播发现；
  2. 用配对码换取 token，写入配置（重启后免重新配对）；
  3. 循环：每 5 秒上报状态并捎回待执行指令；有指令立即执行并回传结果；
  4. 断线自动重连（退避 3→30 秒），期间状态明确展示给界面。

两个必须注意的实现细节：
  - 局域网请求必须**禁用系统代理**（否则挂代理的机器连不上同网段老师机）；
  - 所有异常都转成结构化状态，绝不抛出到线程外。
"""
from __future__ import annotations

import json
import platform
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..core.config import config
from ..core.paths import config_dir
from .commands import collect_files, execute
from .proto import PROTOCOL_VERSION, REPORT_INTERVAL, make_token

_TIMEOUT = 20.0
_LOOP_INTERVAL = 5.0
_MAX_BACKOFF = 30.0

def refresh_opener() -> None:
    """按配置重建请求器。

    默认**直连**（显式禁用系统代理，否则挂代理的机器连不上同网段 A 端）；
    若用户在设置里填了代理（跨网段 / 内网穿透场景），则改走该代理。
    """
    global _opener
    proxy = str(config.get("lan_proxy", "") or "").strip()
    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    else:
        handler = urllib.request.ProxyHandler({})
    _opener = urllib.request.build_opener(handler)


# 局域网直连：显式禁用代理（配置了代理时由 refresh_opener 覆盖）
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post(url: str, payload: dict[str, Any], timeout: float = _TIMEOUT) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with _opener.open(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def _get(url: str, timeout: float = _TIMEOUT) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with _opener.open(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def _node_id() -> str:
    """本机在机房中的稳定标识（生成一次后持久化）。"""
    value = str(config.get("lan_node_id", "") or "")
    if not value:
        value = make_token()[:12]
        config.set("lan_node_id", value)
    return value


def receive_dir() -> Path:
    """学生机接收老师机下发文件的目录（保证可写）。"""
    folder = config_dir() / "lan_received"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return folder


def _foreground_app() -> str:
    """当前前台程序名（Windows；其它平台返回空串）。"""
    if platform.system() != "Windows":
        return ""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        import psutil

        return psutil.Process(pid.value).name()
    except Exception:
        return ""


def _status_payload() -> dict[str, Any]:
    """上报给老师机的本机状态（真实采集，失败项为 None）。"""
    from ..core.monitor import device_info, monitor

    payload: dict[str, Any] = {
        "id": _node_id(),
        "name": platform.node() or "未知设备",
        "os": f"{platform.system()} {platform.release()}",
        "foreground": _foreground_app(),
        "ts": int(time.time() * 1000),
    }
    try:
        device = device_info()
        payload["user"] = device.get("user", "")
        model = f"{device.get('manufacturer', '')} {device.get('model', '')}".strip()
        payload["model"] = model
    except Exception:
        pass

    try:
        metrics = monitor.metrics()
        payload["cpu"] = round(float(metrics.get("cpu", {}).get("percent") or 0), 1)
        payload["memory"] = round(float(metrics.get("memory", {}).get("percent") or 0), 1)
        payload["memTotalGB"] = round(
            float(metrics.get("memory", {}).get("total") or 0) / (1024 ** 3), 1
        )
        payload["uptime"] = int(time.time() - float(metrics.get("bootTime") or time.time()))
    except Exception:
        pass

    try:
        hardware = monitor.hardware()
        disks = hardware.get("disks") or []
        if disks:
            payload["disk"] = round(float(disks[0].get("percent") or 0), 1)
        response = monitor.metrics()
        payload["download"] = response.get("network", {}).get("download", 0)
    except Exception:
        pass
    return payload


class LanClient:
    """学生机连接器（单例使用）。"""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._state = "idle"          # idle / connecting / connected / error
        self._message = "未启用"
        self._server = ""
        self._teacher = ""
        self._token = ""
        self._last_report = 0.0
        self._last_error = ""

    # ── 状态 ──────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": bool(self._thread and self._thread.is_alive()),
                "state": self._state,
                "message": self._message,
                "server": self._server,
                "teacher": self._teacher,
                "paired": bool(self._token),
                "lastReport": int(self._last_report * 1000),
                "nodeId": _node_id(),
            }

    def _set(self, state: str, message: str) -> None:
        with self._lock:
            self._state = state
            self._message = message

    # ── 生命周期 ──────────────────────────────────────

    def start(self, server: str = "", code: str = "") -> dict[str, Any]:
        """启动连接。server 为空则走局域网自动发现。"""
        if self._thread and self._thread.is_alive():
            return {"ok": True, "message": "客户端已在运行"}
        self._stop.clear()
        if server:
            config.set("lan_server_url", server)
        if code:
            config.set("lan_pair_code", code)
        self._set("connecting", "正在连接老师机…")
        self._thread = threading.Thread(target=self._loop, daemon=True, name="oc-lan-client")
        self._thread.start()
        return {"ok": True, "message": "客户端已启动"}

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        self._thread = None
        self._set("idle", "已停止连接")
        return {"ok": True, "message": "客户端已停止"}

    # ── 主循环 ────────────────────────────────────────

    def _loop(self) -> None:
        backoff = 3.0
        while not self._stop.is_set():
            try:
                if not self._ensure_connected():
                    self._set("error", self._last_error or "未能连接老师机")
                    self._sleep(backoff)
                    backoff = min(_MAX_BACKOFF, backoff * 1.6)
                    continue
                backoff = 3.0
                self._report_once()
                self._sleep(_LOOP_INTERVAL)
            except Exception as exc:  # 任何异常都不允许终止线程
                self._last_error = str(exc)
                self._set("error", f"连接异常：{exc}")
                self._sleep(backoff)
                backoff = min(_MAX_BACKOFF, backoff * 1.6)

    def _sleep(self, seconds: float) -> None:
        self._stop.wait(max(0.5, seconds))

    # ── 连接与配对 ────────────────────────────────────

    def _ensure_connected(self) -> bool:
        with self._lock:
            if self._token and self._server:
                return True

        # 1) 确定服务器地址：配置 > 自动发现
        server = str(config.get("lan_server_url", "") or "")
        teacher_name = ""
        if not server:
            from .discovery import broadcast_search

            self._set("connecting", "正在搜索局域网内的老师机…")
            found = broadcast_search(timeout=3.0)
            if not found:
                self._last_error = "未发现老师机（请确认老师机已启动服务，或手动填写 IP）"
                return False
            server = f"http://{found[0]['ip']}:{found[0].get('httpPort', 38900)}"
            teacher_name = str(found[0].get("name") or "")
        if not server.startswith("http"):
            server = f"http://{server}"

        # 2) 用配对码换取 token
        code = str(config.get("lan_pair_code", "") or "")
        if not code:
            self._last_error = "尚未填写配对码"
            return False

        self._set("connecting", f"正在与 {server} 配对…")
        try:
            result = _post(
                f"{server.rstrip('/')}/api/pair",
                {
                    "code": code,
                    "node": {
                        "id": _node_id(),
                        "name": platform.node(),
                        "os": f"{platform.system()} {platform.release()}",
                    },
                },
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self._last_error = f"无法连接 {server}：{exc}"
            return False

        if not result.get("ok"):
            self._last_error = str(result.get("message") or "配对失败")
            return False

        with self._lock:
            self._token = str(result.get("token") or "")
            self._server = server
            self._teacher = str(result.get("teacher") or teacher_name or "老师机")
        self._set("connected", f"已连接：{self._teacher}")
        return True

    # ── 上报与执行 ────────────────────────────────────

    def _report_once(self) -> None:
        with self._lock:
            server, token = self._server, self._token
        if not server or not token:
            return

        try:
            result = _post(
                f"{server.rstrip('/')}/api/report",
                {"token": token, "info": _status_payload(), "version": PROTOCOL_VERSION},
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            with self._lock:
                self._token = ""      # 触发下一轮重新配对
            self._set("error", f"与老师机断开：{exc}")
            return

        if not result.get("ok"):
            with self._lock:
                self._token = ""
            self._set("error", str(result.get("message") or "令牌失效，将重新配对"))
            return

        with self._lock:
            self._last_report = time.time()
            self._set("connected", f"已连接：{self._teacher}")

        commands = result.get("commands") or []
        if commands:
            self._run_commands(commands, server, token)

    def _receive_file(self, server: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        """接收老师机下发的文件（分块写入磁盘，不吃内存）。"""
        file_id = str(payload.get("fileId") or "")
        name = Path(str(payload.get("name") or "file.bin")).name
        if not file_id:
            return {"ok": False, "message": "指令缺少文件标识", "data": {}}

        target = receive_dir() / name
        url = f"{server.rstrip('/')}/api/file/{file_id}?token={quote(token)}"
        try:
            request = urllib.request.Request(url)
            with _opener.open(request, timeout=900) as resp, target.open("wb") as handle:
                shutil.copyfileobj(resp, handle, 1024 * 1024)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {"ok": False, "message": f"接收失败：{exc}", "data": {}}

        try:
            size = target.stat().st_size
        except OSError:
            size = 0
        return {
            "ok": True,
            "message": f"已接收 {name}（{size / 1048576:.1f} MB）",
            "data": {"path": str(target)},
        }

    def _send_collection(
        self, server: str, token: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """把指定目录打包上传给老师机（收作业）。"""
        paths = payload.get("paths") if isinstance(payload.get("paths"), list) else []
        if not paths:
            paths = [str(Path.home() / "Desktop")]
        result = collect_files([str(item) for item in paths])
        if not result.get("ok"):
            return result

        info = result.get("data") or {}
        zip_path = Path(str(info.get("zip") or ""))
        if not zip_path.is_file():
            return {"ok": False, "message": "打包结果丢失", "data": {}}

        name = f"{platform.node()}_{time.strftime('%Y%m%d_%H%M%S')}.zip"
        url = f"{server.rstrip('/')}/api/upload?token={quote(token)}&name={quote(name)}"
        try:
            payload_bytes = zip_path.read_bytes()
        except OSError as exc:
            return {"ok": False, "message": f"读取打包结果失败：{exc}", "data": {}}

        request = urllib.request.Request(
            url,
            data=payload_bytes,
            method="POST",
            headers={"Content-Type": "application/octet-stream"},
        )
        try:
            with _opener.open(request, timeout=900) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {"ok": False, "message": f"上传失败：{exc}", "data": {}}

        try:
            zip_path.unlink(missing_ok=True)
        except OSError:
            pass
        if not data.get("ok"):
            return {"ok": False, "message": str(data.get("message") or "老师机未接受"), "data": {}}
        return {
            "ok": True,
            "message": f"已上交 {len(payload_bytes) / 1048576:.1f} MB（{info.get('count', 0)} 个文件）",
            "data": {},
        }

    def _run_commands(self, commands: list[Any], server: str, token: str) -> None:
        results = []
        for command in commands:
            if not isinstance(command, dict):
                continue
            action = str(command.get("action") or "")
            payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}
            started = time.time()
            if action == "push_file":
                outcome = self._receive_file(server, token, payload)
            elif action == "pull_file":
                outcome = self._send_collection(server, token, payload)
            else:
                # 电源类动作需要知道是谁下的指令（写进本机日志，便于事后追溯）
                if action == "power":
                    payload = {**payload, "source": self._teacher or ""}
                outcome = execute(action, payload)
            results.append(
                {
                    "seq": command.get("seq"),
                    "action": action,
                    "ok": bool(outcome.get("ok")),
                    "message": str(outcome.get("message") or "")[:300],
                    "detail": f"耗时 {time.time() - started:.1f}s",
                    "data": outcome.get("data") or {},
                }
            )
        if not results:
            return
        try:
            _post(f"{server.rstrip('/')}/api/result", {"token": token, "results": results})
        except (urllib.error.URLError, OSError, ValueError):
            pass  # 结果回传失败不影响后续心跳


client = LanClient()
