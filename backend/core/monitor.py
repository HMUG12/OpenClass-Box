"""
系统监测服务 —— 硬件 / 网络实时速率 / IP 地址

两条硬性设计约束：

1. **离线优先**。工具箱的主战场是没有外网的教学/机房环境，
   因此唯一依赖网络的动作（公网 IP 查询）必须快速失败并优雅降级，
   绝不允许拖慢或阻塞界面。

2. **统一采样**。实时指标由后台线程按固定间隔采集并缓存到快照里，
   前端轮询只读缓存。避免多个请求各自触发系统调用造成的抖动与开销。
"""
from __future__ import annotations

import json
import os
import platform
import re
import socket
import subprocess
import threading
import time
from typing import Any

import psutil

SAMPLE_INTERVAL = 1.0
HISTORY_SIZE = 60          # 保留最近 60 个采样点
PUBLIC_IP_TIMEOUT = 2.5    # 秒；离线环境下不要让用户等待

# 公网 IP 探测源，逐个尝试，全部失败即降级
_PUBLIC_IP_SOURCES = (
    ("https://api.ipify.org?format=json", lambda d: d.get("ip")),
    ("https://ifconfig.me/all.json", lambda d: d.get("ip_addr")),
    ("https://www.taobao.com/help/getip.php", lambda d: d.get("ip")),
)


def _gb(value: int | float, digits: int = 1) -> float:
    return round(value / (1024 ** 3), digits)


def _mb(value: int | float, digits: int = 1) -> float:
    return round(value / (1024 ** 2), digits)


def _run_capture(args: list[str], timeout: float) -> str:
    """
    捕获外部命令的标准输出并安全解码。

    中文 Windows 上 PowerShell 与 ipconfig 的输出编码并不一致
    （PowerShell 多为 UTF-8，ipconfig 跟随系统 ANSI 即 GBK）。
    若直接用 text=True + 固定 encoding，解码异常会在 Python 内部的
    读取线程里抛出，业务代码 try 不到。因此这里拿原始字节自行按序尝试。
    """
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return ""

    raw = proc.stdout or b""
    if not raw:
        return ""

    for encoding in ("utf-8", "gbk", "mbcs", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="ignore")


# ══════════════════════════════════════════════════════════════
# 静态硬件信息（采集较慢，结果长期缓存）
# ══════════════════════════════════════════════════════════════

def cpu_name() -> str:
    """Windows 上 platform.processor() 往往返回不准确的值，优先读注册表。"""
    if platform.system() == "Windows":
        try:
            import winreg

            path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:  # type: ignore[attr-defined]
                name, _ = winreg.QueryValueEx(key, "ProcessorNameString")  # type: ignore[attr-defined]
                return str(name).strip()
        except OSError:
            pass
    return platform.processor() or platform.machine()


def device_info() -> dict[str, str]:
    """设备与登录用户信息（注册表读取，秒级、无需 WMI）。

    用于「系统状态」右上角显示当前设备型号 / 用户名 / 主机名，
    便于在一体机、教室机等多机环境下快速辨认当前这台机器。
    """
    info: dict[str, str] = {
        "user": os.environ.get("USERNAME", "") or os.environ.get("USER", ""),
        "hostname": platform.node(),
        "manufacturer": "",
        "model": "",
        "family": "",
    }
    if platform.system() == "Windows":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS"
            ) as key:
                for value_name, field in (
                    ("SystemManufacturer", "manufacturer"),
                    ("SystemProductName", "model"),
                    ("SystemFamily", "family"),
                ):
                    try:
                        info[field] = str(winreg.QueryValueEx(key, value_name)[0]).strip()
                    except OSError:
                        continue
        except (OSError, ImportError):
            pass
    return info


def _query_wmi_aux() -> dict[str, Any]:
    """一次性向 PowerShell 取显卡 / 主板 / BIOS 信息。

    这条路径较慢（启动 PowerShell 约 1~2 秒），因此只在首次调用时执行，
    结果写入缓存后不再触碰。
    """
    if platform.system() != "Windows":
        return {}

    script = r"""
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $gpus = Get-CimInstance Win32_VideoController | ForEach-Object {
        [ordered]@{
            name = $_.Name
            memoryGB = [math]::Round($_.AdapterRAM / 1GB, 2)
            driverVersion = $_.DriverVersion
            resolution = "$($_.CurrentHorizontalResolution)x$($_.CurrentVerticalResolution)"
        }
    }
    $board = Get-CimInstance Win32_BaseBoard | ForEach-Object {
        [ordered]@{ manufacturer = $_.Manufacturer; product = $_.Product }
    }
    [ordered]@{ gpus = @($gpus); boards = @($board) } | ConvertTo-Json -Depth 6 -Compress
    """
    output = _run_capture(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=15,
    )
    if not output.strip():
        return {}
    try:
        # PowerShell 5.1 在非交互模式下可能附加进度/警告行，只取第一个 JSON 对象
        start = output.find("{")
        end = output.rfind("}") + 1
        data = json.loads(output[start:end])
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ══════════════════════════════════════════════════════════════
# IP 与网络拓扑
# ══════════════════════════════════════════════════════════════

