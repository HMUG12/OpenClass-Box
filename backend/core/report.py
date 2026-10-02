"""
报修报告 —— 一键导出本机真实信息，供维修人员快速判断问题。

原则：
  - 内容全部来自真实系统查询（psutil / platform），**不编造任何字段**；
  - 输出纯文本（UTF-8），默认存到桌面，便于发送或打印；
  - 失败时明确返回错误，不产出半份报告。
"""
from __future__ import annotations

import datetime as _dt
import os
import platform
import socket
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:
    psutil = None

from .diag_result import from_health, only_issues, summarize
from .health import run_checks

# 报告里排在最前的两段：维修人员通常只看这两段，硬件明细是备查的
_PRIORITY_KEYS = ("诊断结论", "需要处理")

# 虚拟网络适配器：虚拟机 / Hyper-V / 隧道软件装的。
# 它们会把"这台机器连在哪"淹没在一堆 172.x 地址里，所以标记出来排到后面；
# 但**不能直接丢**——有时问题恰恰出在 VPN 或虚拟网卡上。
_VIRTUAL_ADAPTER_HINTS = (
    "vmware",
    "virtualbox",
    "vbox",
    "hyper-v",
    "vethernet",
    "virtual",
    "tailscale",
    "zerotier",
    "docker",
    "wsl",
    "loopback",
    "npcap",
    "tap-",
    "openvpn",
    "wireguard",
    "easyconnect",
    "sangfor",
    "vpn",
)


def _is_virtual_adapter(name: str) -> bool:
    lowered = (name or "").lower()
    return any(hint in lowered for hint in _VIRTUAL_ADAPTER_HINTS)


def _gb(value: float) -> str:
    return f"{value / 1024 ** 3:.1f} GB"


def _cpu_processor_name() -> str:
    """从注册表取 CPU 型号（platform.processor 在部分机器上为空）。"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        ) as key:
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0])
    except (OSError, ImportError):
        return platform.processor() or "-"


def collect() -> dict[str, Any]:
    """采集本机信息（全部真实查询）。"""
    data: dict[str, Any] = {
        "生成时间": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "系统": {
            "版本": f"{platform.system()} {platform.release()}",
            "内部版本": platform.version(),
            "计算机名": socket.gethostname(),
            "处理器架构": platform.machine(),
        },
    }

    if psutil:
        freq = psutil.cpu_freq()
        data["CPU"] = {
            "型号": _cpu_processor_name(),
            "物理核心 / 逻辑核心": f"{psutil.cpu_count(logical=False)} / {psutil.cpu_count(logical=True)}",
            "当前频率": f"{freq.current:.0f} MHz" if freq else "-",
            "当前占用": f"{psutil.cpu_percent(interval=0.4):.0f}%",
        }

        mem = psutil.virtual_memory()
        data["内存"] = {
            "总量": _gb(mem.total),
            "已用": f"{_gb(mem.used)}（{mem.percent:.0f}%）",
            "可用": _gb(mem.available),
        }

        disks: list[str] = []
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in (part.opts or "") or not part.fstype:
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            disks.append(
                f"{part.mountpoint} {part.fstype}｜总 {_gb(usage.total)} / "
                f"已用 {_gb(usage.used)}（{usage.percent:.0f}%）/ 可用 {_gb(usage.free)}"
            )
        data["磁盘"] = disks

        physical: list[str] = []
        virtual: list[str] = []
        addrs = psutil.net_if_addrs()
        for name, stat in psutil.net_if_stats().items():
            if not stat.isup:
                continue
            ipv4 = [
                a.address
                for a in addrs.get(name, [])
                if a.family == socket.AF_INET and not a.address.startswith("127.")
            ]
            if not ipv4:
                continue
            line = f"{name}：{', '.join(ipv4)}"
            # 物理网卡排前面：维修人员要看的是"这台机器连在哪"
            (virtual if _is_virtual_adapter(name) else physical).append(line)
        data["网络"] = physical + [f"{line}（虚拟适配器）" for line in virtual]

    health = run_checks()
    unified = health.get("unified") or from_health(health.get("items") or [])
    info = summarize(unified)

    # 结论与"要处理的事"是维修人员最先看的两段（render_text 会把它排到最前）
    data["诊断结论"] = info["headline"]
    issues = only_issues(unified)
    if issues:
        data["需要处理"] = [
            f"[{one['statusLabel']}] {one['title']}：{one['summary']}"
            + (f"｜怎么办：{one['advice']}" if one.get("advice") else "")
            + ("（需人工/报修）" if one.get("manual") else "")
            for one in issues
        ]
    data["体检明细"] = [
        f"{'正常' if item['ok'] else '异常'}｜{item['name']}：{item['detail']}"
        for item in health["items"]
    ]
    return data


def render_text(data: dict[str, Any]) -> str:
    """渲染为整齐的纯文本报告。

    顺序上把「诊断结论 / 需要处理」提到最前：维修人员最需要先看到这两段，
    硬件明细放后面备查。
    """
    lines = ["=" * 48, "OpenClass-Box 报修信息报告", "=" * 48]

    ordered: dict[str, Any] = {
        key: data[key] for key in _PRIORITY_KEYS if key in data
    }
    ordered.update({key: value for key, value in data.items() if key not in _PRIORITY_KEYS})

    for key, value in ordered.items():
        if key == "生成时间":
            continue
        lines.append("")
        lines.append(f"【{key}】")
        if isinstance(value, dict):
            for k, v in value.items():
                lines.append(f"  {k}：{v}")
        elif isinstance(value, list):
            lines.extend([f"  - {item}" for item in value] or ["  （无）"])
        else:
            lines.append(f"  {value}")

    lines += [
        "",
        "-" * 48,
        f"生成时间：{data.get('生成时间', '-')}",
        "说明：本报告由 OpenClass-Box 自动采集，内容均为本机真实信息。",
    ]
    return "\n".join(lines)


def export() -> dict[str, Any]:
    """生成报告并保存到桌面。返回 {ok, path, content}。"""
    try:
        data = collect()
        text = render_text(data)
        desktop = Path(os.path.expanduser("~")) / "Desktop"
        if not desktop.is_dir():
            desktop = Path(os.path.expanduser("~"))
        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = desktop / f"OpenClass-Box报修报告_{stamp}.txt"
        path.write_text(text, encoding="utf-8")
        return {"ok": True, "path": str(path), "content": text}
    except (OSError, ValueError) as exc:
        return {"ok": False, "path": "", "content": f"导出失败：{exc}"}
