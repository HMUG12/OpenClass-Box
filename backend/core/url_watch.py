"""
剪贴板网址监听 —— 复制网址后自动评分，风险网址推送给前端提示。

只做「提醒」，不拦截任何用户操作：发现剪贴板里的网址评分达到 warn/danger，
就记一条告警，前端取走后展示提示条。
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any

from .url_guard import check_url

_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)
_INTERVAL = 2.0          # 剪贴板轮询间隔
_DEDUPE_TTL = 300.0      # 同一网址 5 分钟内不重复告警
_MAX_ALERTS = 20

_alerts: list[dict[str, Any]] = []
_seen: dict[str, float] = {}
_thread: threading.Thread | None = None
_lock = threading.Lock()


def _read_clipboard() -> str:
    """读取剪贴板文本；失败返回空串（无 pywin32 或剪贴板被占用）。"""
    try:
        import win32clipboard

        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                return str(win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT) or "")
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass
    return ""


def _loop() -> None:
    while True:
        try:
            from .security import record, settings

            if settings()["clipboard"]:
                text = _read_clipboard()
                match = _URL_RE.search(text or "")
                if match:
                    url = match.group(0)
                    now = time.time()
                    with _lock:
                        fresh = now - _seen.get(url, 0.0) > _DEDUPE_TTL
                        if fresh:
                            _seen[url] = now
                    if fresh:
                        result = check_url(url)
                        record(result, "clipboard")  # 全部记录，供安全中心展示
                        if result["level"] != "safe":
                            push_alert(result)
        except Exception:
            pass
        time.sleep(_INTERVAL)


def start() -> None:
    """启动监听（幂等，已在运行则直接返回）。"""
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, daemon=True, name="oc-url-watch")
    _thread.start()


def push_alert(result: dict[str, Any]) -> None:
    """投递一条风险告警：弹系统通知 + 留给界面横幅。

    系统通知是关键：老师上课时窗口多半最小化或收在托盘里，
    只在界面里显示横幅等于没提醒到人。
    """
    try:
        from ..system.notify import notify

        level = str(result.get("level") or "")
        url = str(result.get("url") or "")
        reasons = result.get("reasons") or []
        detail = str(reasons[0]) if reasons else "请留意链接来源，不要输入账号密码"
        label = "高危网址" if level == "danger" else "可疑网址"
        notify(
            "OpenClass 安全提醒",
            f"{label}（{result.get('score', 0)} 分）：{url[:70]}\n{detail}",
            key="url-alert",
        )
    except Exception:
        pass

    with _lock:
        _alerts.append(result)
        del _alerts[:-_MAX_ALERTS]


def alerts() -> list[dict[str, Any]]:
    """取出未读告警（取出即清空，避免重复弹提示）。"""
    global _alerts
    with _lock:
        items = list(_alerts)
        _alerts = []
    return items
