"""
设备衰退监测与维护清单 —— 把"预测"做成**可信、可交付**的版本。

立场（写在最前面，避免以后漂移）：

  · **不做寿命预测**。一个学校几十台机器，样本量不足以支撑任何模型；
    对外宣称"这块硬盘还剩 45 天"在没有数据支撑时就是编造，一旦不准，
    学校对我们的信任是一次性的。宁可不吹，也不编。
  · 只做三件确定的事：
      1. **采集**：硬盘可靠性计数（温度 / 通电时间 / 读写错误 / 磨损）、
         异常关机与蓝屏历史、CPU 温度；
      2. **判定**：用**公开且可解释**的规则给结论（关注 / 预警 / 建议更换），
         每条结论都能说清"依据是哪个数值"；
      3. **成单**：汇总成"本月维护清单"——该换谁的硬盘、该给谁清灰、
         哪几台机器最近老是无故重启，一次性交给电教委员。
  · 规则来源（都是公开统计规律，不是玄学）：
      - 重映射 / 待处理扇区 > 0：大规模硬盘统计里最显著的失效前兆；
      - 通电时间 + 温度长期偏高：老化与散热的叠加风险；
      - 温度超过阈值：先清灰、查风道，别急着换件；
      - 近 14 天异常关机 / 蓝屏 ≥ 阈值：内存、电源、驱动的嫌疑信号。
"""
from __future__ import annotations

import json
import platform
import subprocess
import time
from typing import Any

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ── 判定阈值（都可解释，改这里就等于改策略）──────────────
HDD_TEMP_WARN = 50          # ℃：机械盘长期高温会加速老化
SSD_TEMP_WARN = 70          # ℃：固态盘
POWER_ON_HOURS_HIGH = 20000  # 小时：约 2.3 年连续通电，进入关注区间
CRASH_WINDOW_DAYS = 14      # 天：统计异常关机 / 蓝屏的时间窗
CRASH_WARN_COUNT = 3        # 次：窗口内达到这个次数就值得查一查

LEVELS = {
    "ok": "正常",
    "watch": "关注",
    "warn": "预警",
    "replace": "建议更换",
}


# ══════════════════════════════════════════════════════════════
# 采集
# ══════════════════════════════════════════════════════════════

def _run_ps(script: str, timeout: int = 25) -> Any:
    """执行 PowerShell 并把 JSON 结果解析出来；任何失败都返回 None。"""
    if platform.system() != "Windows":
        return None
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = proc.stdout.decode("utf-8", "replace").strip()
    if not text:
        return None
    start = min(
        (index for index in (text.find("["), text.find("{")) if index >= 0),
        default=-1,
    )
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except ValueError:
        return None


_DISK_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$rows = @()
foreach ($d in Get-PhysicalDisk -ErrorAction SilentlyContinue) {
    $c = $null
    try { $c = $d | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue } catch {}
    $rows += [ordered]@{
        name        = "$($d.FriendlyName)"
        mediaType   = "$($d.MediaType)"
        busType     = "$($d.BusType)"
        sizeGB      = [math]::Round($d.Size / 1GB, 1)
        health      = "$($d.HealthStatus)"
        temperature = if ($c -and $c.Temperature -ne $null) { [int]$c.Temperature } else { $null }
        powerOnHours= if ($c -and $c.PowerOnHours -ne $null) { [int]$c.PowerOnHours } else { $null }
        readErrors  = if ($c -and $c.ReadErrorsTotal -ne $null) { [int64]$c.ReadErrorsTotal } else { $null }
        writeErrors = if ($c -and $c.WriteErrorsTotal -ne $null) { [int64]$c.WriteErrorsTotal } else { $null }
        wear        = if ($c -and $c.Wear -ne $null) { [int]$c.Wear } else { $null }
    }
}
@($rows) | ConvertTo-Json -Depth 5 -Compress
"""

_CRASH_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$since = (Get-Date).AddDays(-%d)
$events = @()
try {
    $events = Get-WinEvent -FilterHashtable @{LogName='System'; Id=41,6008,1001; StartTime=$since} -ErrorAction SilentlyContinue |
        Select-Object -First 50 | ForEach-Object {
            [ordered]@{ id = $_.Id; time = $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss'); text = "$($_.ProviderName)" }
        }
} catch {}
@($events) | ConvertTo-Json -Depth 4 -Compress
"""


