"""
局域网放行 —— 一次把需要互通的端口都开好。

为什么要有它：Windows 防火墙默认**拦截入站连接**，于是出现「本机能打开
127.0.0.1 的服务，同网段的手机或另一台电脑却连不上」。手机控制台（38610）、
临时传输（38620）、机房协同（38900）以及局域网发现用的 UDP 端口都属于这
一类问题 —— 之前只放行了手机控制台一个端口，所以临时传输在同一个网段里
也用不了。

规则只对「专用 / 域」网络放行，公共 WiFi（咖啡厅、酒店这类）依旧关闭，
避免在不受信任的网络里把服务暴露出去。
"""
from __future__ import annotations

import os
import subprocess
from typing import Any

RULE_TCP = "OpenClass-Box 局域网服务"
RULE_UDP = "OpenClass-Box 局域网发现"

# 需要放行的 TCP 端口：手机控制台 / 临时传输 / 机房协同
TCP_PORTS = (38610, 38620, 38900)


def _discovery_port() -> int:
    try:
        from ..net.proto import DISCOVERY_PORT

        return int(DISCOVERY_PORT)
    except Exception:
        return 38901


def _run(args: list[str], timeout: float = 15.0) -> str:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return ""
    for encoding in ("gbk", "utf-8", "latin-1"):
        try:
            return proc.stdout.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def status() -> dict[str, Any]:
    """检查放行状态（只读，不申请管理员）。"""
    if os.name != "nt":
        return {"supported": False, "allowed": True, "message": "非 Windows 系统无需放行", "rules": []}

    tcp_text = _run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={RULE_TCP}"])
    udp_text = _run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={RULE_UDP}"])
    tcp_ok = RULE_TCP in tcp_text
    udp_ok = RULE_UDP in udp_text
    allowed = tcp_ok and udp_ok

    return {
        "supported": True,
        "allowed": allowed,
        "tcp": tcp_ok,
        "udp": udp_ok,
        "ports": list(TCP_PORTS),
        "discoveryPort": _discovery_port(),
        "rules": [RULE_TCP, RULE_UDP],
        "message": "已放行（手机、临时传输、机房协同都可用）"
        if allowed
        else "未放行：同网段的手机 / 其他电脑会连不上（防火墙默认拦截入站）",
    }


def allow() -> dict[str, Any]:
    """添加（或更新）放行规则 —— 需要管理员，弹 UAC 确认。"""
    if os.name != "nt":
        return {"ok": False, "message": "仅支持 Windows"}

    ports = ",".join(str(p) for p in TCP_PORTS)
    udp_port = _discovery_port()
    # 先删旧规则再加新的：保证端口列表始终是最新的（升级后端口变了也能生效）
    command = (
        f'netsh advfirewall firewall delete rule name="{RULE_TCP}" >nul 2>nul & '
        f'netsh advfirewall firewall delete rule name="{RULE_UDP}" >nul 2>nul & '
        f'netsh advfirewall firewall add rule name="{RULE_TCP}" dir=in action=allow '
        f"protocol=TCP localport={ports} profile=private,domain & "
        f'netsh advfirewall firewall add rule name="{RULE_UDP}" dir=in action=allow '
        f"protocol=UDP localport={udp_port} profile=private,domain"
    )
    try:
        import ctypes

        ret = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            None, "runas", "cmd.exe", f"/c {command}", None, 0
        )
    except Exception as exc:
        return {"ok": False, "message": f"提权失败：{exc}"}
    if ret <= 32:
        return {"ok": False, "message": "已取消或提权失败（需要管理员同意）"}
    return {
        "ok": True,
        "message": (
            f"已请求放行：TCP {ports} 与 UDP {udp_port}（仅专用/域网络）。"
            "请在系统弹窗中确认，几秒后点「刷新状态」查看结果。"
        ),
        "ports": list(TCP_PORTS),
    }


def revoke() -> dict[str, Any]:
    """撤销放行（把口子关回去）。"""
    if os.name != "nt":
        return {"ok": False, "message": "仅支持 Windows"}
    command = (
        f'netsh advfirewall firewall delete rule name="{RULE_TCP}" >nul 2>nul & '
        f'netsh advfirewall firewall delete rule name="{RULE_UDP}" >nul 2>nul & echo done'
    )
    try:
        import ctypes

        ret = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            None, "runas", "cmd.exe", f"/c {command}", None, 0
        )
    except Exception as exc:
        return {"ok": False, "message": f"提权失败：{exc}"}
    if ret <= 32:
        return {"ok": False, "message": "已取消或提权失败"}
    return {"ok": True, "message": "已撤销局域网放行规则"}
