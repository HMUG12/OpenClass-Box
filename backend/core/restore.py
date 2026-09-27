"""
系统还原点 —— 查看与创建（创建需要管理员权限，走 UAC 确认）。

课堂场景：电教在装软件/改设置之前先建一个还原点，出问题可以回滚。
注意：Windows 对还原点有频率限制（默认 24 小时内只允许创建一个），
这是系统行为，本工具如实反馈系统返回的信息。
"""
from __future__ import annotations

import ctypes
import json
import subprocess
from pathlib import Path
from typing import Any

from .paths import config_dir

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_PS = ["powershell", "-NoProfile", "-Command"]


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _run_elevated(command: str) -> bool:
    """以管理员身份运行 PowerShell（弹 UAC）。"""
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "powershell.exe", f'-NoProfile -Command "{command}"', None, 1
        )
        return int(rc) > 32
    except (AttributeError, OSError):
        return False


def list_points() -> dict[str, Any]:
    """列出系统还原点（只读查询）。"""
    script = (
        "Get-ComputerRestorePoint | "
        "Select-Object SequenceNumber,Description,CreationTime | "
        "ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(
            [*_PS, script], capture_output=True, timeout=60, creationflags=_CREATE_NO_WINDOW
        )
        text = proc.stdout.decode("gbk", "ignore").strip()
        if not text:
            return {"ok": True, "points": [], "message": "当前没有可用的还原点"}
        data = json.loads(text)
        points = data if isinstance(data, list) else [data]
        return {"ok": True, "points": points, "message": ""}
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {"ok": False, "points": [], "message": f"读取失败：{exc}"}


def protection_enabled() -> bool | None:
    """系统保护是否开启（只读注册表；None 表示读不到）。"""
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore",
        )
        try:
            disabled = int(winreg.QueryValueEx(key, "DisableSR")[0])
        except OSError:
            disabled = 0
        key.Close()
        return disabled == 0
    except (ImportError, OSError):
        return None


def _result_path() -> Path:
    folder = config_dir() / "restore"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "last_result.txt"


def create_point(description: str = "") -> dict[str, Any]:
    """创建系统还原点。

    三个最容易踩的坑，这里都提前处理或说清楚：
      1. **命令写法**：旧的实现把 PS 命令塞进 -Command "..."，中文描述与嵌套
         引号会被解析得乱七八糟（这就是「创建有异常」的主因）。现在改成写
         临时 .ps1 文件再执行，输出重定向到文件后读回来；
      2. **系统保护未开**：很多一体机 / 精简系统默认关闭系统保护，
         Checkpoint-Computer 必然失败 —— 先检测，并直接告诉用户去哪儿开；
      3. **24 小时限制**：Windows 默认 24 小时只允许建一个还原点，
         失败信息里会明确写出来，免得以为是本工具的毛病。
    """
    # 描述只保留安全字符，避免命令注入
    desc = "".join(
        ch for ch in (description or "OpenClass-Box 手动还原点") if ch.isalnum() or ch in "-_ 中文"
    )[:60] or "OpenClass-Box 手动还原点"

    enabled = protection_enabled()
    if enabled is False:
        return {
            "ok": False,
            "message": "系统保护未开启：请先到「此电脑 → 属性 → 系统保护」给系统盘开启保护，再回来创建还原点",
            "protection": False,
        }

    folder = config_dir() / "restore"
    folder.mkdir(parents=True, exist_ok=True)
    script_path = folder / "create_point.ps1"
    out_path = _result_path()
    script = (
        "$ErrorActionPreference='Stop'\r\n"
        "try {\r\n"
        f"  Checkpoint-Computer -Description '{desc}' -RestorePointType 'MODIFY_SETTINGS'\r\n"
        "  'OK 还原点创建成功'\r\n"
        "} catch {\r\n"
        "  'ERR ' + $_.Exception.Message\r\n"
        "}\r\n"
    )
    try:
        # 带 BOM：PowerShell 读中文脚本时才不会乱码
        script_path.write_text(script, encoding="utf-8-sig")
    except OSError as exc:
        return {"ok": False, "message": f"准备脚本失败：{exc}"}

    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)]

    def _interpret(text: str) -> dict[str, Any]:
        text = (text or "").strip()
        if "OK" in text and "ERR" not in text:
            return {"ok": True, "message": "还原点创建成功", "detail": text[:300]}
        lowered = text.lower()
        if "24" in text or "时间间隔" in text or "frequency" in lowered or "already" in lowered:
            return {
                "ok": False,
                "message": "系统限制：24 小时内只能创建一个还原点，请明天再试",
                "detail": text[:300],
            }
        if "not enabled" in lowered or "未启用" in text or "disabled" in lowered:
            return {
                "ok": False,
                "message": "系统保护未启用：请先在系统属性里开启「系统保护」",
                "detail": text[:300],
            }
        return {
            "ok": False,
            "message": f"创建失败：{text[:180] or '系统拒绝了本次操作'}",
            "detail": text[:300],
        }

    if _is_admin():
        try:
            proc = subprocess.run(
                args, capture_output=True, timeout=300, creationflags=_CREATE_NO_WINDOW
            )
            text = proc.stdout.decode("gbk", "ignore")
            if not text.strip():
                text = proc.stderr.decode("gbk", "ignore")
            result = _interpret(text)
            try:
                out_path.write_text(text, encoding="utf-8")
            except OSError:
                pass
            return result
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "message": f"创建失败：{exc}"}

    # 非管理员：提权执行（异步），输出写文件，用户可点「查看上次结果」拿回执
    try:
        ret = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            "cmd.exe",
            f'/c powershell -NoProfile -ExecutionPolicy Bypass -File "{script_path}" > "{out_path}" 2>&1',
            str(folder),
            0,
        )
    except Exception as exc:
        return {"ok": False, "message": f"提权失败：{exc}"}
    if ret <= 32:
        return {"ok": False, "message": "已取消提权（创建还原点需要管理员权限）"}
    return {
        "ok": True,
        "async": True,
        "message": "已请求管理员权限：请在系统弹窗中确认，约 10 秒后点「查看上次结果」",
    }


def last_result() -> dict[str, Any]:
    """读取上一次（提权）创建还原点的回执。"""
    path = _result_path()
    if not path.exists():
        return {"ok": False, "message": "还没有可查看的结果"}
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        return {"ok": False, "message": f"读取失败：{exc}"}
    if not text:
        return {"ok": False, "message": "还没有结果（可能仍在执行，稍等再点一次）"}

    lowered = text.lower()
    if "OK" in text and "ERR" not in text:
        return {"ok": True, "message": "还原点创建成功", "detail": text[:400]}
    if "24" in text or "时间间隔" in text or "frequency" in lowered:
        return {"ok": False, "message": "系统限制：24 小时内只能创建一个还原点", "detail": text[:400]}
    return {"ok": False, "message": f"创建失败：{text[:200]}", "detail": text[:400]}