def disk_signals() -> list[dict[str, Any]]:
    """硬盘衰退信号（温度 / 通电时间 / 读写错误 / 磨损 / 健康状态）。"""
    data = _run_ps(_DISK_SCRIPT)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    result: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        result.append(
            {
                "name": str(item.get("name")),
                "mediaType": str(item.get("mediaType") or ""),
                "busType": str(item.get("busType") or ""),
                "sizeGB": item.get("sizeGB"),
                "health": str(item.get("health") or ""),
                "temperature": item.get("temperature"),
                "powerOnHours": item.get("powerOnHours"),
                "readErrors": item.get("readErrors"),
                "writeErrors": item.get("writeErrors"),
                "wear": item.get("wear"),
            }
        )
    return result


def crash_signals(days: int = CRASH_WINDOW_DAYS) -> dict[str, Any]:
    """异常关机 / 蓝屏历史（Event 41=意外关机，6008=意外关机，1001=BugCheck）。"""
    data = _run_ps(_CRASH_SCRIPT % days)
    if isinstance(data, dict):
        data = [data]
    items = [item for item in (data or []) if isinstance(item, dict)]
    return {
        "windowDays": days,
        "count": len(items),
        "events": items[:20],
    }


# ══════════════════════════════════════════════════════════════
# 判定（纯函数，方便测试与解释）
# ══════════════════════════════════════════════════════════════

def evaluate_disk(disk: dict[str, Any]) -> dict[str, Any]:
    """给一块硬盘下结论，并说明**依据是哪个数值**。"""
    reasons: list[str] = []
    level = "ok"

    def bump(target: str) -> None:
        nonlocal level
        order = ["ok", "watch", "warn", "replace"]
        if order.index(target) > order.index(level):
            level = target

    health = str(disk.get("health") or "").lower()
    if health and health not in ("healthy", "ok", "unknown", ""):
        bump("replace")
        reasons.append(f"系统报告磁盘状态为 {disk.get('health')}")

    read_errors = disk.get("readErrors")
    write_errors = disk.get("writeErrors")
    total_errors = 0
    for value in (read_errors, write_errors):
        if isinstance(value, (int, float)) and value > 0:
            total_errors += int(value)
    if total_errors > 0:
        # 读写错误计数是"已经开始丢数据"的信号，比温度更值得紧张
        bump("replace" if total_errors >= 10 else "warn")
        reasons.append(f"读写错误累计 {total_errors} 次")

    temperature = disk.get("temperature")
    media = str(disk.get("mediaType") or "").lower()
    limit = SSD_TEMP_WARN if "ssd" in media else HDD_TEMP_WARN
    if isinstance(temperature, (int, float)) and temperature > 0:
        if temperature >= limit:
            bump("warn")
            reasons.append(f"温度 {temperature}℃（超过 {limit}℃ 阈值）")
        elif temperature >= limit - 5:
            bump("watch")
            reasons.append(f"温度 {temperature}℃ 接近阈值")

    hours = disk.get("powerOnHours")
    if isinstance(hours, (int, float)) and hours >= POWER_ON_HOURS_HIGH:
        bump("watch")
        reasons.append(f"通电 {int(hours)} 小时（老化区间）")

    wear = disk.get("wear")
    if isinstance(wear, (int, float)) and wear >= 80:
        bump("warn")
        reasons.append(f"磨损度 {wear}%")

    if media.lower().startswith("hdd") or "hdd" in media:
        type_label = "机械硬盘"
    elif "ssd" in media:
        type_label = "固态硬盘"
    else:
        type_label = media or "未知类型"

    return {
        "level": level,
        "levelLabel": LEVELS.get(level, level),
        "reasons": reasons,
        "typeLabel": type_label,
        **disk,
    }


