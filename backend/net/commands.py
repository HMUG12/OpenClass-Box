"""
远程动作执行 —— 学生机收到老师机指令后在本机做什么。

三条原则：
  1. **复用单机能力**：体检、修复、清理等全部调用 core 下已有实现，
     保证「远程做的」和「本机点的」结果完全一致；
  2. **跨平台**：关机 / 重启 / 锁屏等系统动作按平台分支（Windows 用
     shutdown 命令，Linux / 国产系统用 systemctl / loginctl）；
  3. **结构化回报**：每个动作都返回 {ok, message, data}，
     由客户端统一回传给老师机展示。
"""
from __future__ import annotations

import platform
import subprocess
import time
from pathlib import Path
from typing import Any

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _windows() -> bool:
    return platform.system() == "Windows"


# ══════════════════════════════════════════════════════════════
# 体检 / 修复 / 清理
# ══════════════════════════════════════════════════════════════

def run_checkup() -> dict[str, Any]:
    from ..core.health import run_checks

    result = run_checks()
    items = result.get("items", [])
    return {
        "ok": True,
        "message": f"体检完成：{result.get('okCount', 0)}/{result.get('total', 0)} 项正常",
        "data": {"items": items, "healthy": result.get("healthy")},
    }


def run_repair(key: str = "") -> dict[str, Any]:
    from ..core.repair import list_repairs, run_repair as _repair

    if key:
        result = _repair(key)
        return {
            "ok": bool(result.get("ok")),
            "message": str(result.get("message") or ""),
            "data": {"restart": bool(result.get("restart"))},
        }

    details: list[str] = []
    for item in list_repairs():
        try:
            result = _repair(item["key"])
            details.append(f"{item['name']}：{'成功' if result.get('ok') else '未执行'}")
        except Exception as exc:  # 单项失败不影响其余
            details.append(f"{item['name']}：失败({exc})")
    return {"ok": True, "message": "；".join(details)[:400], "data": {}}


def run_cleanup(keys: list[str] | None = None) -> dict[str, Any]:
    from ..core.cleanup import analyze, run

    if not keys:
        analysis = analyze()
        keys = [
            item["key"]
            for item in analysis.get("items", [])
            if item.get("exists") and item.get("size", 0) > 0
        ]
    if not keys:
        return {"ok": True, "message": "没有可清理的项目", "data": {}}

    result = run(keys)
    freed = float(result.get("freed") or 0)
    return {
        "ok": True,
        "message": f"清理完成，释放 {freed / 1048576:.1f} MB",
        "data": {"freed": freed, "details": list(result.get("details") or [])[:6]},
    }


# ══════════════════════════════════════════════════════════════
# 进程 / 电源 / 消息
# ══════════════════════════════════════════════════════════════

def kill_process(pid: int) -> dict[str, Any]:
    from ..core.procs import kill_process as _kill

    result = _kill(int(pid))
    return {
        "ok": bool(result.get("ok")),
        "message": str(result.get("message") or ""),
        "data": {},
    }


def lock_screen() -> dict[str, Any]:
    """锁屏（不同于关机类动作，随点随生效，风险低）。"""
    try:
        if _windows():
            subprocess.Popen(
                ["rundll32.exe", "user32.dll,LockWorkStation"],
                creationflags=_CREATE_NO_WINDOW,
            )
            return {"ok": True, "message": "已锁定屏幕", "data": {}}
        subprocess.Popen(["loginctl", "lock-session"])
        return {"ok": True, "message": "已锁定屏幕", "data": {}}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "message": f"锁屏失败：{exc}", "data": {}}


def power(mode: str, delay: int = 30, source: str = "") -> dict[str, Any]:
    """关机 / 重启 / 睡眠 / 休眠 / 取消关机。

    受「允许远程电源控制」开关约束（默认关闭）；只有「取消关机」始终放行 ——
    误操作时总得有退路。真正的命令构造与执行在 core.power。
    """
    from ..core import power as power_core

    action = (mode or "").lower()
    if action == "lock":
        return lock_screen()
    if action != "cancel" and not power_core.allowed():
        return {
            "ok": False,
            "message": "这台机器未开启远程电源控制（需在「设置 → 机房管理」里开启）",
            "data": {},
        }
    return power_core.execute(action, int(delay or 30), source)


