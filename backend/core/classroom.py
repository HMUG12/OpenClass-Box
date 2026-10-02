"""
课堂专属工具 —— 投屏 / 触摸 / 教学软件 / 还原环境检测。

这些检测只对「教室一体机」这个场景有意义，通用工具箱不会做：
  · **投影与显示拓扑**：投屏没画面时，先确认是不是被切成了扩展模式；
  · **无线投屏能力**：这台机器到底支不支持 Miracast（Wi-Fi Direct）；
  · **触摸**：屏幕触控是否可用、支持几点、一键打开系统校准；
  · **教学软件盘点**：希沃白板 / 鸿合 / 极域 / 班级优化大师等装了哪些、什么版本；
  · **还原环境**：冰点还原 / 影子系统 / 云桌面是否在保护 —— 避免「改完重启就复原」。

所有结果均为本机实测；测不到的项会明确说明原因，不编造。
"""
from __future__ import annotations

import ctypes
import os
import platform
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# SM_DIGITIZER 的位含义（微软文档）
_DIGITIZER_INTEGRATED_TOUCH = 0x01
_DIGITIZER_EXTERNAL_TOUCH = 0x02
_DIGITIZER_INTEGRATED_PEN = 0x04
_DIGITIZER_EXTERNAL_PEN = 0x08
_DIGITIZER_MULTI_INPUT = 0x40
_DIGITIZER_READY = 0x80

# 常见教学 / 教室管理软件识别规则（关键词小写匹配卸载项名称）
_APP_RULES: list[dict[str, Any]] = [
    {"kind": "白板", "name": "希沃白板 5", "match": ("easinote", "希沃白板")},
    {"kind": "白板", "name": "鸿合白板", "match": ("hitevision", "鸿合", "hht whiteboard")},
    {"kind": "白板", "name": "中庆白板", "match": ("zq-whiteboard", "中庆")},
    {"kind": "白板", "name": "天喻白板", "match": ("ty-", "天喻")},
    {"kind": "投屏", "name": "希沃授课助手", "match": ("seewolink", "希沃授课助手", "seewo link")},
    {"kind": "投屏", "name": "希沃传屏", "match": ("seewo", "希沃传屏")},
    {"kind": "投屏", "name": "鸿合投屏", "match": ("hitevision", "鸿合投屏")},
    {"kind": "投屏", "name": "乐播投屏", "match": ("lebo", "乐播")},
    {"kind": "课堂管理", "name": "班级优化大师", "match": ("班级优化大师", "classdojo", "optimized classroom")},
    {"kind": "课堂管理", "name": "极域电子教室", "match": ("mythware", "topdomainer", "极域")},
    {"kind": "课堂管理", "name": "ClassIn", "match": ("classin",)},
    {"kind": "云桌面", "name": "噢易云桌面", "match": ("os-easy", "oseasy", "噢易")},
    {"kind": "云桌面", "name": "锐捷云桌面", "match": ("rainbow", "ruijie", "锐捷")},
    {"kind": "考试", "name": "学考/机考系统", "match": ("kaoshi", "考试系统", "机考")},
]

# 还原 / 保护类软件（服务名与进程名）
_GUARDS: list[dict[str, Any]] = [
    {
        "name": "冰点还原（Deep Freeze）",
        "services": ("dfserv", "dfservwmi"),
        "processes": ("dfserv.exe", "frzstate2k.exe"),
    },
    {
        "name": "影子系统（Shadow Defender）",
        "services": ("shadowdefender",),
        "processes": ("shadowdefender.exe", "defenderdaemon.exe"),
    },
    {
        "name": "联想慧盾 / 保护卡",
        "services": ("lenovohd", "hdguard"),
        "processes": ("hdguard.exe", "lenovohips.exe"),
    },
    {
        "name": "希沃一键还原",
        "services": ("seeworestore", "seewo restore"),
        "processes": ("seeworestore.exe", "restoremgr.exe"),
    },
]

_apps_cache: dict[str, Any] = {"at": 0.0, "items": None}
_lock = threading.Lock()


