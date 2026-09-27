"""
运行日志 —— 把异常与关键事件落盘，方便反馈问题时定位。

为什么需要：教室现场的问题往往「说不清」（偶发、重启后消失），有一份本地
日志就能直接看到出错的位置与时间。日志只写在程序数据目录，不采集任何个人
信息，并且做了大小轮转（默认 1 MB × 3 份），不会无限增长。
"""
from __future__ import annotations

import datetime as _dt
import platform
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

from .paths import config_dir

_MAX_BYTES = 1024 * 1024
_KEEP = 3
_lock = threading.RLock()


def log_dir() -> Path:
    folder = config_dir() / "logs"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return folder


def log_file() -> Path:
    return log_dir() / "app.log"


def _rotate() -> None:
    path = log_file()
    try:
        if not path.exists() or path.stat().st_size < _MAX_BYTES:
            return
    except OSError:
        return
    for index in range(_KEEP - 1, 0, -1):
        source = path.with_name(f"app.log.{index}")
        target = path.with_name(f"app.log.{index + 1}")
        if source.exists():
            try:
                source.replace(target)
            except OSError:
                pass
    try:
        path.replace(path.with_name("app.log.1"))
    except OSError:
        pass


def log(message: str, level: str = "INFO", **fields: Any) -> None:
    """写一行日志。失败静默（日志本身绝不能把程序带崩）。"""
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    extra = " ".join(f"{key}={value}" for key, value in fields.items() if value not in (None, ""))
    line = f"[{stamp}] [{level}] {message}" + (f" | {extra}" if extra else "")
    with _lock:
        try:
            _rotate()
            with log_file().open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass


def log_exception(exc_type: type, exc: BaseException, tb: Any, source: str = "") -> None:
    """记录一次异常（含完整堆栈）。"""
    try:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
    except Exception:
        text = f"{exc_type}: {exc}"
    prefix = f"{source} " if source else ""
    log(f"{prefix}出现异常：{exc}", "ERROR")
    with _lock:
        try:
            with log_file().open("a", encoding="utf-8") as handle:
                handle.write(text.rstrip() + "\n")
        except OSError:
            pass


def install_hooks() -> None:
    """把未捕获异常写进日志（主线程 + 子线程），同时保留默认行为。"""

    def _hook(exc_type: type, exc: BaseException, tb: Any) -> None:
        log_exception(exc_type, exc, tb, "主线程")
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook

    if hasattr(threading, "excepthook"):
        def _thread_hook(args: Any) -> None:
            name = getattr(getattr(args, "thread", None), "name", "") or ""
            log_exception(args.exc_type, args.exc_value, args.exc_traceback, f"线程[{name}]")

        threading.excepthook = _thread_hook  # type: ignore[assignment]


def log_startup(version: str = "") -> None:
    log(
        "程序启动",
        version=version,
        python=platform.python_version(),
        system=f"{platform.system()} {platform.release()}",
        frozen=bool(getattr(sys, "frozen", False)),
        pid=__import__("os").getpid(),
    )


def log_exit() -> None:
    log("程序退出", pid=__import__("os").getpid())


def tail(lines: int = 200) -> str:
    """最近若干行日志（供诊断页展示）。"""
    path = log_file()
    if not path.exists():
        return ""
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"（读取日志失败：{exc}）"
    return "\n".join(content[-max(1, lines):])


def stats() -> dict[str, Any]:
    path = log_file()
    size = 0
    try:
        size = path.stat().st_size if path.exists() else 0
    except OSError:
        size = 0
    return {
        "path": str(path),
        "dir": str(log_dir()),
        "size": size,
        "exists": path.exists(),
    }


def clear() -> dict[str, Any]:
    removed = 0
    for path in log_dir().glob("app.log*"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    log("日志已清理", level="INFO")
    return {"ok": True, "message": f"已清理 {removed} 个日志文件"}
