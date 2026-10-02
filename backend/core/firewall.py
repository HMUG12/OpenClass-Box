"""
局域网放行 —— 一次把需要互通的端口都开好。

为什么要有它：Windows 防火墙默认**拦截入站连接**，于是出现「本机能打开
127.0.0.1 的服务，同网段的手机或另一台电脑却连不上」。手机控制台（38610）、
临时传输（38620）、机房协同（38900）以及局域网发现用的 UDP 端口都属于这一
类问题。

这一版修掉了两个会让用户白折腾的点：

  1. **报"成功"了但其实没加上**
     旧实现用 ShellExecuteW 起一个 cmd 就返回，只要进程起得来就报"已请求放行"。
     用户在 UAC 弹窗上点"否"、或 netsh 被安全软件/组策略拦下，界面照样显示成功 ——
     于是反复点、反复连不上，还不知道该找谁。现在把命令写成脚本执行，
     **读回执行结果，并复核规则是否真的存在**。

  2. **规则加了却不生效**
     规则只对「专用 / 域」网络生效，而教室网络经常被 Windows 识别成"公用"
     （新接的网络、换过路由、WiFi 都会）。这种情况放行一百次也没用，
     所以状态里把**网络类别**一并显示出来，并提供「一键改为专用」。

状态检测也从 netsh 换成 PowerShell 的 Get-NetFirewallRule：规则名是中文，
经命令行编码转换后 netsh 有时查不到已存在的规则，会出现"明明放行了却显示未放行"。
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any

from .paths import config_dir

RULE_TCP = "OpenClass-Box 局域网服务"
RULE_UDP = "OpenClass-Box 局域网发现"

# 需要放行的 TCP 端口：手机控制台 / 临时传输 / 机房协同
TCP_PORTS = (38610, 38620, 38900)

_CATEGORY_LABELS = {
    "Private": "专用",
    "Public": "公用",
    "DomainAuthenticated": "域",
}


def _discovery_port() -> int:
    try:
        from ..net.proto import DISCOVERY_PORT

        return int(DISCOVERY_PORT)
    except Exception:
        return 38901


def _run(args: list[str], timeout: float = 20.0) -> str:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return ""
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return proc.stdout.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def _run_ps(script: str, timeout: float = 25.0) -> str:
    return _run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        timeout,
    )


_STATUS_PS = (
    "$ErrorActionPreference='SilentlyContinue';"
    "try{"
    "  $r = @(Get-NetFirewallRule -DisplayName 'OpenClass-Box*' |"
    "        ForEach-Object { @{ enabled = [string]$_.Enabled;"
    "                            profile = [string]$_.Profile;"
    "                            direction = [string]$_.Direction } });"
    "  $cats = @(Get-NetConnectionProfile | Select-Object -ExpandProperty NetworkCategory |"
    "           ForEach-Object { [string]$_ });"
    "  @{ rules = $r; categories = $cats } | ConvertTo-Json -Depth 5 -Compress"
    "}catch{ '{}' }"
)


def _runas(target: str, params: str) -> int:
    """以管理员身份启动；返回 ShellExecute 的返回值（<=32 表示被取消或失败）。

    单独抽出来是为了可测试：这条路径"用户点了否"与"真的执行失败"必须能区分，
    而那正是旧版本"显示成功但其实没放行"的根源。
    """
    try:
        import ctypes

        return int(ctypes.windll.shell32.ShellExecuteW(None, "runas", target, params, None, 0))
    except Exception:  # noqa: BLE001
        return 0


def _snapshot() -> dict[str, Any]:
    """读取当前规则与网络类别（只读，不需要管理员）。"""
    if os.name != "nt":
        return {"rules": [], "categories": []}
    text = _run_ps(_STATUS_PS)
    start = text.find("{")
    if start < 0:
        return {"rules": [], "categories": []}
    try:
        data = json.loads(text[start:])
    except ValueError:
        return {"rules": [], "categories": []}
    if not isinstance(data, dict):
        return {"rules": [], "categories": []}
    rules = data.get("rules") or []
    if isinstance(rules, dict):
        rules = [rules]
    cats = data.get("categories") or []
    if isinstance(cats, str):
        cats = [cats]
    return {
        "rules": [item for item in rules if isinstance(item, dict)],
        "categories": [str(item) for item in cats],
    }


def status() -> dict[str, Any]:
    """检查放行状态：规则在不在、网络是不是"公用"（后者会让规则不生效）。"""
    if os.name != "nt":
        return {"supported": False, "allowed": True, "message": "非 Windows 系统无需放行", "rules": []}

    snap = _snapshot()
    inbound = [
        item
        for item in snap["rules"]
        if str(item.get("direction", "")).endswith("Inbound")
    ]
    # 两条规则（TCP / UDP）都启用才算放行完整
    enabled = [item for item in inbound if str(item.get("enabled", "")).lower() == "true"]
    allowed = len(enabled) >= 2

    categories = snap["categories"]
    labels = [_CATEGORY_LABELS.get(item, item) for item in categories]
    only_public = bool(categories) and all(item == "Public" for item in categories)
    has_private = any(item in ("Private", "DomainAuthenticated") for item in categories)

    if not allowed:
        message = "未放行：同网段的手机 / 其他电脑会连不上（防火墙默认拦截入站）"
    elif only_public:
        # 规则在、但当前网络是"公用" —— 这是最容易让人白折腾的一种
        message = (
            "已放行，但当前网络被 Windows 识别为「公用」—— 规则只对专用/域网络生效，"
            "所以现在仍然连不上。点「把当前网络改为专用」即可。"
        )
    else:
        message = "已放行（手机控制台、临时传输、机房协同都可用）"

    return {
        "supported": True,
        "allowed": allowed,
        "ruleCount": len(inbound),
        "enabledCount": len(enabled),
        "ports": list(TCP_PORTS),
        "discoveryPort": _discovery_port(),
        "rules": [RULE_TCP, RULE_UDP],
        "categories": labels,
        "rawCategories": categories,
        "onlyPublic": only_public,
        "hasPrivate": has_private,
        "message": message,
    }


# ── 放行（需要管理员）────────────────────────────────────────


def _script_path() -> Any:
    return config_dir() / "_allow_lan.bat"


def _result_path() -> Any:
    return config_dir() / "_allow_lan.result"


def allow() -> dict[str, Any]:
    """添加（或更新）放行规则，并**确认是否真的加上**。

    做法：把 netsh 命令写成一个临时脚本 → 提权执行 → 轮询结果文件 →
    再用 status() 复核。只有复核通过才报成功；被取消、被拦截、部分失败都如实说明。
    """
    if os.name != "nt":
        return {"ok": False, "message": "仅支持 Windows"}

    ports = ",".join(str(p) for p in TCP_PORTS)
    udp_port = _discovery_port()
    result = _result_path()
    try:
        result.unlink()
    except OSError:
        pass

    # cmd 默认按本地代码页读脚本：这里显式用 gbk 写，避免中文规则名变乱码
    lines = [
        "@echo off",
        f'echo START>"{result}"',
        f'netsh advfirewall firewall delete rule name="{RULE_TCP}" >nul 2>&1',
        f'netsh advfirewall firewall delete rule name="{RULE_UDP}" >nul 2>&1',
        f'netsh advfirewall firewall add rule name="{RULE_TCP}" dir=in action=allow '
        f"protocol=TCP localport={ports} profile=private,domain >>\"{result}\" 2>&1",
        f'netsh advfirewall firewall add rule name="{RULE_UDP}" dir=in action=allow '
        f"protocol=UDP localport={udp_port} profile=private,domain >>\"{result}\" 2>&1",
        f'echo DONE>>"{result}"',
    ]
    script = _script_path()
    try:
        script.write_text("\r\n".join(lines) + "\r\n", encoding="gbk")
    except OSError as exc:
        return {"ok": False, "message": f"无法写入临时脚本：{exc}"}

    if _runas("cmd.exe", f'/c "{script}"') <= 32:
        return {"ok": False, "message": "已取消或提权失败（需要管理员同意）"}

    # 等脚本写完结果：UAC 弹窗需要用户点一下，最多给 30 秒
    detail = ""
    for _ in range(60):
        time.sleep(0.5)
        try:
            detail = result.read_text(encoding="gbk", errors="ignore")
        except OSError:
            continue
        if "DONE" in detail:
            break

    check = status()
    try:
        script.unlink()
        result.unlink()
    except OSError:
        pass

    if check.get("allowed"):
        extra = ""
        if check.get("onlyPublic"):
            extra = (
                "（注意：当前网络是「公用」，规则不会生效 —— 点「把当前网络改为专用」）"
            )
        return {
            "ok": True,
            "message": f"已放行 TCP {ports} 与 UDP {udp_port}，规则已确认存在{extra}",
            "ports": list(TCP_PORTS),
            "status": check,
        }

    # 没成功：把脚本输出里的关键行带出来，别只说一句"失败"
    hint = ""
    for line in (detail or "").splitlines():
        low = line.strip().lower()
        if low and low not in ("start", "done") and "ok" not in low[:3]:
            hint = line.strip()
            break
    return {
        "ok": False,
        "message": (
            f"放行未生效{('：' + hint) if hint else ''}。"
            "常见原因：UAC 弹窗被取消、或安全软件/组策略拦截了防火墙修改。"
        ),
        "status": check,
    }


def set_private() -> dict[str, Any]:
    """把当前网络改为「专用」—— 规则只对专用/域生效，而教室网络常被识别成公用。"""
    if os.name != "nt":
        return {"ok": False, "message": "仅支持 Windows"}

    command = "Get-NetConnectionProfile | Set-NetConnectionProfile -NetworkCategory Private"
    if _runas("powershell.exe", f'-NoProfile -Command "{command}"') <= 32:
        return {"ok": False, "message": "已取消或提权失败（需要管理员同意）"}

    # 等用户点完 UAC 再复核
    for _ in range(30):
        time.sleep(0.5)
        if status().get("hasPrivate"):
            break
    check = status()
    if check.get("hasPrivate"):
        return {"ok": True, "message": "已把当前网络改为「专用」，规则现在会生效", "status": check}
    return {
        "ok": False,
        "message": "未确认成功（可能取消了 UAC）。也可手动改：设置 → 网络和 Internet → 属性 → 网络配置文件",
        "status": check,
    }


def revoke() -> dict[str, Any]:
    """撤销放行（把口子关回去）。"""
    if os.name != "nt":
        return {"ok": False, "message": "仅支持 Windows"}
    command = (
        f'netsh advfirewall firewall delete rule name="{RULE_TCP}" >nul 2>nul & '
        f'netsh advfirewall firewall delete rule name="{RULE_UDP}" >nul 2>nul & echo done'
    )
    if _runas("cmd.exe", f"/c {command}") <= 32:
        return {"ok": False, "message": "已取消或提权失败"}
    return {"ok": True, "message": "已撤销局域网放行规则（几秒后点「刷新状态」确认）"}