def _run(args: list[str], timeout: float = 8.0) -> str:
    """执行命令并安全解码（中文系统优先 GBK）。"""
    if platform.system() != "Windows":
        return ""
    try:
        proc = subprocess.run(
            args, capture_output=True, timeout=timeout, creationflags=_CREATE_NO_WINDOW
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    for encoding in ("gbk", "utf-8", "latin-1"):
        try:
            return proc.stdout.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def _item(key: str, name: str, ok: bool, detail: str, suggest: str = "") -> dict[str, Any]:
    return {"key": key, "name": name, "ok": ok, "detail": detail, "suggest": suggest}


# ══════════════════════════════════════════════════════════════
# 显示 / 投影
# ══════════════════════════════════════════════════════════════

def screen() -> dict[str, Any]:
    """显示与投影拓扑（Win32 系统指标，秒级、无需 WMI）。"""
    if platform.system() != "Windows":
        return _item("screen", "投影 / 显示器", True, f"当前系统为 {platform.system()}，已跳过")
    try:
        user32 = ctypes.windll.user32
        monitors = int(user32.GetSystemMetrics(80))  # SM_CMONITORS
        primary_w = int(user32.GetSystemMetrics(0))
        primary_h = int(user32.GetSystemMetrics(1))
        virtual_w = int(user32.GetSystemMetrics(78))  # SM_CXVIRTUALSCREEN
        virtual_h = int(user32.GetSystemMetrics(79))
    except (AttributeError, OSError):
        return _item("screen", "投影 / 显示器", True, "无法读取显示信息，已跳过")

    if monitors <= 1:
        return _item(
            "screen",
            "投影 / 显示器",
            True,
            f"仅 1 个显示设备（主屏 {primary_w}×{primary_h}）；未接投影或外接屏",
            "投屏没画面时：按 Win+P 选择「复制」；再检查 HDMI / VGA 线是否插紧",
        )

    extended = virtual_w > primary_w
    mode = "扩展模式（屏幕 2 是独立桌面）" if extended else "复制模式（两块屏显示同一画面）"
    quality = "分辨率一致" if not extended else f"虚拟桌面 {virtual_w}×{virtual_h}"
    return _item(
        "screen",
        "投影 / 显示器",
        True,
        f"{monitors} 个显示设备，主屏 {primary_w}×{primary_h}，当前为{mode}，{quality}",
        "" if not extended else "如果希望一体机与投影显示同一画面，按 Win+P 切到「复制」",
    )


# ══════════════════════════════════════════════════════════════
# 触摸
# ══════════════════════════════════════════════════════════════

def touch() -> dict[str, Any]:
    """触摸屏可用性（SM_DIGITIZER 位标志）。"""
    if platform.system() != "Windows":
        return _item("touch", "触摸屏", True, f"当前系统为 {platform.system()}，已跳过")
    try:
        user32 = ctypes.windll.user32
        flags = int(user32.GetSystemMetrics(94))  # SM_DIGITIZER
        max_points = int(user32.GetSystemMetrics(95))  # SM_MAXIMUMTOUCHES
    except (AttributeError, OSError):
        return _item("touch", "触摸屏", True, "无法读取触摸信息，已跳过")

    has_touch = bool(flags & (_DIGITIZER_INTEGRATED_TOUCH | _DIGITIZER_EXTERNAL_TOUCH))
    has_pen = bool(flags & (_DIGITIZER_INTEGRATED_PEN | _DIGITIZER_EXTERNAL_PEN))
    ready = bool(flags & _DIGITIZER_READY)

    if not has_touch and not has_pen:
        return _item(
            "touch",
            "触摸屏",
            True,
            "未检测到触摸设备（这台机器可能是非触摸型号，或触摸驱动未加载）",
            "若屏幕本应支持触摸：到「设备管理器 → 人体学输入设备」看「符合 HID 标准的触摸屏」是否被禁用或带感叹号",
        )

    parts = []
    if has_touch:
        parts.append(f"触摸可用（最多 {max_points} 点同时触控）" if max_points else "触摸可用")
    if has_pen:
        parts.append("支持触控笔")
    if not ready:
        parts.append("驱动状态异常：Windows 未报告输入就绪")
    return _item(
        "touch",
        "触摸屏",
        ready,
        "；".join(parts),
        "" if ready else "建议重新安装触摸驱动，或在设备管理器里禁用后重新启用触摸屏",
    )


def open_touch_calibration() -> dict[str, Any]:
    """打开 Windows 触摸校准（优先直接进校准工具，退回平板电脑设置）。"""
    if platform.system() != "Windows":
        return {"ok": False, "message": "仅支持 Windows"}
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    tabcal = system32 / "TabCal.exe"
    try:
        if tabcal.exists():
            subprocess.Popen([str(tabcal)], creationflags=_CREATE_NO_WINDOW)
            return {"ok": True, "message": "已打开触摸校准工具（按提示点十字光标）"}
        subprocess.Popen(
            ["control.exe", "/name", "Microsoft.TabletPCSettings"],
            creationflags=_CREATE_NO_WINDOW,
        )
        return {"ok": True, "message": "已打开「平板电脑设置」，可在其中选择「校准笔和触控输入」"}
    except OSError as exc:
        return {"ok": False, "message": f"打开校准失败：{exc}"}


def open_display_switch() -> dict[str, Any]:
    """打开 Win+P 投影模式切换面板。"""
    if platform.system() != "Windows":
        return {"ok": False, "message": "仅支持 Windows"}
    try:
        subprocess.Popen(["DisplaySwitch.exe"], creationflags=_CREATE_NO_WINDOW)
        return {"ok": True, "message": "已打开投影模式面板：复制 / 扩展 / 仅电脑 / 仅投影"}
    except OSError as exc:
        return {"ok": False, "message": f"打开失败：{exc}"}


# ══════════════════════════════════════════════════════════════
# 无线投屏
# ══════════════════════════════════════════════════════════════

def wireless_display() -> dict[str, Any]:
    """无线投屏能力：Miracast 需要 Wi-Fi Direct 虚拟适配器 + WLAN 服务。

    Windows 只有在网卡与驱动支持时才会创建「Microsoft Wi-Fi Direct
    Virtual Adapter」，因此它的存在比 netsh 文案更可靠。
    """
    if platform.system() != "Windows":
        return _item("wireless", "无线投屏", True, f"当前系统为 {platform.system()}，已跳过")

    direct_adapter = False
    wlan_adapters = 0
    try:
        import psutil

        for name in psutil.net_if_addrs():
            low = name.lower()
            if "wi-fi direct" in low or "wifi direct" in low:
                direct_adapter = True
            elif "wi-fi" in low or "wlan" in low or "无线" in name:
                wlan_adapters += 1
    except ImportError:
        pass

    # WLAN 服务状态（无线投屏依赖它）
    service_state = ""
    try:
        import psutil

        for svc in psutil.win_service_iter():
            if svc.name().lower() in ("wlansvc", "wlansvc"):
                service_state = svc.status()
                break
    except Exception:
        service_state = ""

    service_text = {
        "running": "WLAN 服务运行中",
        "stopped": "WLAN 服务已停止",
    }.get(service_state, "未查询到 WLAN 服务状态")

    if direct_adapter:
        detail = f"支持无线投屏（检测到 Wi-Fi Direct 虚拟适配器）；{service_text}"
        return _item("wireless", "无线投屏", True, detail, "")

    if wlan_adapters == 0:
        detail = "未检测到无线网卡，这台机器无法使用无线投屏（可用 HDMI 有线投屏）"
        suggest = "需要无线投屏时请加装无线网卡，或改用有线投屏 / 投屏器"
    else:
        detail = f"有无线网卡但未创建 Wi-Fi Direct 适配器；{service_text}"
        suggest = "若需要无线投屏：确认无线网卡驱动已装好，并在「设置 → 系统 → 投影到此电脑」里检查是否允许"
    return _item("wireless", "无线投屏", True, detail, suggest)


# ══════════════════════════════════════════════════════════════
# 教学软件盘点
# ══════════════════════════════════════════════════════════════

def _uninstall_entries() -> list[tuple[str, str]]:
    """读取注册表卸载项，返回 (名称, 版本) 列表。"""
    try:
        import winreg
    except ImportError:
        return []

    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    entries: list[tuple[str, str]] = []
    for root, path in roots:
        try:
            key = winreg.OpenKey(root, path)
        except OSError:
            continue
        try:
            index = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                try:
                    sub = winreg.OpenKey(key, sub_name)
                except OSError:
                    continue
                try:
                    name = str(winreg.QueryValueEx(sub, "DisplayName")[0])
                    version = str(winreg.QueryValueEx(sub, "DisplayVersion")[0])
                except OSError:
                    continue
                finally:
                    sub.Close()
                if name:
                    entries.append((name, version))
        finally:
            key.Close()
    return entries


def teaching_apps(force: bool = False) -> dict[str, Any]:
    """盘点教学相关软件（结果缓存 5 分钟，注册表扫描约 1 秒）。"""
    now = time.time()
    with _lock:
        if not force and _apps_cache["items"] is not None and now - float(_apps_cache["at"]) < 300:
            return {"key": "apps", "name": "教学软件", "ok": True, "detail": "", "suggest": "",
                    "items": _apps_cache["items"], "cached": True}

    entries = _uninstall_entries()
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for name, version in entries:
        low = name.lower()
        for rule in _APP_RULES:
            if any(kw in low for kw in rule["match"]):
                if rule["name"] in seen:
                    break
                seen.add(rule["name"])
                found.append({"name": rule["name"], "kind": rule["kind"], "version": version})
                break

    found.sort(key=lambda i: (i["kind"], i["name"]))
    with _lock:
        _apps_cache["at"] = now
        _apps_cache["items"] = found

    kinds = sorted({item["kind"] for item in found})
    detail = (
        f"识别到 {len(found)} 个教学相关软件（{'/'.join(kinds)}）"
        if found
        else "未识别到常见教学软件（希沃白板 / 鸿合 / 极域 / 班级优化大师等）"
    )
    return {"key": "apps", "name": "教学软件", "ok": True, "detail": detail, "suggest": "",
            "items": found, "cached": False}


# ══════════════════════════════════════════════════════════════
# 还原 / 保护环境
# ══════════════════════════════════════════════════════════════

def restore_guard() -> dict[str, Any]:
    """检测还原 / 保护类软件（影响「改了重启就复原」这类现象）。"""
    services: set[str] = set()
    processes: set[str] = set()
    try:
        import psutil

        try:
            for svc in psutil.win_service_iter():
                services.add(svc.name().lower())
        except Exception:
            pass
        for proc in psutil.process_iter(["name"]):
            name = str(proc.info.get("name") or "").lower()
            if name:
                processes.add(name)
    except ImportError:
        return _item("restore", "还原环境", True, "缺少 psutil 组件，已跳过", "")

    hits: list[str] = []
    for guard in _GUARDS:
        if any(s in services for s in guard["services"]) or any(
            p in processes for p in guard["processes"]
        ):
            hits.append(guard["name"])

    if hits:
        return _item(
            "restore",
            "还原环境",
            True,
            f"检测到还原/保护类软件：{'、'.join(hits)}",
            "注意：这类软件会在重启后恢复系统盘，装机与设置修改请在「解除保护」状态下进行",
        )
    return _item(
        "restore",
        "还原环境",
        True,
        "未检测到冰点还原 / 影子系统 / 慧盾等还原保护软件",
        "",
    )


# ══════════════════════════════════════════════════════════════
# 汇总
# ══════════════════════════════════════════════════════════════

def report() -> dict[str, Any]:
    """课堂检测汇总（并发执行各检测项）。"""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="oc-class") as pool:
        futures = {
            "screen": pool.submit(screen),
            "touch": pool.submit(touch),
            "wireless": pool.submit(wireless_display),
            "apps": pool.submit(teaching_apps),
            "restore": pool.submit(restore_guard),
        }
        results: dict[str, Any] = {}
        for key, future in futures.items():
            try:
                results[key] = future.result(timeout=12)
            except Exception as exc:
                results[key] = _item(key, key, True, f"检测失败：{exc}", "")

    items = [results[k] for k in ("screen", "touch", "wireless", "apps", "restore") if k in results]
    # 统一结果（并列产物）：与体检、维护清单同一形状
    from .diag_result import from_classroom, summarize

    unified = from_classroom(items)
    return {
        "items": items,
        "apps": results.get("apps", {}).get("items", []),
        "total": len(items),
        "unified": unified,
        "unifiedSummary": summarize(unified),
    }