def evaluate_crash(signals: dict[str, Any]) -> dict[str, Any]:
    """异常关机 / 蓝屏的信号判定。"""
    count = int(signals.get("count") or 0)
    days = int(signals.get("windowDays") or CRASH_WINDOW_DAYS)
    level = "ok"
    reasons: list[str] = []
    if count >= CRASH_WARN_COUNT:
        level = "warn"
        reasons.append(f"近 {days} 天有 {count} 次异常关机 / 蓝屏")
    elif count > 0:
        level = "watch"
        reasons.append(f"近 {days} 天有 {count} 次异常关机 / 蓝屏")
    return {
        "level": level,
        "levelLabel": LEVELS[level],
        "reasons": reasons,
        "count": count,
        "windowDays": days,
        # 事件明细留给界面展开看（只取最近若干条，避免无限增长）
        "events": list(signals.get("events") or [])[:10],
    }


def evaluate_cpu_temp(temp: Any) -> dict[str, Any]:
    """CPU 温度（读不到就明确说读不到，不猜）。"""
    if not isinstance(temp, (int, float)) or temp <= 0:
        return {"level": "ok", "levelLabel": LEVELS["ok"], "reasons": [], "temperature": None}
    if temp >= 85:
        return {
            "level": "warn",
            "levelLabel": LEVELS["warn"],
            "reasons": [f"CPU 温度 {temp}℃（建议清灰 / 检查散热）"],
            "temperature": temp,
        }
    if temp >= 75:
        return {
            "level": "watch",
            "levelLabel": LEVELS["watch"],
            "reasons": [f"CPU 温度 {temp}℃ 偏高"],
            "temperature": temp,
        }
    return {"level": "ok", "levelLabel": LEVELS["ok"], "reasons": [], "temperature": temp}


# ══════════════════════════════════════════════════════════════
# 汇总：维护清单
# ══════════════════════════════════════════════════════════════

_ORDER = ["replace", "warn", "watch", "ok"]


