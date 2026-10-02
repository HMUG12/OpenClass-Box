"""
一键体检 —— 面向课堂一体机的离线自检。

设计目标：老师按一个按钮，得到「哪里有问题 + 该怎么处理」的大白话结论。
原则：
  - 全部检查离线执行，不依赖外网；
  - 单项失败只标记该项，不中断整体体检；
  - 检测不确定时不误报（宁可标"无法检测"也不吓人）。
"""
from __future__ import annotations

import ctypes
import socket
from typing import Any

try:
    import psutil
except ImportError:  # 理论上打包一定带 psutil，这里兜底
    psutil = None


def _check_network() -> dict[str, Any]:
    """网卡是否已连接且拿到有效 IPv4 地址。"""
    ok = False
    detail = "未检测到已连接的网卡"
    if psutil:
        try:
            for name, stat in psutil.net_if_stats().items():
                if not stat.isup:
                    continue
                for addr in psutil.net_if_addrs().get(name, []):
                    if addr.family == socket.AF_INET and not addr.address.startswith("169.254"):
                        ok = True
                        detail = f"已连接：{name}（{addr.address}）"
                        break
                if ok:
                    break
        except OSError:
            pass
    return {
        "key": "network",
        "name": "网络连接",
        "ok": ok,
        "detail": detail,
        "suggest": "" if ok else "检查网线 / Wi-Fi 是否连接，或点击「一键修复」→「重置网络」",
    }


def _check_sound() -> dict[str, Any]:
    """是否存在可用的音频输出设备（winmm）。"""
    try:
        count = ctypes.windll.winmm.waveOutGetNumDevs()
        ok = count > 0
        detail = f"检测到 {count} 个音频输出设备" if ok else "未检测到音频输出设备"
    except (AttributeError, OSError):
        ok, detail = True, "（无法检测音频设备，已跳过）"
    return {
        "key": "sound",
        "name": "声音设备",
        "ok": ok,
        "detail": detail,
        "suggest": "" if ok else "检查音响/音频线是否插好；若无声音可尝试「一键修复」→「重启音频服务」",
    }


def _check_display() -> dict[str, Any]:
    """显示器数量与主屏分辨率。"""
    try:
        monitors = ctypes.windll.user32.GetSystemMetrics(80)  # SM_CMONITORS
        width = ctypes.windll.user32.GetSystemMetrics(0)
        height = ctypes.windll.user32.GetSystemMetrics(1)
        ok = monitors >= 1
        detail = f"{monitors} 个显示设备，主屏 {width}×{height}"
    except (AttributeError, OSError):
        ok, detail = True, "（无法检测显示设备，已跳过）"
    return {
        "key": "display",
        "name": "显示 / 投影",
        "ok": ok,
        "detail": detail,
        "suggest": "" if ok else "检查一体机屏幕与投影仪的连线是否松动",
    }


def _check_disk() -> dict[str, Any]:
    """C 盘剩余空间。"""
    ok, detail, suggest = True, "磁盘空间正常", ""
    if psutil:
        try:
            usage = psutil.disk_usage("C:\\")
            ok = usage.percent < 90
            detail = f"C 盘剩余 {usage.free / 1024 ** 3:.1f} GB（已用 {usage.percent:.1f}%）"
            suggest = "" if ok else "空间不足，建议点击「一键修复」→「清理临时文件」"
        except OSError:
            pass
    return {"key": "disk", "name": "磁盘空间", "ok": ok, "detail": detail, "suggest": suggest}


def _check_memory() -> dict[str, Any]:
    """内存占用。"""
    ok, detail, suggest = True, "内存正常", ""
    if psutil:
        mem = psutil.virtual_memory()
        ok = mem.percent < 90
        detail = f"内存占用 {mem.percent:.0f}%（剩余 {mem.available / 1024 ** 3:.1f} GB）"
        suggest = "" if ok else "内存不足，请关闭不用的软件后再上课"
    return {"key": "memory", "name": "内存", "ok": ok, "detail": detail, "suggest": suggest}


def run_checks() -> dict[str, Any]:
    """执行全部体检项。返回 items 列表与汇总（全部离线）。"""
    items = [
        _check_network(),
        _check_sound(),
        _check_display(),
        _check_disk(),
        _check_memory(),
    ]
    ok_count = sum(1 for i in items if i["ok"])
    # 统一结果（并列产物，旧字段一律保留）：界面卡片 / 报修报告 / 远程汇总
    # 从此共用同一形状，前端不必为每种检测各写一遍渲染
    from .diag_result import from_health, summarize

    unified = from_health(items)
    return {
        "items": items,
        "okCount": ok_count,
        "total": len(items),
        "healthy": ok_count == len(items),
        "unified": unified,
        "unifiedSummary": summarize(unified),
    }
