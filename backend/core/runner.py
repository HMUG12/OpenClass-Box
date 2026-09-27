"""
工具运行器 —— 以独立进程启动工具。

这是本应用的核心设计决策：工具之间、工具与主程序之间完全进程隔离。
任何工具崩溃都不会拖垮主界面；也正因如此，
我们才能把 .exe / .bat / .ps1 / .py 这些毫不相干的第三方程序纳入同一个工具箱。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

DETACHED_FLAGS = 0
if os.name == "nt":
    DETACHED_FLAGS = (
        subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
        | subprocess.CREATE_NO_WINDOW
    )

_EXECUTABLE_SUFFIXES = {".exe", ".com", ".bat", ".cmd", ".ps1", ".html", ".htm"}

# 网页类工具（插件最常见的形态）：交给系统默认浏览器打开
_HTML_SUFFIXES = {".html", ".htm"}


def resolve_command(entry: Path, args: list[str]) -> tuple[list[str], Path]:
    """根据入口文件类型决定实际要执行的命令行。"""
    suffix = entry.suffix.lower()
    workdir = entry.parent if entry.is_dir() or True else entry.parent

    if suffix == ".py":
        return [sys.executable, str(entry), *args], workdir
    if suffix == ".bat" or suffix == ".cmd":
        return ["cmd.exe", "/c", str(entry), *args], workdir
    if suffix == ".ps1":
        return [
            "powershell.exe",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(entry),
            *args,
        ], workdir
    extra: list[str] = []
    # OpenOffice / LibreOffice：bootstraprc 里的 UserInstallation 在便携或
    # 非安装场景下不生效，必须由命令行 -env 指定一个可写的用户配置目录，
    # 否则启动即报 "A general error occurred while accessing your central
    # configuration"。这里统一指向 program 同级目录下的 user。
    if entry.name.lower() == "soffice.exe":
        user_dir = entry.parent.parent / "user"
        posix = user_dir.as_posix()
        if len(posix) > 1 and posix[1] == ":":
            posix = posix[0].lower() + posix[1:]
        extra.append(f"-env:UserInstallation=file:///{posix}")

    # .exe / .com / 无扩展名：直接执行
    return [str(entry), *extra, *args], workdir


def launch_detached(entry: Path, args: list[str], admin: bool = False) -> tuple[bool, str]:
    """启动一个工具进程。返回 (成功, 说明)。"""
    if not entry.exists():
        return False, f"入口文件不存在：{entry}"

    # 网页类工具：用默认浏览器打开（不需要提权，也不占用子进程）
    if entry.suffix.lower() in _HTML_SUFFIXES:
        ok, message = open_in_explorer(entry)
        return (True, "已在浏览器中打开") if ok else (False, message)

    cmd, workdir = resolve_command(entry, args)

    if admin and os.name == "nt":
        return _run_as_admin(cmd, workdir)

    try:
        subprocess.Popen(
            cmd,
            cwd=str(workdir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=DETACHED_FLAGS,
        )
    except FileNotFoundError:
        return False, f"无法执行：{cmd[0]}"
    except PermissionError:
        return False, f"没有权限执行：{cmd[0]}"
    except OSError as exc:
        return False, f"启动失败：{exc}"

    return True, "已启动"


def _run_as_admin(cmd: list[str], workdir: Path) -> tuple[bool, str]:
    """Windows UAC 提权启动。"""
    if os.name != "nt":
        return False, "当前平台不支持提权启动"

    import ctypes

    params = subprocess.list2cmdline(cmd[1:])
    ret = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
        None,
        "runas",
        cmd[0],
        params,
        str(workdir),
        1,  # SW_SHOWNORMAL
    )
    # ShellExecute 返回值 <= 32 表示失败
    if ret <= 32:
        if ret == 1223:  # ERROR_CANCELLED
            return False, "已取消提权请求"
        return False, f"提权失败（错误码 {ret}）"
    return True, "已请求管理员权限启动"


def open_in_explorer(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, f"路径不存在：{path}"
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        return False, f"打开失败：{exc}"
    return True, "已打开"


def is_executable(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in _EXECUTABLE_SUFFIXES