def scan() -> dict[str, Any]:
    """完整扫描一次：硬盘 + 异常重启 + CPU 温度 → 维护清单。"""
    from .hardware_detail import collect

    raw_disks = disk_signals()
    disks = [evaluate_disk(item) for item in raw_disks]
    # 注意：crash_signals() 给的是原始信号，必须过一遍判定才有 level
    crashes = evaluate_crash(crash_signals())
    # SMART 详情需要管理员权限或磁盘/控制器支持，读不到时要如实告知用户
    smart_available = any(
        item.get("temperature") is not None
        or item.get("powerOnHours") is not None
        or item.get("readErrors") is not None
        for item in raw_disks
    )

    cpu_temp = None
    try:
        quick = collect(quick=True)
        for item in quick.get("temperatures", []) or []:
            if isinstance(item, dict) and "cpu" in str(item.get("name", "")).lower():
                cpu_temp = item.get("value")
                break
    except Exception:
        cpu_temp = None
    cpu = evaluate_cpu_temp(cpu_temp)

    actions: list[dict[str, Any]] = []
    for disk in disks:
        if disk["level"] in ("watch", "warn", "replace"):
            actions.append(
                {
                    "kind": "disk",
                    "level": disk["level"],
                    "levelLabel": disk["levelLabel"],
                    "title": str(disk.get("name") or "硬盘"),
                    "detail": "；".join(disk["reasons"]) or "—",
                    "advice": _disk_advice(disk),
                }
            )
    if crashes["level"] != "ok":
        actions.append(
            {
                "kind": "crash",
                "level": crashes["level"],
                "levelLabel": crashes["levelLabel"],
                "title": "异常关机 / 蓝屏",
                "detail": "；".join(crashes["reasons"]) or "—",
                "advice": "先查内存与电源，再看显卡驱动；必要时用「维护 → 诊断 → 生成诊断包」交给维修人员",
            }
        )
    if cpu["level"] != "ok":
        actions.append(
            {
                "kind": "thermal",
                "level": cpu["level"],
                "levelLabel": cpu["levelLabel"],
                "title": "CPU 温度",
                "detail": "；".join(cpu["reasons"]) or "—",
                "advice": "先清灰与检查风道；一体机长期高温也会拖慢整机",
            }
        )

    # 磁盘空间：不需要管理员权限，SMART 读不到时至少还有这一项能提醒
    try:
        import psutil

        for part in psutil.disk_partitions(all=False):
            if "cdrom" in (part.opts or "") or not part.fstype:
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            if usage.total <= 0:
                continue
            percent = float(usage.percent)
            free_gb = usage.free / (1024 ** 3)
            if percent >= 95:
                actions.append(
                    {
                        "kind": "space",
                        "level": "warn",
                        "levelLabel": LEVELS["warn"],
                        "title": f"{part.mountpoint} 空间告急",
                        "detail": f"已用 {percent:.0f}%（剩余 {free_gb:.1f} GB）",
                        "advice": "用「维护 → 一键修复 → 磁盘清理」清理临时文件；长期不足建议加盘或迁移课件",
                    }
                )
            elif percent >= 90:
                actions.append(
                    {
                        "kind": "space",
                        "level": "watch",
                        "levelLabel": LEVELS["watch"],
                        "title": f"{part.mountpoint} 空间偏低",
                        "detail": f"已用 {percent:.0f}%（剩余 {free_gb:.1f} GB）",
                        "advice": "顺手清理临时文件，避免上课录像或课件写入失败",
                    }
                )
    except Exception:
        pass

    # 还原保护状态：不替代还原卡，但"保护被关了"必须让人知道
    restore: dict[str, Any] = {}
    try:
        from .restore_watch import detect as detect_restore

        restore = detect_restore()
        if restore.get("level") in ("watch", "warn"):
            actions.append(
                {
                    "kind": "restore",
                    "level": restore["level"],
                    "levelLabel": restore["levelLabel"],
                    "title": "还原保护",
                    "detail": "；".join(restore.get("reasons") or []) or restore.get("detail", ""),
                    "advice": restore.get("advice", ""),
                }
            )
    except Exception:
        restore = {}

    actions.sort(key=lambda item: _ORDER.index(item["level"]))

    worst = "ok"
    for item in actions:
        if _ORDER.index(item["level"]) < _ORDER.index(worst):
            worst = item["level"]

    return {
        "ok": True,
        "scannedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "level": worst,
        "levelLabel": LEVELS.get(worst, worst),
        "disks": disks,
        "crashes": crashes,
        "cpu": cpu,
        "restore": restore,
        "actions": actions,
        "counts": {
            "replace": sum(1 for a in actions if a["level"] == "replace"),
            "warn": sum(1 for a in actions if a["level"] == "warn"),
            "watch": sum(1 for a in actions if a["level"] == "watch"),
        },
        "smartAvailable": smart_available,
        "smartHint": ""
        if smart_available
        else "当前权限读不到磁盘 SMART 详情（温度 / 通电时间 / 读写错误计数）—— "
        "以管理员身份运行本程序可以读到更完整的数据；下面的结论仍来自系统报告的健康状态、"
        "磁盘空间与异常关机历史。",
        "note": "结论只依据可解释的数值（读写错误、温度、通电时间、异常关机次数、磁盘空间），不做寿命预测",
    }


def _disk_advice(disk: dict[str, Any]) -> str:
    level = disk["level"]
    if level == "replace":
        return "尽快备份重要课件并更换该盘；换盘后可用「学期模式 → 配置模板」快速恢复环境"
    if level == "warn":
        return "一周内备份数据并安排更换；期间避免在这台机器上存放唯一副本"
    return "先改善散热（清灰 / 查风道），并每月复查一次错误计数"


def status() -> dict[str, Any]:
    """轻量状态：给界面显示"上次扫描"与是否存在待处理项。"""
    cached = _read_cache()
    if cached:
        return {"ok": True, **cached}
    return {"ok": True, "scannedAt": "", "level": "ok", "levelLabel": LEVELS["ok"], "actions": [], "counts": {}}


# ── 扫描结果落盘（界面下次打开就能直接看到，不必每次重扫）──
def _cache_file():
    from .paths import config_dir

    return config_dir() / "health_watch.json"


def _read_cache() -> dict[str, Any]:
    path = _cache_file()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def scan_and_cache() -> dict[str, Any]:
    """扫描并写入缓存（供定时任务 / 手动刷新调用）。"""
    result = scan()
    try:
        path = _cache_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {k: v for k, v in result.items() if k != "disks"},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass
    return result
