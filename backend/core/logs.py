"""
诊断日志采集 —— 一键把系统日志与设备信息打包，便于远程报修。

产出：桌面上一个 zip 诊断包，内含：
  - system-info.txt                     本机硬件/系统信息（真实采集）
  - health.txt                          体检结果
  - event-application.txt / event-system.txt   最近的系统事件日志

原则：只读不写系统；单项采集失败只记录说明，不中断整包生成。
"""
from __future__ import annotations

import datetime as _dt
import os
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from .health import run_checks
from .report import collect as collect_system, render_text

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_PS = ["powershell", "-NoProfile", "-Command"]
_MAX_EVENTS = 300


def _decode(raw: bytes) -> str:
    """PowerShell 输出解码：优先 UTF-8，回退 GBK。"""
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def _event_log_text(name: str) -> str:
    """导出最近的事件日志为文本（只读）。"""
    script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        f"Get-WinEvent -LogName {name} -MaxEvents {_MAX_EVENTS} -ErrorAction SilentlyContinue | "
        "Select-Object TimeCreated,LevelDisplayName,ProviderName,Id,Message | "
        "Format-List | Out-String -Width 300"
    )
    try:
        proc = subprocess.run(
            [*_PS, script], capture_output=True, timeout=150, creationflags=_CREATE_NO_WINDOW
        )
        text = _decode(proc.stdout).strip()
        return text or f"（未能读取 {name} 日志：可能被组策略限制）"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"（读取 {name} 日志失败：{exc}）"


def _scrub(text: str) -> str:
    """脱敏：把用户名与用户目录换成占位符。

    诊断包十有八九要发给别人（维修人员、群里、老师），而系统信息与
    事件日志里天然带着 `C:\\Users\\张三\\...` 这类路径 —— 这是老师的真名。
    这里只做**精确替换**（路径里的用户名、用户名@主机名），
    不按子串乱替，避免把 "Administrator" 之类的词误伤。
    """
    import re

    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    home = os.path.expanduser("~")
    if home and home not in ("~", "/", "\\"):
        text = text.replace(home, r"%USERPROFILE%")
    if not user or len(user) < 3:
        return text

    escaped = re.escape(user)
    rules = (
        (rf"(?i)([A-Za-z]:\\Users\\){escaped}\b", r"\1<用户>"),
        (rf"(?i)(\\\\Users\\){escaped}\b", r"\1<用户>"),
        (rf"(?i)\b{escaped}@", "<用户>@"),
    )
    for pattern, replacement in rules:
        text = re.sub(pattern, replacement, text)
    return text


_PRIVACY_NOTE = """OpenClass-Box 诊断包 —— 内容与隐私说明

本包用于向他人（维修人员 / 管理员）说明本机状况，内含：
  · system-info.txt     系统与硬件信息（版本、CPU、内存、磁盘、网络配置）
  · health.txt          一键体检结果
  · event-application.txt / event-system.txt   系统事件日志（Application / System）

已做脱敏：
  · 用户目录被替换为 %USERPROFILE%（例如 C:\\Users\\<用户>\\Desktop）
  · 路径中的用户名与「用户名@主机名」被替换为 <用户>
  · **不包含**任何密码、访问码、配对码、浏览器历史或文档内容

请注意：
  事件日志里仍可能保留进程路径、软件名与设备型号等中性信息；
  若仍不放心，可在发送前自行打开本包检查、删除不必要的内容。
"""


def export() -> dict[str, Any]:
    """生成诊断包到桌面，返回 {ok, path, size, parts, message}。"""
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    desktop = Path(os.path.expanduser("~")) / "Desktop"
    if not desktop.is_dir():
        desktop = Path(os.path.expanduser("~"))
    zip_path = desktop / f"OpenClass-Box诊断包_{stamp}.zip"

    parts: list[str] = []
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            # 0) 隐私说明：诊断包经常要发给别人，先说清楚含什么、不含什么
            archive.writestr("隐私说明.txt", _PRIVACY_NOTE)

            # 1) 系统信息（脱敏后写入）
            try:
                archive.writestr("system-info.txt", _scrub(render_text(collect_system())))
                parts.append("系统信息")
            except (OSError, ValueError) as exc:
                archive.writestr("system-info.txt", f"采集失败：{exc}")

            # 2) 体检结果
            try:
                health = run_checks()
                lines = [
                    f"{'正常' if item['ok'] else '异常'}｜{item['name']}：{item['detail']}"
                    for item in health["items"]
                ]
                archive.writestr("health.txt", _scrub("\n".join(lines)))
                parts.append("体检结果")
            except (OSError, ValueError) as exc:
                archive.writestr("health.txt", f"采集失败：{exc}")

            # 3) 事件日志（脱敏后写入：这里最容易带出用户名与路径）
            for log_name, file_name in (
                ("Application", "event-application.txt"),
                ("System", "event-system.txt"),
            ):
                archive.writestr(file_name, _scrub(_event_log_text(log_name)))
                parts.append(f"{log_name} 日志")

        size = zip_path.stat().st_size
        return {
            "ok": True,
            "path": str(zip_path),
            "size": size,
            "parts": parts,
            "message": "诊断包已生成",
        }
    except (OSError, zipfile.BadZipFile) as exc:
        return {"ok": False, "path": "", "size": 0, "parts": parts, "message": f"生成失败：{exc}"}
