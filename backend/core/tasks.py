"""
本机定时任务 —— 让一体机按点自己做事。

教室里的实际用法：
  · 每天早上 7:50 自动跑一次体检，把结果写进报告
  · 每周五放学后自动清理临时文件
  · 上课前自动打开白板软件、统一换壁纸
  · 考试前用批处理关掉无关进程

支持的动作：命令（cmd /c）、脚本（.bat .cmd .ps1 .vbs）、程序（.exe 等）
支持的时间：每天 / 每周（指定星期）/ 一次性（指定日期时间）/ 开机后延迟

安全与可靠性（这块必须稳，因为它会自动执行）：
  1. **总开关默认关闭**：不开总开关，任何任务都不会执行；
  2. **任务逐条可停用**：改一个任务不影响其他；
  3. **执行留痕**：每次执行写运行日志 + 本地记录（成功/失败/耗时/输出摘要）；
  4. **退出提示**：还有启用中的任务时，退出程序会先问一句；
  5. **超时保护**：单次执行默认 300 秒，超时自动终止，避免卡死；
  6. **不做自动发现**：任务目标只能由用户显式添加，绝不自己猜。
"""
from __future__ import annotations

import json
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .config import config
from .paths import config_dir

CHECK_INTERVAL = 20.0       # 调度轮询间隔
RUN_TIMEOUT = 300           # 单次执行超时（秒）
MAX_LOG_LINES = 300         # 本地执行记录条数上限

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 失败通知回调（托盘气泡），由 main 启动时注入 —— 避免这里反向依赖界面层
_notify = None


def set_notifier(func) -> None:
    """注入通知回调：定时任务失败时弹一次托盘气泡。

    定时任务是后台跑的，用户不会盯着日志；失败了至少要"冒个泡"，
    否则任务静默失效、直到某天才被发现。
    """
    global _notify
    _notify = func

KINDS: dict[str, str] = {
    "command": "命令",
    "script": "脚本",
    "program": "程序",
}

TRIGGERS: dict[str, str] = {
    "daily": "每天",
    "weekly": "每周",
    "once": "一次性",
    "boot": "开机后",
}

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

_lock = threading.RLock()


# ══════════════════════════════════════════════════════════════
# 存储
# ══════════════════════════════════════════════════════════════

def _default_state() -> dict[str, Any]:
    return {"enabled": False, "items": []}


def state() -> dict[str, Any]:
    raw = config.get("local_tasks", None)
    if not isinstance(raw, dict):
        return _default_state()
    merged = _default_state()
    merged.update(raw)
    if not isinstance(merged.get("items"), list):
        merged["items"] = []
    return merged


def _save(data: dict[str, Any]) -> bool:
    return config.set("local_tasks", data)


def set_enabled(value: bool) -> dict[str, Any]:
    data = state()
    data["enabled"] = bool(value)
    ok = _save(data)
    return {
        "ok": ok,
        "enabled": bool(value),
        "message": "定时任务总开关已开启" if value else "定时任务总开关已关闭（任务不会执行）",
    }


def has_enabled_tasks() -> bool:
    """是否有启用中的任务（退出程序时用它决定要不要提示）。"""
    data = state()
    return bool(data.get("enabled")) and any(
        item.get("enabled") for item in data.get("items", [])
    )


def enabled_count() -> int:
    data = state()
    if not data.get("enabled"):
        return 0
    return sum(1 for item in data.get("items", []) if item.get("enabled"))


# ══════════════════════════════════════════════════════════════
# 增删改
# ══════════════════════════════════════════════════════════════

def add(
    name: str,
    kind: str,
    target: str,
    args: str = "",
    workdir: str = "",
    trigger_type: str = "daily",
    run_time: str = "08:00",
    weekdays: list[int] | None = None,
    once_at: str = "",
    boot_delay: int = 60,
) -> dict[str, Any]:
    if kind not in KINDS:
        return {"ok": False, "message": f"不支持的动作类型：{kind}"}
    if trigger_type not in TRIGGERS:
        return {"ok": False, "message": f"不支持的触发方式：{trigger_type}"}
    target = (target or "").strip().strip('"')
    if not target:
        return {"ok": False, "message": "请填写要执行的内容"}

    item = {
        "id": f"t{int(time.time() * 1000)}",
        "name": (name or target)[:60],
        "kind": kind,
        "target": target,
        "args": [a for a in str(args or "").split() if a],
        "workdir": (workdir or "").strip().strip('"'),
        "trigger": {
            "type": trigger_type,
            "time": run_time or "08:00",
            "weekdays": list(weekdays or []),
            "onceAt": once_at or "",
            "bootDelay": max(10, int(boot_delay or 60)),
        },
        "enabled": True,
        "lastRun": "",
        "lastResult": "",
        "runCount": 0,
        "createdAt": int(time.time()),
    }
    with _lock:
        data = state()
        data["items"].append(item)
        if not _save(data):
            return {"ok": False, "message": "保存失败（数据目录不可写）"}
    return {"ok": True, "message": f"已添加任务：{item['name']}", "item": item}