def message(text: str) -> dict[str, Any]:
    """在学生机弹出一条提示（Windows 用 MessageBox，Linux 用 notify-send）。"""
    text = (text or "老师机消息")[:200]
    try:
        if _windows():
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, "老师机消息", 0x40)
            return {"ok": True, "message": "消息已弹出", "data": {}}
        subprocess.Popen(["notify-send", "老师机消息", text])
        return {"ok": True, "message": "消息已弹出", "data": {}}
    except (OSError, AttributeError) as exc:
        return {"ok": False, "message": f"弹窗失败：{exc}", "data": {}}


def wallpaper(path: str) -> dict[str, Any]:
    """统一换壁纸（路径为学生机上已存在的文件）。"""
    from ..core.wallpaper import set_wallpaper

    result = set_wallpaper(path)
    return {
        "ok": bool(result.get("ok")),
        "message": str(result.get("message") or ""),
        "data": {},
    }


# ══════════════════════════════════════════════════════════════
# 文件回收（收作业）
# ══════════════════════════════════════════════════════════════

def collect_files(paths: list[str], limit_mb: int = 200) -> dict[str, Any]:
    """把指定目录下的文件打包为 zip（供老师机拉取）。

    安全约束：只读指定的目录、单次总量上限，避免误操作大目录。
    """
    import tempfile
    import zipfile

    total = 0
    limit = limit_mb * 1024 * 1024
    collected: list[str] = []

    tmp = Path(tempfile.gettempdir()) / f"oc_collect_{int(time.time())}.zip"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
            for raw in paths[:10]:
                base = Path(raw)
                if not base.exists():
                    continue
                files = [base] if base.is_file() else [
                    p for p in base.rglob("*") if p.is_file()
                ]
                for file in files[:2000]:
                    try:
                        size = file.stat().st_size
                    except OSError:
                        continue
                    if total + size > limit:
                        break
                    archive.write(file, arcname=file.name)
                    total += size
                    collected.append(file.name)
    except (OSError, zipfile.BadZipFile) as exc:
        return {"ok": False, "message": f"打包失败：{exc}", "data": {}}

    if not collected:
        return {"ok": False, "message": "指定位置没有可回收的文件", "data": {}}
    return {
        "ok": True,
        "message": f"已打包 {len(collected)} 个文件（{total / 1048576:.1f} MB）",
        "data": {"zip": str(tmp), "count": len(collected), "size": total},
    }


# ══════════════════════════════════════════════════════════════
# 分发
# ══════════════════════════════════════════════════════════════

def execute(action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """执行一条远程指令（客户端调用）。"""
    payload = payload or {}
    try:
        if action == "checkup":
            return run_checkup()
        if action == "repair":
            return run_repair(str(payload.get("key") or ""))
        if action == "cleanup":
            keys = payload.get("keys")
            return run_cleanup([str(k) for k in keys] if isinstance(keys, list) else None)
        if action == "kill":
            return kill_process(int(payload.get("pid") or 0))
        if action == "power":
            return power(
                str(payload.get("mode") or ""),
                int(payload.get("delay") or 30),
                str(payload.get("source") or ""),
            )
        if action == "message":
            return message(str(payload.get("text") or ""))
        if action == "wallpaper":
            return wallpaper(str(payload.get("path") or ""))
        if action == "pull_file":
            paths = payload.get("paths")
            return collect_files([str(p) for p in paths] if isinstance(paths, list) else [])
    except Exception as exc:  # 任何异常都转成结构化结果，避免客户端中断
        return {"ok": False, "message": f"执行异常：{exc}", "data": {}}
    return {"ok": False, "message": f"不支持的指令：{action}", "data": {}}
