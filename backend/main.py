"""
OpenClass 桌面宿主 —— pywebview + WebView2 + 系统托盘常驻。

设计要点：
  1. 界面由前端（React + Fluent UI）渲染，视觉一致性远好于手写 QSS；
  2. WebView2 是 Windows 10/11 的系统组件，无需打包 Chromium；
  3. 业务逻辑仍在 Python 侧，工具以独立进程运行，互不干扰；
  4. 关闭按钮 / Alt+F4 不退出进程，而是最小化到托盘（常驻后台）；
  5. 通过文件参数（右键「打开方式」）调用时，自动路由到对应集成工具。
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path

import webview

from .api import Api
from .core import paths
from .system.tray import TrayIcon

WINDOW_TITLE = "OpenClass-Box"
WINDOW_SIZE = (1180, 760)
MIN_SIZE = (900, 600)
ICON = paths.app_root() / "openclass.ico"

# 单实例互斥体（Local\ = 当前用户会话，多用户同时登录互不影响）
_MUTEX_NAME = r"Local\OpenClass-Box-SingleInstance"
_mutex_handle: int | None = None


def _activate_existing() -> None:
    """把已在运行的窗口唤到前台（最小化状态先还原）。"""
    try:
        import ctypes

        from .system.wincontrol import restore

        restore()
        hwnd = ctypes.windll.user32.FindWindowW(None, WINDOW_TITLE)
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def ensure_single_instance() -> bool:
    """确保只有本实例在运行。

    已存在实例时：唤起它的窗口并返回 False（调用方应直接退出）。
    用 Win32 命名互斥体实现，进程崩溃后由系统自动释放，不会残留锁。
    """
    if sys.platform != "win32":
        return True
    global _mutex_handle
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        _mutex_handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            _activate_existing()
            return False
    except (AttributeError, OSError):
        return True
    return True


def resolve_url(dev: bool) -> str:
    """决定加载到 WebView 的地址。

    这里返回**本地路径**而非 file:// URI，配合 webview.start(http_server=True)
    由 pywebview 内置的本地 HTTP 服务提供页面。file:// 直加载在部分设备的
    WebView2 上会因安全策略/路径编码差异而整页加载失败（界面一片黑，
    且 pywebview 注入的标题栏拖动脚本也一起失效）。
    """
    if dev:
        return "http://localhost:5173"

    index = paths.frontend_index()
    if not index.is_file():
        raise FileNotFoundError(
            "未找到前端构建产物 frontend/dist/index.html。\n"
            "请先执行：cd frontend && npm run build"
        )
    loading = index.parent / "loading.html"
    if loading.is_file():
        return str(loading)  # 先显示启动加载动画，再由 loading.html 跳转到 index.html
    return str(index)


def ensure_webview2() -> bool:
    """检测 WebView2 运行时；缺失时给出明确提示（避免只看到黑屏）。

    界面完全由 WebView2 渲染，缺它时窗口会是一片黑——与其让用户猜，
    不如直接弹窗告诉他装什么、去哪装。
    """
    if sys.platform != "win32":
        return True

    # 随包携带的固定版本运行时优先：有它就不依赖目标机是否安装 WebView2
    bundled = paths.app_root() / "WebView2Runtime"
    if (bundled / "msedgewebview2.exe").is_file():
        return True

    guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"  # WebView2 Runtime 官方产品码
    try:
        import winreg

        for root, sub in (
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
            (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
        ):
            try:
                with winreg.OpenKey(root, sub) as key:
                    winreg.QueryValueEx(key, "pv")
                    return True
            except OSError:
                continue
    except ImportError:
        return True

    # 注册表没有时再看安装目录（部分绿色部署只落文件）
    for probe in (
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft" / "EdgeWebView" / "Application",
        Path(os.environ.get("ProgramFiles", "")) / "Microsoft" / "EdgeWebView" / "Application",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "EdgeWebView" / "Application",
    ):
        try:
            if probe.is_dir() and any(probe.iterdir()):
                return True
        except OSError:
            continue

    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            "未检测到 Microsoft Edge WebView2 运行时，程序界面无法显示。\n\n"
            "请先安装 WebView2 运行时（微软官方、免费），安装后重新打开本程序：\n"
            "https://developer.microsoft.com/microsoft-edge/webview2/\n\n"
            "（使用安装包安装时会自动装好这一组件）",
            "OpenClass-Box - 缺少界面运行组件",
            0x30,  # MB_ICONWARNING
        )
    except (AttributeError, OSError):
        pass
    return False


def webview_guess_gui() -> str | None:
    """优先使用 EdgeChromium(WebView2)。"""
    return "edgechromium" if sys.platform == "win32" else None


class AppHost:
    def __init__(self) -> None:
        self.api = Api()
        self.window = None
        self.tray = TrayIcon(self._show, self._quit)
        # 安全告警走系统通知：老师上课时窗口可能收在托盘里，只在界面提示等于没提醒
        try:
            from .system.notify import set_sink

            set_sink(self.tray.notify)
        except Exception:
            pass

    def _show(self) -> None:
        if self.window is None:
            return
        # 先解除最小化（Win32 直控），再显示，避免「点了托盘图标窗口不出来」
        try:
            from .system.wincontrol import restore

            restore()
        except Exception:
            pass
        self.window.show()

    def _quit(self) -> None:
        # 还有启用中的定时任务时先问一句：否则用户会以为任务还在后台跑
        try:
            from .core import tasks

            count = tasks.enabled_count()
            if count:
                import ctypes

                text = (
                    f"还有 {count} 个定时任务处于启用状态。\n\n"
                    "退出后这些任务不会再执行（下次启动程序后会继续）。\n确定要退出吗？"
                )
                answer = ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
                    None, text, "OpenClass-Box · 定时任务", 0x04 | 0x30
                )
                if answer != 6:  # IDYES
                    return
        except Exception:
            pass

        try:
            self.tray.stop()
        except Exception:
            pass
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                import os

                os._exit(0)
        else:
            import os

            os._exit(0)

    def run(
        self,
        dev: bool = False,
        debug: bool = False,
        hidden: bool = False,
        files: list[str] | None = None,
        role: str = "auto",
    ) -> None:
        paths.ensure_runtime_dirs()

        # 缺 WebView2 时明确提示（而不是留一个黑屏窗口）
        if not ensure_webview2():
            sys.exit(3)

        try:
            url = resolve_url(dev)
        except FileNotFoundError as exc:
            print(f"[OpenClass] {exc}", file=sys.stderr)
            sys.exit(2)

        # 随包携带的固定版本 WebView2：直接指定运行目录，彻底摆脱目标机
        # 是否安装 WebView2 的问题（pywebview 会用 BrowserExecutableFolder 加载它）
        bundled_rt = paths.app_root() / "WebView2Runtime"
        if (bundled_rt / "msedgewebview2.exe").is_file():
            webview.settings['WEBVIEW2_RUNTIME_PATH'] = str(bundled_rt)

        # frameless：仅标题栏(.oc-titlebar-drag)可拖，其余区域(按钮)正常可点
        webview.settings['DRAG_REGION_SELECTOR'] = '.oc-titlebar-drag'

        # 渲染兼容：部分一体机（老显卡 / 驱动）在 WebView2 硬件合成下会出现
        # 「点某些界面整窗黑屏」，关掉 GPU 合成即可绕开（对日常使用影响很小）。
        #
        # 渲染模式可在「设置 → 系统集成 → 渲染模式」里切换：
        #   safe（默认）= 关闭 GPU 合成，优先保证不黑屏；
        #   gpu          = 启用 GPU 合成，动画更顺滑（确认本机不黑屏后再切）
        render_mode = "safe"
        try:
            from .core.config import config as _config

            render_mode = str(_config.get("render_mode", "safe") or "safe").lower()
        except Exception:
            render_mode = "safe"

        if render_mode != "gpu":
            os.environ.setdefault(
                "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--disable-gpu-compositing"
            )

        # 窗口直接显示：启动动画由 frontend/public/loading.html 负责
        # （窗口一打开就能看到的加载动画），不再做「窗口隐藏 + 滑入」
        # 启动时补一份配置快照（当天已有就不重复写）：设置被改乱了能回退。
        # 失败不影响启动 —— 备份是兜底，不是必需品
        try:
            from .core.config_backup import auto_backup

            auto_backup()
        except Exception:
            pass

        self.window = webview.create_window(
            title=WINDOW_TITLE,
            url=url,
            js_api=self.api,
            width=WINDOW_SIZE[0],
            height=WINDOW_SIZE[1],
            min_size=MIN_SIZE,
            frameless=True,
            easy_drag=False,  # 仅标题栏 RPC 拖动，避免全窗口拖动导致按钮点不动
            background_color="#1B1A19",
            text_select=False,
        )
        self.api.attach_window(self.window)
        self.api.attach_reveal(self._reveal_window)
        self.api.attach_tray(self.tray)
        self.api.tray_available = self.tray.available
        self._start_hidden = bool(hidden or files)

        # 关闭 / Alt+F4 的行为由设置决定：默认最小化到托盘常驻，
        # 用户可在「设置 → 关闭行为」里改为直接退出。
        def on_closing(e: object) -> None:
            from .core import config

            if self.tray.available and bool(config.get("close_to_tray", True)):
                setattr(e, "cancel", True)
                self.window.hide()

        self.window.events.closing += on_closing

        # 来自文件参数的调用（右键「打开方式」）→ 路由启动并仅留托盘
        def route_files() -> None:
            for f in files or []:
                try:
                    self.api.open_file_with(f)
                except Exception:
                    pass

        threading.Thread(target=self.api.refresh_tools, daemon=True).start()
        # 剪贴板网址监听：复制到风险网址时自动产生告警供前端提示
        try:
            from .core.url_watch import start as start_url_watch

            start_url_watch()
        except Exception:
            pass
        # 浏览网站自动检测：读 Chrome/Edge 历史库，发现风险网址即告警
        try:
            from .core.browser_watch import start as start_browser_watch

            start_browser_watch()
        except Exception:
            pass
        if files:
            threading.Thread(target=route_files, daemon=True).start()

        # 右键「打开方式」注册表自愈：整体移动文件夹后首次启动自动刷新路径
        try:
            from .system.shell_integration import ensure_openwith

            ensure_openwith()
        except Exception:
            pass

        self.tray.start()

        # 本机定时任务调度器：总开关关闭时它只是空转，不会有任何执行
        try:
            from .core.tasks import runner
            from .core.tasks import set_notifier as tasks_set_notifier

            # 任务失败时弹一次托盘气泡（后台任务静默失败最难排查）
            tasks_set_notifier(lambda title, message: self.tray.notify(title, message))
            runner.start()
        except Exception:
            pass

        # 安全守护：弹窗拦截 / 高占用提醒（都必须在设置里显式开启）
        try:
            from .core.guard import guard_settings, watchdog

            settings = guard_settings()
            if settings.get("popup_guard") or settings.get("high_usage_guard"):
                watchdog.start()
        except Exception:
            pass

        # 启动角色：A 端（服务端）自动拉起管理服务；B 端恢复客户端身份
        if role and role != "auto":
            try:
                self.api.apply_role(role)
            except Exception:
                pass

        # 窗口何时露面、以什么方式露面，统一交给 _reveal_window()：
        # 由前端首屏就绪后调用 api.frontend_ready() 触发（见下方方法与 api.py）。
        # 静默启动时仍需 show() 一次再 hide()，否则托盘会没有可唤起的窗口。

        try:
            webview.start(
                debug=debug,
                http_server=True,
                private_mode=True,
                gui=webview_guess_gui(),
                icon=str(ICON) if ICON.exists() else None,
            )
        finally:
            self.tray.stop()

    # ── 窗口露面 ────────────────────────────────────────────

    def _reveal_window(self) -> None:
        """首屏就绪后的窗口处理（由前端 frontend_ready() 触发，跑在后台线程）。

        启动动画交回 frontend/public/loading.html —— 也就是窗口一打开就能看到的
        那个加载动画，不再做「窗口隐藏 + 从底部滑入」。这里只剩静默启动这一件事：
          · 正常启动：窗口已由 pywebview 直接显示，什么都不用做；
          · 静默启动 / 右键打开文件：显示一次再藏进托盘，避免打扰用户。
        """
        window = self.window
        if window is None or not getattr(self, "_start_hidden", False):
            return
        try:
            window.show()
            if self.tray.available:
                window.hide()
        except Exception:
            pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="OpenClass", description="开源实用工具箱")
    parser.add_argument("--dev", action="store_true", help="连接本地 vite 开发服务器")
    parser.add_argument("--debug", action="store_true", help="开启 WebView 调试")
    parser.add_argument("--hidden", action="store_true", help="启动后仅驻留系统托盘")
    parser.add_argument(
        "--autostart",
        action="store_true",
        help="由开机自启拉起（是否静默仍由「启动时的窗口行为」设置决定）",
    )
    parser.add_argument(
        "--role",
        choices=("auto", "a", "b"),
        default="auto",
        help="启动角色：a=A 端（服务端，自动开管理服务）/ b=B 端（本体，恢复学生机身份）/ auto=沿用上次配置",
    )
    parser.add_argument("files", nargs="*", help="要打开的文件路径（右键打开方式）")
    parser.add_argument("--register-openwith", action="store_true", help="注册右键「打开方式」入口后退出")
    parser.add_argument("--unregister-openwith", action="store_true", help="注销右键「打开方式」入口后退出")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # 运行日志：把启动信息与未捕获异常落到 data/logs/app.log，
    # 现场反馈问题时可以直接看这份日志定位
    from .core.applog import install_hooks, log_startup

    install_hooks()
    try:
        from . import __version__ as _current_version  # type: ignore[attr-defined]

        log_startup(_current_version)
    except ImportError:
        log_startup()

    # 单实例：重复启动时唤起已有窗口并直接退出（避免开多个）
    if not (args.register_openwith or args.unregister_openwith):
        if not ensure_single_instance():
            return 0

    if args.register_openwith or args.unregister_openwith:
        from .system.shell_integration import set_openwith

        ok = set_openwith(bool(args.register_openwith))
        verb = "注册" if args.register_openwith else "注销"
        print(f"[OpenClass] 右键「打开方式」{verb}: {'成功' if ok else '失败（需以 OpenClass.exe 运行）'}")
        return 0 if (ok or args.unregister_openwith) else 1

    # 启动行为可配置：silent = 静默启动到托盘（默认 window = 显示界面）
    hidden = bool(args.hidden)
    if not hidden:
        try:
            from .core.config import config

            hidden = str(config.get("startup_mode", "window")).lower() == "silent"
        except Exception:
            hidden = False

    # 修正历史遗留：旧版本写入注册表的自启命令带 --hidden，会让「显示界面」
    # 设置失效。这里开机自启拉起时顺势重写一次（只在命令不符时才会真写注册表）。
    if args.autostart:
        try:
            from .system.autostart import repair_autostart

            repair_autostart()
        except Exception:
            pass

    try:
        AppHost().run(
            dev=args.dev,
            debug=args.debug,
            hidden=hidden,
            files=args.files or None,
            role=args.role,
        )
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