def _parse_ipconfig() -> dict[str, Any]:
    """从 ipconfig /all 解析网关与 DNS（psutil 不提供这两项）。"""
    result: dict[str, Any] = {"gateways": [], "dnsServers": [], "dhcpEnabled": None}
    if platform.system() != "Windows":
        return result

    output = _run_capture(["ipconfig", "/all"], timeout=8)
    if not output:
        return result

    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("默认网关") or stripped.lower().startswith("default gateway"):
            value = stripped.split(":")[-1].strip()
            if value and value not in result["gateways"]:
                result["gateways"].append(value)
        elif stripped.startswith("DNS Servers") or stripped.startswith("DNS 服务器"):
            value = stripped.split(":")[-1].strip()
            if value and value not in result["dnsServers"]:
                result["dnsServers"].append(value)
    return result


def query_public_ip() -> dict[str, Any]:
    """查询公网出口 IP。

    走统一网络层（跟随系统代理），失败时给出**可读原因**——
    「未获取到」和「为什么没获取到」对老师是两回事：
    前者只能干瞪眼，后者能判断该不该找网络管理员。
    """
    from .net_util import http_get_json, proxy_note

    last_error = ""
    for url, extractor in _PUBLIC_IP_SOURCES:
        ok, data, error = http_get_json(url, timeout=PUBLIC_IP_TIMEOUT)
        if not ok:
            last_error = error
            continue
        ip = extractor(data) if isinstance(data, dict) else None
        if ip:
            return {
                "ip": str(ip).strip(),
                "source": url.split("/")[2],
                "reachable": True,
                "proxy": proxy_note(),
            }
    return {
        "ip": None,
        "source": None,
        "reachable": False,
        "reason": last_error or "所有查询源都没有返回结果",
        "proxy": proxy_note(),
    }


def local_ip() -> str | None:
    """获取局域网 IP，优先选择物理网卡的内网地址（跳过 VPN/虚拟网卡）。"""
    import ipaddress

    candidates: list[str] = []
    for name, addresses in psutil.net_if_addrs().items():
        low = name.lower()
        if any(k in low for k in ("tailscale", "vpn", "tap", "tun", "vmware",
                                  "virtualbox", "veth", "docker", "loopback")):
            continue
        for addr in addresses:
            if addr.family != socket.AF_INET:
                continue
            try:
                ip = ipaddress.ip_address(addr.address)
            except ValueError:
                continue
            if not ip.is_private or ip.is_loopback or ip.is_link_local:
                continue
            candidates.append(addr.address)

    if candidates:
        def _prio(ip: str) -> int:
            if ip.startswith("192.168."):
                return 0
            if ip.startswith("10."):
                return 1
            return 2

        candidates.sort(key=_prio)
        return candidates[0]

    # 兜底：UDP 出口探测（仍可能命中虚拟网卡路由，故为最后手段）
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.5)
    try:
        sock.connect(("223.5.5.5", 80))  # 阿里 DNS，仅用于选路
        return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except socket.gaierror:
            return None
    finally:
        sock.close()


# ══════════════════════════════════════════════════════════════
# 监测器
# ══════════════════════════════════════════════════════════════