def update(item_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        data = state()
        for item in data["items"]:
            if item.get("id") != item_id:
                continue
            for key in ("name", "target", "workdir", "enabled"):
                if key in patch:
                    item[key] = patch[key]
            if "args" in patch and isinstance(patch["args"], list):
                item["args"] = [str(a) for a in patch["args"]]
            if "trigger" in patch and isinstance(patch["trigger"], dict):
                item["trigger"] = {**item.get("trigger", {}), **patch["trigger"]}
            if not _save(data):
                return {"ok": False, "message": "保存失败"}
            return {"ok": True, "message": "已更新", "item": item}
    return {"ok": False, "message": "没有找到这个任务"}


def remove(item_id: str) -> dict[str, Any]:
    with _lock:
        data = state()
        before = len(data["items"])
        data["items"] = [i for i in data["items"] if i.get("id") != item_id]
        if len(data["items"]) == before:
            return {"ok": False, "message": "没有找到这个任务"}
        if not _save(data):
            return {"ok": False, "message": "保存失败"}
    return {"ok": True, "message": "任务已删除"}


# ══════════════════════════════════════════════════════════════
# 执行
# ══════════════════════════════════════════════════════════════

def _build_command(item: dict[str, Any]) -> list[str]:
    """把任务翻译成命令行（纯函数，便于安全测试）。"""
    kind = str(item.get("kind") or "command")
    target = str(item.get("target") or "")
    args = [str(a) for a in (item.get("args") or [])]

    if kind == "command":
        return ["cmd.exe", "/c", target, *args]

    if kind == "program":
        return [target, *args]

    # script：按后缀挑解释器
    suffix = Path(target).suffix.lower()
    if suffix in (".bat", ".cmd"):
        return ["cmd.exe", "/c", target, *args]
    if suffix == ".ps1":
        return [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            target,
            *args,
        ]
    if suffix == ".vbs":
        return ["cscript.exe", "//nologo", target, *args]
    if suffix == ".py":
        return [sys.executable, target, *args]
    return ["cmd.exe", "/c", target, *args]


def run_now(item_id: str) -> dict[str, Any]:
    """立即执行一个任务（不等调度）。"""
    data = state()
    item = next((i for i in data["items"] if i.get("id") == item_id), None)
    if item is None:
        return {"ok": False, "message": "没有找到这个任务"}
    outcome = _execute(item)
    return {
        "ok": bool(outcome.get("ok")),
        "message": f"已执行：{item.get('name')}（{outcome.get('message')}）",
        "result": outcome,
    }


def _execute(item: dict[str, Any]) -> dict[str, Any]:
    """真正执行一条任务，并把结果记进本地记录。"""
    command = _build_command(item)
    workdir = str(item.get("workdir") or "").strip()
    started = time.time()
    ok = False
    detail = ""

    try:
        proc = subprocess.run(
            command,
            cwd=workdir or None,
            capture_output=True,
            timeout=RUN_TIMEOUT,
            creationflags=_CREATE_NO_WINDOW,
        )
        ok = proc.returncode == 0
        output = (proc.stdout or b"").decode("gbk", "ignore") or (proc.stderr or b"").decode(
            "gbk", "ignore"
        )
        detail = f"返回码 {proc.returncode}"
        if output.strip():
            detail += f" · 输出：{output.strip().splitlines()[0][:120]}"
    except subprocess.TimeoutExpired:
        detail = f"超时（超过 {RUN_TIMEOUT} 秒已终止）"
    except FileNotFoundError:
        detail = "找不到要执行的文件"
    except OSError as exc:
        detail = f"执行失败：{exc}"

    elapsed = round(time.time() - started, 1)

    if not ok and _notify is not None:
        try:
            _notify("定时任务失败", f"{item.get('name') or '未命名'}：{detail}")
        except Exception:
            pass

    record = {
        "ts": int(time.time() * 1000),
        "name": str(item.get("name") or ""),
        "ok": ok,
        "detail": detail,
        "elapsed": elapsed,
    }

    # 写本地记录 + 运行日志（出问题时都能查）
    try:
        path = config_dir() / "tasks" / "log.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_LOG_LINES:
            path.write_text("\n".join(lines[-MAX_LOG_LINES:]) + "\n", encoding="utf-8")
    except OSError:
        pass
    try:
        from .applog import log

        log(
            f"定时任务执行：{record['name']}",
            "INFO" if ok else "WARN",
            result=detail,
            seconds=elapsed,
        )
    except Exception:
        pass

    # 回写任务状态
    with _lock:
        data = state()
        for target in data["items"]:
            if target.get("id") == item.get("id"):
                target["lastRun"] = time.strftime("%Y-%m-%d %H:%M:%S")
                target["lastResult"] = detail
                target["runCount"] = int(target.get("runCount") or 0) + 1
                if target.get("trigger", {}).get("type") == "once":
                    target["enabled"] = False   # 一次性任务跑完自动停用
                break
        _save(data)

    return {"ok": ok, "message": detail, "elapsed": elapsed}


def logs(limit: int = 50) -> list[dict[str, Any]]:
    path = config_dir() / "tasks" / "log.jsonl"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    items: list[dict[str, Any]] = []
    for line in lines[-max(1, limit):]:
        try:
            items.append(json.loads(line))
        except ValueError:
            continue
    return list(reversed(items))


# ══════════════════════════════════════════════════════════════
# 调度
# ══════════════════════════════════════════════════════════════

class TaskRunner:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._booted = False
        self._started_at = time.time()
        self._last_fired: dict[str, str] = {}   # itemId → "2026-09-27 08:00"

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="oc-tasks")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(CHECK_INTERVAL):
            try:
                self.tick()
            except Exception:
                continue

    def _due(self, item: dict[str, Any], now: time.struct_time) -> bool:
        trigger = item.get("trigger") or {}
        kind = str(trigger.get("type") or "daily")
        stamp = time.strftime("%Y-%m-%d %H:%M", now)
        key = f"{item.get('id')}"

        if kind == "boot":
            if self._booted:
                return False
            delay = int(trigger.get("bootDelay") or 60)
            if time.time() - self._started_at < delay:
                return False
            self._booted = True
            return True

        current = f"{now.tm_hour:02d}:{now.tm_min:02d}"
        if kind == "daily":
            if current != str(trigger.get("time") or "08:00")[:5]:
                return False
        elif kind == "weekly":
            weekdays = trigger.get("weekdays") or []
            if (now.tm_wday) not in weekdays:      # 0=周一
                return False
            if current != str(trigger.get("time") or "08:00")[:5]:
                return False
        elif kind == "once":
            target = str(trigger.get("onceAt") or "")
            if not target or stamp < target:
                return False
        else:
            return False

        if self._last_fired.get(key) == stamp:
            return False
        self._last_fired[key] = stamp
        return True

    def tick(self) -> list[str]:
        """检查一次，返回本次执行的任务名列表。"""
        data = state()
        if not data.get("enabled"):
            return []

        now = time.localtime()
        fired: list[str] = []
        for item in data.get("items", []):
            if not item.get("enabled"):
                continue
            if not self._due(item, now):
                continue
            outcome = _execute(item)
            fired.append(f"{item.get('name')}（{outcome.get('message')}）")
        return fired

    def next_runs(self) -> list[dict[str, Any]]:
        """给界面用：预估每个任务的下次执行时间（纯计算，不执行）。"""
        data = state()
        now = time.localtime()
        result: list[dict[str, Any]] = []
        for item in data.get("items", []):
            trigger = item.get("trigger") or {}
            kind = str(trigger.get("type") or "daily")
            label = TRIGGERS.get(kind, kind)
            when = "—"
            if kind in ("daily", "weekly"):
                when = f"{label} {trigger.get('time') or '08:00'}"
                if kind == "weekly":
                    days = [
                        WEEKDAYS[d] for d in (trigger.get("weekdays") or []) if 0 <= int(d) <= 6
                    ]
                    when += f"（{'、'.join(days) or '未选星期'}）"
            elif kind == "once":
                when = f"{label} {trigger.get('onceAt') or '未设置时间'}"
            elif kind == "boot":
                when = f"{label} {trigger.get('bootDelay') or 60} 秒"
            result.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "enabled": bool(item.get("enabled")),
                    "when": when,
                    "lastRun": item.get("lastRun") or "",
                    "lastResult": item.get("lastResult") or "",
                    "runCount": int(item.get("runCount") or 0),
                }
            )
        return result


runner = TaskRunner()
