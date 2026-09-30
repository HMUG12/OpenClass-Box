"""
开机自启 —— 写入/移除当前用户的注册表 Run 键。

仅作用于当前用户（HKEY_CURRENT_USER），不需要管理员权限，也不会污染全局。
打包成 exe 后写入的是 exe 路径；开发态写入 python main.py。

关键点（修过的真实问题）：
自启动命令**不再无条件带 --hidden**。旧版本无论用户把「启动时的窗口行为」
设成什么都不管，写进注册表的命令永远带 --hidden，于是永远静默启动 ——
用户明明选了「显示界面」。现在命令不写死隐藏参数，由主程序读取 startup_mode
判断，设置才真正生效；同时提供 repair_autostart() 修正历史遗留的旧命令。
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import winreg
except ImportError:
    winreg = None

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "OpenClass-Box"


def _command() -> str:
    """自启动命令行（不带 --hidden，静默与否交给设置决定）。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --autostart'
    root = Path(__file__).resolve().parents[2]
    return f'"{sys.executable}" "{root / "main.py"}" --autostart'


def current_command() -> str:
    """注册表里现存的自启命令（没有则空串）。"""
    if not winreg:
        return ""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY)
    except OSError:
        return ""
    try:
        return str(winreg.QueryValueEx(key, APP_NAME)[0])
    except OSError:
        return ""
    finally:
        winreg.CloseKey(key)


def set_autostart(enabled: bool) -> bool:
    if not winreg:
        return False
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
    except OSError:
        try:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY)
        except OSError:
            return False
    try:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except OSError:
                pass
        return True
    except OSError:
        return False
    finally:
        winreg.CloseKey(key)


def is_autostart() -> bool:
    return bool(current_command())


def repair_autostart() -> bool:
    """修正历史版本写死的 --hidden 自启命令（让「显示界面」真正生效）。

    只在已开启自启、且命令确实不是当前格式时重写；命令里必须含本程序标识，
    避免动到用户自己添加的其它自启项。返回是否做了修正。
    """
    existing = current_command()
    if not existing:
        return False
    expected = _command()
    if existing == expected:
        return False
    if "OpenClass-Box.exe" not in existing and "main.py" not in existing:
        return False
    return set_autostart(True)
