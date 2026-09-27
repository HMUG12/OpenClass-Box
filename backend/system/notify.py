"""
系统通知 —— 安全告警必须「弹出来」，而不是只躺在软件里。

真实场景：老师上课时窗口是最小化或收在托盘的。这时剪贴板里粘到一个钓鱼网址、
浏览器访问了高风险站点，只靠界面内横幅根本看不见。所以把告警送到 Windows
通知区域：

  · 有托盘时 → 走 pystray 的气泡通知（不引入额外依赖）；
  · 没有托盘或发送失败 → 退回运行日志，保证告警不丢，界面横幅仍然显示。

main.py 在托盘就绪后注入真正的发送实现（set_sink）；核心模块只依赖本文件，
避免反向依赖界面层。同一个 key 在短时间内只会弹一次，防止刷屏。
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

_sink: Optional[Callable[[str, str], bool]] = None
_lock = threading.Lock()
_last: dict[str, float] = {}

# 同类通知的最短间隔（秒）：检测是持续性的，不能每次都弹
MIN_INTERVAL = 8.0


def set_sink(sink: Optional[Callable[[str, str], bool]]) -> None:
    """注入系统通知实现（由 main.py 在托盘创建后调用）。"""
    global _sink
    with _lock:
        _sink = sink


def available() -> bool:
    with _lock:
        return _sink is not None


def notify(title: str, message: str, key: str = "", interval: float = MIN_INTERVAL) -> bool:
    """发送系统通知。返回是否真的弹出来了。"""
    token = key or title
    now = time.time()
    with _lock:
        if now - _last.get(token, 0.0) < interval:
            return False
        _last[token] = now
        sink = _sink

    if sink is not None:
        try:
            if sink(title, message):
                return True
        except Exception:
            pass

    # 兜底：至少写进运行日志，用户反馈问题时能看到曾触发过的告警
    try:
        from ..core.applog import log

        log(f"通知（未弹出，仅记录）：{title} — {message}", "WARN")
    except Exception:
        pass
    return False
