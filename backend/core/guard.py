"""
安全扩展 —— 教室机器上最常见的五类问题，一项项能查、能修、能还原。

  · **浏览器篡改修复**：桌面快捷方式被加"导航尾巴"、hosts 被塞广告域名、
    浏览器主页被改 —— 能定位到具体位置，改之前自动备份；
  · **USB 设备防护**：查看并切换「U 盘只读 / 禁用 USB 存储」；
  · **自启动管理**：列出开机自启项，可停用 / 恢复（停用即备份，随时能还）；
  · **弹窗管理**：按已知规则找出广告弹窗进程，可一键结束，也可开后台守护；
  · **异常高占用监测**：CPU / 内存超阈值就托盘提醒（只提醒，不自动杀进程）。

三条原则：
  1. **改前必备份**：所有修改都先把原值写进 data/guard/，一键还原；
  2. **需要管理员的动作弹 UAC**，绝不静默改系统；
  3. **判定必须说清依据**：命中了哪条规则、发现了什么，都写在结果里。
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .config import config
from .paths import config_dir

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ── 判定用规则 ────────────────────────────────────────────────

# 常见"装软件被顺带安装"的弹窗/推广进程（命中即列出，交由用户决定）
POPUP_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("2345 系列推广", ("2345explorer.exe", "2345minipage.exe", "2345tray.exe", "2345speedbox.exe")),
    ("迅雷推广", ("thunder.exe", "xlserviceplatform.exe", "thunderplatform.exe")),
    ("WPS 推送/更新", ("wpscenter.exe", "wpsupdate.exe", "ksomisc.exe")),
    ("搜狗推广", ("sogouexplorer.exe", "sgdownload.exe", "sgtool.exe")),
    ("驱动人生/精灵", ("drivergenius.exe", "drivergeniusnew.exe", "drivergeniusupdate.exe")),
    ("Flash 助手", ("flashcenter.exe", "flashhelper.exe", "flashhelperupdate.exe")),
    ("酷狗推广", ("kugouupdate.exe", "kugounews.exe", "kugoupop.exe")),
    ("快压/压缩推广", ("kuaizip.exe", "kzip.exe", "zipupdate.exe")),
    ("鲁大师推广", ("ludashi.exe", "ldsnews.exe", "ldspopup.exe")),
)

# 导航站 / 广告域名关键词：命中说明快捷方式或 hosts 被改过
HIJACK_WORDS = (
    "hao123", "2345.com", "kua123", "sogou.com", "so.com", "dh.2345",
    "microsoftedge.microsoft.com/newtab", "360.cn/nav", "union.360",
    "cn.bing.com/search?form=hijack", "go.microsoft.com/fwlink/?linkid=ad",
)

HOSTS_PATH = (
    Path(os.environ.get("SystemRoot", r"C:\Windows"))
    / "System32"
    / "drivers"
    / "etc"
    / "hosts"
)

WATCH_CPU = 80.0
WATCH_MEM_MB = 1500


# ══════════════════════════════════════════════════════════════
# 备份
# ══════════════════════════════════════════════════════════════

def guard_dir() -> Path:
    folder = config_dir() / "guard"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _backup(name: str, payload: dict[str, Any]) -> str:
    path = guard_dir() / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)
    except OSError:
        return ""


# ══════════════════════════════════════════════════════════════
# 1. 浏览器篡改
# ══════════════════════════════════════════════════════════════

def _start_menu_dirs() -> list[Path]:
    dirs = []
    for base in (os.environ.get("APPDATA"), os.environ.get("PROGRAMDATA")):
        if base:
            dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return [d for d in dirs if d.is_dir()]


def shortcut_dirs() -> list[Path]:
    """可能被塞"导航尾巴"的位置：桌面、公共桌面、开始菜单、任务栏固定项。"""
    candidates: list[Path] = []
    desktop = Path(os.path.expanduser("~")) / "Desktop"
    if desktop.is_dir():
        candidates.append(desktop)
    public = os.environ.get("PUBLIC")
    if public:
        folder = Path(public) / "Desktop"
        if folder.is_dir():
            candidates.append(folder)
    candidates += _start_menu_dirs()
    pins = (
        Path(os.environ.get("APPDATA", ""))
        / "Microsoft"
        / "Internet Explorer"
        / "Quick Launch"
        / "User Pinned"
        / "TaskBar"
    )
    if pins.is_dir():
        candidates.append(pins)
    return candidates


def scan_hijack() -> dict[str, Any]:
    """扫描快捷方式尾巴、hosts、浏览器主页。"""
    issues: list[dict[str, Any]] = []

    # ① 快捷方式（.url 可直接读；.lnk 用关键词启发式）
    for folder in shortcut_dirs():
        try:
            files = list(folder.glob("*.url")) + list(folder.glob("*.lnk"))
        except OSError:
            continue
        for file in files:
            try:
                raw = file.read_bytes()
            except OSError:
                continue
            text = raw.decode("utf-16", "ignore") + raw.decode("gbk", "ignore")
            for word in HIJACK_WORDS:
                if word.lower() in text.lower():
                    issues.append(
                        {
                            "kind": "shortcut",
                            "path": str(file),
                            "reason": f"快捷方式指向导航/推广地址（命中关键词 {word}）",
                            "detail": word,
                        }
                    )
                    break

    # ② hosts
    if HOSTS_PATH.is_file():
        try:
            lines = HOSTS_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            lines = []
        for index, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            low = stripped.lower()
            if any(word.split("?")[0] in low for word in HIJACK_WORDS) or re.search(
                r"(hao123|2345|sogou|360\.cn|union)", low
            ):
                issues.append(
                    {
                        "kind": "hosts",
                        "line": index,
                        "content": stripped[:200],
                        "reason": "hosts 中出现导航/推广域名（正常上网不需要这些）",
                    }
                )

    # ③ 浏览器主页（只读展示，不擅自改）
    homes: list[dict[str, str]] = []
    try:
        import winreg

        candidates = [
            ("IE", winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Internet Explorer\Main", "Start Page"),
            ("Edge", winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Edge\Main", "Start Page"),
            ("Edge(策略)", winreg.HKEY_CURRENT_USER, r"Software\Policies\Microsoft\Edge", "HomepageLocation"),
            ("Chrome", winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\Main", "Start Page"),
            ("Chrome(策略)", winreg.HKEY_CURRENT_USER, r"Software\Policies\Google\Chrome", "HomepageLocation"),
        ]
        for label, root, path, name in candidates:
            try:
                key = winreg.OpenKey(root, path)
                value = str(winreg.QueryValueEx(key, name)[0])
                key.Close()
            except OSError:
                continue
            homes.append({"browser": label, "value": value})
            if any(word in value.lower() for word in HIJACK_WORDS):
                issues.append(
                    {
                        "kind": "homepage",
                        "browser": label,
                        "value": value,
                        "reason": "浏览器主页被改成了导航/推广地址",
                    }
                )
    except ImportError:
        pass

    return {
        "issues": issues,
        "homepages": homes,
        "hostsPath": str(HOSTS_PATH),
        "scannedDirs": [str(d) for d in shortcut_dirs()],
        "total": len(issues),
    }


def fix_hosts(lines: list[int]) -> dict[str, Any]:
    """把 hosts 里指定的行注释掉（先备份整份 hosts）。"""
    if not HOSTS_PATH.is_file():
        return {"ok": False, "message": "找不到 hosts 文件"}
    try:
        content = HOSTS_PATH.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return {"ok": False, "message": f"读取失败：{exc}"}

    saved = _backup("hosts", {"content": content, "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    rows = content.splitlines()
    fixed = 0
    for index in lines:
        if 1 <= index <= len(rows) and rows[index - 1].strip() and not rows[index - 1].lstrip().startswith("#"):
            rows[index - 1] = "# [OpenClass 已注释] " + rows[index - 1]
            fixed += 1

    new_content = "\n".join(rows) + "\n"
    try:
        HOSTS_PATH.write_text(new_content, encoding="utf-8")
    except PermissionError:
        return {
            "ok": False,
            "needAdmin": True,
            "message": "hosts 需要管理员权限才能修改：请以管理员身份重启程序后重试（已备份原文件）",
            "backup": saved,
        }
    except OSError as exc:
        return {"ok": False, "message": f"写入失败：{exc}", "backup": saved}

    return {
        "ok": True,
        "message": f"已注释 {fixed} 行可疑记录（原文件已备份：{saved}）",
        "backup": saved,
    }


def delete_shortcut(path: str) -> dict[str, Any]:
    """删除被加尾巴的快捷方式（先备份文件）。"""
    target = Path((path or "").strip().strip('"'))
    if not target.is_file():
        return {"ok": False, "message": "文件不存在"}
    folder = guard_dir() / "shortcuts"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(target, folder / target.name)
        target.unlink()
    except OSError as exc:
        return {"ok": False, "message": f"删除失败：{exc}"}
    return {"ok": True, "message": f"已删除（备份在 {folder}）"}


# ══════════════════════════════════════════════════════════════
# 2. USB 设备防护
# ══════════════════════════════════════════════════════════════

_USB_STORAGE = r"SYSTEM\CurrentControlSet\Services\USBSTOR"
_STORAGE_POLICY = r"SYSTEM\CurrentControlSet\Control\StorageDevicePolicies"


def usb_status() -> dict[str, Any]:
    """读取 USB 存储策略（只读，不需要管理员）。"""
    result: dict[str, Any] = {
        "storageEnabled": None,
        "writeProtected": None,
        "message": "",
    }
    try:
        import winreg

        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _USB_STORAGE)
            start = int(winreg.QueryValueEx(key, "Start")[0])
            key.Close()
            result["storageEnabled"] = start == 3      # 3=启用，4=禁用
        except OSError:
            pass

        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _STORAGE_POLICY)
            protect = int(winreg.QueryValueEx(key, "WriteProtect")[0])
            key.Close()
            result["writeProtected"] = protect == 1
        except OSError:
            result["writeProtected"] = False
    except ImportError:
        result["message"] = "当前系统不支持读取该策略"

    if result["storageEnabled"] is None:
        result["message"] = "未读到 USBSTOR 策略（可能是 Windows 家庭版或被组策略接管）"
    return result


def set_usb_policy(storage_enabled: bool | None = None, read_only: bool | None = None) -> dict[str, Any]:
    """切换 USB 存储 / 只读策略 —— 写 HKLM，需要管理员（弹 UAC）。"""
    commands: list[str] = []
    if storage_enabled is not None:
        value = "3" if storage_enabled else "4"
        commands.append(
            f'reg add "HKLM\\{_USB_STORAGE}" /v Start /t REG_DWORD /d {value} /f'
        )
    if read_only is not None:
        commands.append(
            f'reg add "HKLM\\{_STORAGE_POLICY}" /v WriteProtect /t REG_DWORD '
            f"/d {1 if read_only else 0} /f"
        )
    if not commands:
        return {"ok": False, "message": "没有要修改的策略"}

    saved = _backup(
        "usb-policy",
        {"before": usb_status(), "at": time.strftime("%Y-%m-%d %H:%M:%S")},
    )
    try:
        import ctypes

        joined = " & ".join(commands)
        ret = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            None, "runas", "cmd.exe", f"/c {joined}", None, 0
        )
    except Exception as exc:
        return {"ok": False, "message": f"提权失败：{exc}"}
    if ret <= 32:
        return {"ok": False, "message": "已取消（需要管理员权限才能改 USB 策略）"}
    return {
        "ok": True,
        "message": "已请求修改 USB 策略（需在系统弹窗确认；重新插拔 U 盘或重启后生效）",
        "backup": saved,
    }


# ══════════════════════════════════════════════════════════════
# 3. 自启动管理
# ══════════════════════════════════════════════════════════════

_STARTUP_RUN_KEYS = (
    (r"Software\Microsoft\Windows\CurrentVersion\Run", "HKCU"),
    (r"Software\Microsoft\Windows\CurrentVersion\Run", "HKLM"),
    (r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", "HKLM"),
)


def startup_items() -> list[dict[str, Any]]:
    """列出开机自启项（注册表 Run + 启动文件夹），并标注是否已被本工具停用。"""
    items: list[dict[str, Any]] = []
    disabled: dict[str, Any] = config.get("guard_disabled_startup", {}) or {}

    try:
        import winreg

        for path, hive_name in _STARTUP_RUN_KEYS:
            hive = winreg.HKEY_CURRENT_USER if hive_name == "HKCU" else winreg.HKEY_LOCAL_MACHINE
            try:
                key = winreg.OpenKey(hive, path)
            except OSError:
                continue
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                item_id = f"{hive_name}|{path}|{name}"
                items.append(
                    {
                        "id": item_id,
                        "name": name,
                        "command": str(value)[:300],
                        "source": f"注册表 {hive_name}",
                        "location": path,
                        "valueName": name,
                        "restorable": hive_name == "HKCU",
                        "disabled": item_id in disabled,
                    }
                )
            key.Close()
    except ImportError:
        pass

    for folder in _startup_folders():
        try:
            files = [f for f in folder.iterdir() if f.is_file()]
        except OSError:
            continue
        for file in files:
            items.append(
                {
                    "id": f"folder|{folder}|{file.name}",
                    "name": file.name,
                    "command": str(file),
                    "source": f"启动文件夹（{folder.name}）",
                    "location": str(folder),
                    "valueName": file.name,
                    "restorable": True,
                    "disabled": False,
                }
            )
    return items


def _startup_folders() -> list[Path]:
    folders = []
    for base in (os.environ.get("APPDATA"), os.environ.get("PROGRAMDATA")):
        if base:
            folder = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            if folder.is_dir():
                folders.append(folder)
    return folders


def disable_startup(item_id: str) -> dict[str, Any]:
    """停用自启项：注册表值删除前先备份，启动文件夹里的文件移动到备份目录。"""
    items = {item["id"]: item for item in startup_items()}
    item = items.get(item_id)
    if item is None:
        return {"ok": False, "message": "没有找到这个自启项"}
    if not item.get("restorable"):
        return {"ok": False, "message": "这一项位于系统级（HKLM），需要管理员权限，暂不自动停用"}

    disabled = dict(config.get("guard_disabled_startup", {}) or {})
    disabled[item_id] = item
    if not config.set("guard_disabled_startup", disabled):
        return {"ok": False, "message": "保存失败（数据目录不可写）"}

    if item["source"].startswith("注册表"):
        try:
            import winreg

            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, item["location"], 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, item["valueName"])
            key.Close()
        except OSError as exc:
            disabled.pop(item_id, None)
            config.set("guard_disabled_startup", disabled)
            return {"ok": False, "message": f"删除注册表值失败：{exc}"}
        return {"ok": True, "message": f"已停用：{item['name']}（原值已备份，可随时恢复）"}

    # 启动文件夹：移动到备份目录
    source = Path(item["command"])
    target = guard_dir() / "startup" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(source), str(target))
    except OSError as exc:
        return {"ok": False, "message": f"移动失败：{exc}"}
    return {"ok": True, "message": f"已移出启动文件夹：{item['name']}（备份在 {target.parent}）"}


def enable_startup(item_id: str) -> dict[str, Any]:
    """恢复之前停用的自启项。"""
    disabled = dict(config.get("guard_disabled_startup", {}) or {})
    item = disabled.get(item_id)
    if item is None:
        return {"ok": False, "message": "这一项不是本工具停用的，无法自动恢复"}

    if item["source"].startswith("注册表"):
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, item["location"], 0, winreg.KEY_SET_VALUE
            )
            winreg.SetValueEx(key, item["valueName"], 0, winreg.REG_SZ, item["command"])
            key.Close()
        except OSError as exc:
            return {"ok": False, "message": f"恢复失败：{exc}"}
    else:
        source = Path(item["command"])
        backup = guard_dir() / "startup" / source.name
        if backup.is_file():
            try:
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(backup), str(source))
            except OSError as exc:
                return {"ok": False, "message": f"恢复失败：{exc}"}

    disabled.pop(item_id, None)
    config.set("guard_disabled_startup", disabled)
    return {"ok": True, "message": f"已恢复：{item['name']}"}


# ══════════════════════════════════════════════════════════════
# 4. 弹窗管理
# ══════════════════════════════════════════════════════════════

def popup_scan() -> dict[str, Any]:
    """找出规则命中的推广/弹窗进程（只列出，不自动结束）。"""
    hits: list[dict[str, Any]] = []
    try:
        import psutil

        for proc in psutil.process_iter(["pid", "name", "exe", "memory_info", "create_time"]):
            name = str(proc.info.get("name") or "").lower()
            if not name:
                continue
            for label, processes in POPUP_RULES:
                if name in processes:
                    info = proc.info.get("memory_info")
                    hits.append(
                        {
                            "pid": proc.info.get("pid"),
                            "name": proc.info.get("name"),
                            "rule": label,
                            "exe": proc.info.get("exe") or "",
                            "memoryMB": round((info.rss if info else 0) / 1048576, 1),
                            "startedAt": int((proc.info.get("create_time") or 0)),
                        }
                    )
                    break
    except ImportError:
        pass
    except Exception:
        pass
    return {"items": hits, "total": len(hits), "rules": [label for label, _ in POPUP_RULES]}


def popup_kill(pid: int) -> dict[str, Any]:
    """结束命中的弹窗进程（复用进程模块的关键进程保护）。"""
    from .procs import kill_process

    return kill_process(int(pid))


# ── 弹窗守护 / 高占用守护：一个后台线程统一处理 ──

class Watchdog:
    """后台守护：弹窗拦截 + 高占用提醒（都可在设置里开关）。"""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._high_strikes = 0
        self._killed: list[dict[str, Any]] = []

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="oc-guard")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(20.0):
            try:
                self.tick()
            except Exception:
                continue

    def tick(self) -> None:
        settings = config.get("guard_settings", {}) or {}
        if settings.get("popup_guard"):
            report = popup_scan()
            for item in report["items"]:
                outcome = popup_kill(int(item["pid"]))
                if outcome.get("ok"):
                    self._killed.append({**item, "at": int(time.time())})
                    del self._killed[:-50]
                    try:
                        from ..system.notify import notify

                        notify(
                            "已拦截推广弹窗",
                            f"{item['name']}（{item['rule']}）已被结束",
                            key=f"popup-{item['name']}",
                            interval=60,
                        )
                    except Exception:
                        pass

        if settings.get("high_usage_guard"):
            heavy = high_usage()
            if heavy["items"]:
                self._high_strikes += 1
                # 连续 3 次（约 1 分钟）都超阈值才提醒，避免瞬时波动误报
                if self._high_strikes >= 3:
                    self._high_strikes = 0
                    top = heavy["items"][0]
                    try:
                        from ..system.notify import notify

                        notify(
                            "有程序占用异常偏高",
                            f"{top['name']}：CPU {top['cpu']:.0f}% / 内存 {top['memoryMB']:.0f} MB",
                            key=f"heavy-{top['name']}",
                            interval=300,
                        )
                    except Exception:
                        pass
            else:
                self._high_strikes = 0

    def killed(self) -> list[dict[str, Any]]:
        return list(reversed(self._killed[-30:]))


watchdog = Watchdog()


def guard_settings() -> dict[str, Any]:
    raw = config.get("guard_settings", {}) or {}
    return {
        "popup_guard": bool(raw.get("popup_guard", False)),
        "high_usage_guard": bool(raw.get("high_usage_guard", False)),
        "cpuThreshold": float(raw.get("cpuThreshold", WATCH_CPU)),
        "memThresholdMB": int(raw.get("memThresholdMB", WATCH_MEM_MB)),
    }


def set_guard_settings(
    popup_guard: bool | None = None,
    high_usage_guard: bool | None = None,
    cpu_threshold: float | None = None,
    mem_threshold_mb: int | None = None,
) -> dict[str, Any]:
    raw = dict(config.get("guard_settings", {}) or {})
    if popup_guard is not None:
        raw["popup_guard"] = bool(popup_guard)
    if high_usage_guard is not None:
        raw["high_usage_guard"] = bool(high_usage_guard)
    if cpu_threshold is not None:
        raw["cpuThreshold"] = max(30.0, min(99.0, float(cpu_threshold)))
    if mem_threshold_mb is not None:
        raw["memThresholdMB"] = max(200, min(32768, int(mem_threshold_mb)))
    ok = config.set("guard_settings", raw)
    if any([popup_guard, high_usage_guard]):
        watchdog.start()
    return {"ok": ok, "settings": guard_settings()}


# ══════════════════════════════════════════════════════════════
# 5. 异常高占用
# ══════════════════════════════════════════════════════════════

def high_usage(cpu_threshold: float | None = None, mem_mb: int | None = None) -> dict[str, Any]:
    """找出 CPU 或内存占用超过阈值的进程（只读，不结束）。"""
    settings = guard_settings()
    cpu_limit = float(cpu_threshold if cpu_threshold is not None else settings["cpuThreshold"])
    mem_limit = int(mem_mb if mem_mb is not None else settings["memThresholdMB"])

    items: list[dict[str, Any]] = []
    try:
        import psutil

        cores = psutil.cpu_count(logical=True) or 1

        # 首次调用先建立基线，随后取瞬时值
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                proc.cpu_percent(None)
            except Exception:
                continue
        time.sleep(0.6)

        for proc in psutil.process_iter(["pid", "name", "memory_info", "exe"]):
            try:
                pid = int(proc.info.get("pid") or 0)
                name = str(proc.info.get("name") or "")
                # 空闲进程与内核进程不是「应用」，算进来只会误报
                if pid in (0, 4) or name in ("System Idle Process", "Idle", "System"):
                    continue
                # psutil 返回的是「占单个核心的百分比」，除以核心数才是占整机的比例
                cpu = float(proc.cpu_percent(None)) / cores
                info = proc.info.get("memory_info")
                memory_mb = (info.rss / 1048576) if info else 0
            except Exception:
                continue
            if cpu >= cpu_limit or memory_mb >= mem_limit:
                items.append(
                    {
                        "pid": pid,
                        "name": name,
                        "cpu": round(cpu, 1),
                        "memoryMB": round(memory_mb, 1),
                        "exe": proc.info.get("exe") or "",
                        "cores": cores,
                        "reason": (
                            f"占整机 CPU {cpu:.0f}%（阈值 {cpu_limit:.0f}%）"
                            if cpu >= cpu_limit
                            else f"内存 {memory_mb:.0f} MB 超过阈值 {mem_limit} MB"
                        ),
                    }
                )
    except ImportError:
        pass

    items.sort(key=lambda i: (-i["cpu"], -i["memoryMB"]))
    return {
        "items": items[:20],
        "total": len(items),
        "cpuThreshold": cpu_limit,
        "memThresholdMB": mem_limit,
    }


def kill_high_usage(pid: int) -> dict[str, Any]:
    from .procs import kill_process

    return kill_process(int(pid))


# ══════════════════════════════════════════════════════════════
# 汇总
# ══════════════════════════════════════════════════════════════

def overview() -> dict[str, Any]:
    """安全扩展总览（供界面一次拉齐）。"""
    return {
        "hijack": scan_hijack(),
        "usb": usb_status(),
        "startup": {"items": startup_items()},
        "popup": popup_scan(),
        "highUsage": {"cpuThreshold": guard_settings()["cpuThreshold"], "memThresholdMB": guard_settings()["memThresholdMB"]},
        "guard": guard_settings(),
        "platform": platform.system(),
    }