class SystemMonitor:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._history: list[dict[str, Any]] = []
        self._latest: dict[str, Any] = {}
        self._static: dict[str, Any] | None = None
        self._aux_ready = threading.Event()
        self._prev_net: Any = None
        self._prev_disk: Any = None
        self._prev_time: float = 0.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ── 生命周期 ───────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._prev_net = psutil.net_io_counters()
        self._prev_disk = psutil.disk_io_counters()
        self._prev_time = time.time()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="oc-monitor")
        self._thread.start()
        # 静态硬件信息较慢，后台预热避免首屏卡顿
        threading.Thread(target=self._warm_static, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(SAMPLE_INTERVAL):
            try:
                self._sample()
            except Exception:  # 采样失败绝不影响主界面
                continue

    # ── 实时采样 ───────────────────────────────────────

    def _sample(self) -> None:
        now = time.time()
        elapsed = max(now - self._prev_time, 1e-6)

        cpu_percent = psutil.cpu_percent(interval=None)
        per_core = psutil.cpu_percent(interval=None, percpu=True)
        memory = psutil.virtual_memory()

        net = psutil.net_io_counters()
        download = upload = 0.0
        if net and self._prev_net:
            download = max(0.0, (net.bytes_recv - self._prev_net.bytes_recv) / elapsed)
            upload = max(0.0, (net.bytes_sent - self._prev_net.bytes_sent) / elapsed)

        disk = psutil.disk_io_counters()
        read = write = 0.0
        if disk and self._prev_disk:
            read = max(0.0, (disk.read_bytes - self._prev_disk.read_bytes) / elapsed)
            write = max(0.0, (disk.write_bytes - self._prev_disk.write_bytes) / elapsed)

        point = {
            "t": round(now * 1000),
            "cpu": cpu_percent,
            "memory": memory.percent,
            "download": round(download),
            "upload": round(upload),
        }

        snapshot = {
            "timestamp": round(now * 1000),
            "cpu": {
                "percent": cpu_percent,
                "perCore": per_core,
                "freqCurrent": getattr(psutil.cpu_freq(), "current", None),
                "loadAvg": self._load_average(),
            },
            "memory": {
                "percent": memory.percent,
                "used": memory.used,
                "total": memory.total,
                "available": memory.available,
            },
            "network": {
                "download": round(download),
                "upload": round(upload),
                "totalRecv": net.bytes_recv if net else 0,
                "totalSent": net.bytes_sent if net else 0,
            },
            "disk": {"read": round(read), "write": round(write)},
            "history": list(self._history),
        }

        with self._lock:
            self._history.append(point)
            if len(self._history) > HISTORY_SIZE:
                del self._history[:-HISTORY_SIZE]
            snapshot["history"] = list(self._history)
            self._latest = snapshot

        self._prev_net = net
        self._prev_disk = disk
        self._prev_time = now

    @staticmethod
    def _load_average() -> list[float] | None:
        try:
            return [round(v, 2) for v in psutil.getloadavg()]
        except AttributeError:  # Windows 无此接口
            return None

    # ── 快照读取 ───────────────────────────────────────

    def metrics(self) -> dict[str, Any]:
        """返回实时快照。采样线程尚未产出时使用首帧兜底。"""
        with self._lock:
            if self._latest:
                return self._latest
        self._sample()
        with self._lock:
            return self._latest

    # ── 静态硬件 ───────────────────────────────────────

    def _warm_static(self) -> None:
        try:
            self.hardware()
        except Exception:
            pass
        finally:
            self._aux_ready.set()

    def hardware(self) -> dict[str, Any]:
        with self._lock:
            if self._static is not None:
                return self._static

        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        freq = psutil.cpu_freq()

        disks: list[dict[str, Any]] = []
        seen: set[str] = set()
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in (part.opts or "") or part.fstype == "":
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            key = part.device
            if key in seen:
                continue
            seen.add(key)
            disks.append(
                {
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "percent": usage.percent,
                }
            )

        # 显卡：注册表登记的全部适配器（含核显）秒级列出，显存取注册表真值
        # （不被 4GB 截断）；驱动 / 分辨率由后台 WMI 查询补齐（见 _fill_wmi_aux）
        gpus: list[dict[str, Any]] = []
        try:
            from .hardware_detail import gpu_list_from_registry

            for item in gpu_list_from_registry():
                size = item.get("memoryBytes")
                gpus.append(
                    {
                        "name": item["name"],
                        "memoryGB": round(size / (1024 ** 3), 1) if size else 0,
                        "shared": not bool(size),
                        "driverVersion": "",
                        "resolution": "",
                        "memorySource": "registry" if size else "shared",
                    }
                )
        except Exception:
            gpus = []

        info: dict[str, Any] = {
            "cpu": {
                "name": cpu_name(),
                "cores": psutil.cpu_count(logical=False),
                "threads": psutil.cpu_count(logical=True),
                "freqMax": getattr(freq, "max", None),
                "freqCurrent": getattr(freq, "current", None),
                "arch": platform.machine(),
            },
            "memory": {
                "total": memory.total,
                "percent": memory.percent,
                "totalGB": _gb(memory.total),
            },
            "swap": {"total": swap.total, "used": swap.used, "percent": swap.percent},
            "disks": disks,
            "gpus": gpus,
            "boards": [],
            "os": {
                "system": platform.system(),
                "release": platform.release(),
                "version": platform.version(),
                "build": platform.win32_ver()[1] if platform.system() == "Windows" else "",
                "hostname": platform.node(),
            },
            "bootTime": psutil.boot_time(),
            "device": device_info(),
            "hardwareReady": False,
        }

        need_aux = False
        with self._lock:
            if self._static is None:
                self._static = info
                need_aux = True
            result = self._static

        # 慢查询（WMI 显卡名/主板）放后台：界面先拿到快照，前端稍后再取一次完整信息
        if need_aux:
            threading.Thread(
                target=self._fill_wmi_aux, daemon=True, name="oc-hw-aux"
            ).start()
        return result

    def _fill_wmi_aux(self) -> None:
        """后台补齐 WMI 硬件信息（显卡名称 / 主板），完成后置 hardwareReady。"""
        try:
            aux = _query_wmi_aux()
        except Exception:
            aux = {}

        try:
            from .hardware_detail import gpu_vram_from_registry

            vram = gpu_vram_from_registry()
        except Exception:
            vram = {}

        with self._lock:
            if self._static is None:
                return

            merged: dict[str, dict[str, Any]] = {}
            for gpu_item in aux.get("gpus", []) or []:
                if not isinstance(gpu_item, dict):
                    continue
                gpu_name = str(gpu_item.get("name") or "")
                if not gpu_name:
                    continue
                memory = float(gpu_item.get("memoryGB") or 0) * (1024 ** 3)
                if gpu_name in vram and vram[gpu_name] > memory:
                    gpu_item = {
                        **gpu_item,
                        "memoryGB": round(vram[gpu_name] / (1024 ** 3), 1),
                        "shared": False,
                        "memorySource": "registry",
                    }
                else:
                    # WMI 的 AdapterRAM 对核显常为 0 或被截断：显存读不到就标记为
                    # 共享内存，前端据此显示「共享内存」而不是「0 GB」
                    gpu_item = {
                        **gpu_item,
                        "memoryGB": float(gpu_item.get("memoryGB") or 0),
                        "shared": not bool(gpu_item.get("memoryGB")),
                        "memorySource": "wmi",
                    }
                merged[gpu_name] = gpu_item

            # 注册表读到但 WMI 未报告的显卡也保留（显存值最准）
            for gpu_item in self._static.get("gpus", []):
                name = str(gpu_item.get("name") or "")
                if not name:
                    continue
                if name not in merged:
                    merged[name] = gpu_item
                elif not merged[name].get("memoryGB"):
                    merged[name]["memoryGB"] = gpu_item.get("memoryGB")

            if merged:
                self._static["gpus"] = list(merged.values())

            boards = aux.get("boards", []) or []
            if boards:
                self._static["boards"] = boards
            self._static["hardwareReady"] = True

    # ── 网络 └ IP ──────────────────────────────────────

    def network(self) -> dict[str, Any]:
        interfaces: list[dict[str, Any]] = []
        stats = psutil.net_if_stats()
        io = psutil.net_io_counters(pernic=True)

        for name, addresses in psutil.net_if_addrs().items():
            iface = {"name": name, "ipv4": [], "ipv6": [], "mac": None,
                     "up": False, "speedMbps": 0, "mtu": 0,
                     "bytesSent": 0, "bytesRecv": 0}

            for addr in addresses:
                if addr.family == socket.AF_INET:
                    iface["ipv4"].append({"address": addr.address, "netmask": addr.netmask})
                elif addr.family == socket.AF_INET6:
                    iface["ipv6"].append(addr.address.split("%")[0])
                elif addr.family == getattr(psutil, "AF_LINK", None):
                    iface["mac"] = addr.address

            stat = stats.get(name)
            if stat:
                iface["up"] = bool(stat.isup)
                iface["speedMbps"] = stat.speed
                iface["mtu"] = stat.mtu

            counter = io.get(name)
            if counter:
                iface["bytesSent"] = counter.bytes_sent
                iface["bytesRecv"] = counter.bytes_recv

            interfaces.append(iface)

        return {"interfaces": interfaces, "topology": _parse_ipconfig()}

    def ip(self, include_public: bool = False) -> dict[str, Any]:
        hostname = platform.node()
        # 主机名解析出的 IP 往往是回环，用出口 IP 兜底
        try:
            resolved = socket.gethostbyname(hostname)
        except socket.gaierror:
            resolved = None

        lan = local_ip()
        for address in ([resolved] if resolved else []) + ([lan] if lan else []):
            if address and address != "127.0.0.1":
                lan = address
                break

        result: dict[str, Any] = {
            "hostname": hostname,
            "local": lan,
            "loopback": "127.0.0.1",
            "public": None,
        }
        if include_public:
            result["public"] = query_public_ip()
        else:
            result["public"] = {"ip": None, "source": None, "reachable": False, "queried": False}
        return result


monitor = SystemMonitor()
