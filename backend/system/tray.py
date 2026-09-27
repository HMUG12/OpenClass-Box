"""
系统托盘 —— 实现「关闭 = 最小化到后台常驻」。

标题栏关闭按钮与 Alt+F4 都不再真正退出进程，而是隐藏窗口；
托盘图标提供「显示 / 退出」入口。pystray 在独立守护线程中运行。
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:  # 缺依赖时降级：关闭按钮直接退出
    pystray = None


def _make_icon(color: str = "#0f6cbd") -> "Image.Image":
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([6, 6, 58, 58], radius=14, fill=color)
    # 画一个简单的「OC」字样，避免引入外部图标资源
    d.rectangle([22, 20, 28, 44], fill="white")
    d.rectangle([36, 20, 42, 26], fill="white")
    d.rectangle([36, 34, 42, 44], fill="white")
    return img


class TrayIcon:
    def __init__(self, on_show: Callable[[], None], on_quit: Callable[[], None]) -> None:
        self._on_show = on_show
        self._on_quit = on_quit
        self._icon: Optional["pystray.Icon"] = None

    @property
    def available(self) -> bool:
        return pystray is not None

    def start(self) -> None:
        if not self.available:
            return
        menu = pystray.Menu(
            pystray.MenuItem("显示 OpenClass", lambda _: self._on_show(), default=True),
            pystray.MenuItem("退出", lambda _: self._on_quit()),
        )
        self._icon = pystray.Icon("OpenClass-Box", _make_icon(), "OpenClass-Box", menu)
        threading.Thread(target=self._icon.run, daemon=True).start()

    def notify(self, title: str, message: str) -> bool:
        """托盘气泡通知（安全告警用它弹给用户，不依赖窗口是否打开）。"""
        if self._icon is None:
            return False
        try:
            self._icon.notify(message, title)
            return True
        except Exception:
            return False

    def stop(self) -> None:
        if self._icon is not None:
            self._icon.stop()
            self._icon = None
