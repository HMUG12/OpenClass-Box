"""开机自动执行的任务（自动化模式）。

**边界：只做无损的事。** 这一条是整个模块的立身之本 ——
自动化意味着没人盯着它跑，做错了没人能当场发现。所以：

  做得了
    · 巡检        体检 + 课堂检测 + 还原保护，出一句结论
    · 清临时文件   只清系统缓存/临时目录（cleanup 模块自己定的白名单）
    · 整理内存     把闲置内存页换到页面文件，物理内存立刻降下来
  只建议，不动手
    · 开机自启项   列出来供人判断
    · 高占用进程   列出来供人判断
  绝不做
    · 结束任何进程      可能关掉老师正在用的软件
    · 改注册表 / 服务   影响面大，出错代价高
    · 卸载 / 删除用户文件

整理内存为什么算"无损"：它只是让系统把不用的页从物理内存挪到页面文件，
不结束进程、不丢数据。代价是下次用到那部分内存时可能有轻微换页延迟 ——
对一体机（通常 8~16 GB）来说，这个代价换来开机后内存明显干净，是划算的。
"""
from __future__ import annotations

import os
import time
from typing import Any

_PROCESS_SET_QUOTA = 0x0100
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _trim_working_sets(limit: int = 12) -> dict[str, Any]:
    """把各进程的闲置内存页换出到页面文件。

    limit 大于 0 时只处理内存占用最高的前 N 个 —— 开机后进程数上百时
    全量遍历反而拖慢启动，而真正占内存的通常就那么几个。
    """
    result: dict[str, Any] = {"ok": False, "trimmed": 0, "freedMB": 0, "message": ""}
    if os.name != "nt":
        result["message"] = "非 Windows，跳过"
        return result

    try:
        import ctypes
        from ctypes import wintypes

        import psutil
    except ImportError as exc:
        result["message"] = f"缺少依赖：{exc}"
        return result

    try:
        before = psutil.virtual_memory().used / 1024 / 1024
    except Exception:
        before = 0.0

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    psapi.EmptyWorkingSet.argtypes = (wintypes.HANDLE,)
    psapi.EmptyWorkingSet.restype = wintypes.BOOL

    access = _PROCESS_SET_QUOTA | _PROCESS_QUERY_LIMITED_INFORMATION
    candidates: list[tuple[int, int]] = []
    for proc in psutil.process_iter(["pid", "memory_info"]):
        try:
            info = proc.info.get("memory_info")
            if info is not None:
                candidates.append((info.rss, int(proc.info["pid"])))
        except Exception:
            continue

    candidates.sort(reverse=True)
    if limit > 0:
        candidates = candidates[:limit]

    trimmed = 0
    for _rss, pid in candidates:
        try:
            handle = kernel32.OpenProcess(access, False, pid)
        except Exception:
            continue
        if not handle:
            continue
        try:
            if psapi.EmptyWorkingSet(handle):
                trimmed += 1
        except Exception:
            pass
        finally:
            kernel32.CloseHandle(handle)

    try:
        after = psutil.virtual_memory().used / 1024 / 1024
    except Exception:
        after = before

    result.update(
        ok=True,
        trimmed=trimmed,
        freedMB=round(max(0.0, before - after), 1),
        message=f"已整理 {trimmed} 个进程的工作集",
    )
    return result

def _startup_advice() -> dict[str, Any]:
    """开机自启项：**只列出来**，不禁用。

    自启项该不该停取决于这台机器在干什么（学校统一装机的东西可能必须留着），
    自动化模式也不该替人做这个决定。
    """
    try:
        from .guard import startup_items

        items = startup_items()
    except Exception as exc:
        return {"ok": False, "count": 0, "items": [], "message": f"读取失败：{exc}"}
    enabled = [one for one in items if not one.get("disabled")]
    return {
        "ok": True,
        "count": len(enabled),
        "items": [
            {"name": one.get("name") or one.get("id"), "source": one.get("source")}
            for one in enabled[:12]
        ],
        "message": (
            f"有 {len(enabled)} 个开机自启项，需要时可到「安全」页停用"
            if enabled else "开机自启项不多"
        ),
    }


def _high_usage_advice() -> dict[str, Any]:
    """高占用进程：**只列出来**，不结束。"""
    try:
        from .guard import high_usage

        data = high_usage()
    except Exception as exc:
        return {"ok": False, "count": 0, "items": [], "message": f"读取失败：{exc}"}
    items = data.get("processes") or data.get("items") or []
    return {
        "ok": True,
        "count": len(items),
        "items": [
            {"pid": one.get("pid"), "name": one.get("name"), "reason": one.get("reason")}
            for one in items[:8]
        ],
        "message": (
            f"有 {len(items)} 个进程占用偏高，可在「安全」页查看" if items else "没有明显占用的进程"
        ),
    }


def run_checkup() -> dict[str, Any]:
    """开机巡检（结论与课前准备同源，判定规则完全一致）。"""
    try:
        from .preflight import run

        result = run()
        return {
            "ok": True,
            "verdict": result.get("verdict"),
            "verdictLabel": result.get("verdictLabel"),
            "headline": result.get("headline"),
            "blocking": [one.get("title") for one in result.get("blocking") or []],
            "attention": [one.get("title") for one in result.get("attention") or []],
        }
    except Exception as exc:
        return {"ok": False, "verdict": "", "message": f"巡检失败：{exc}"}


def run_cleanup() -> dict[str, Any]:
    """清临时文件（只清 cleanup 模块白名单里的系统缓存目录）。"""
    try:
        from .cleanup import analyze, run

        keys = [one["key"] for one in analyze().get("items", []) if one.get("exists")]
        if not keys:
            return {"ok": True, "removed": 0, "freedMB": 0, "message": "没有可清理的临时文件"}
        outcome = run(keys)
        return {
            "ok": True,
            "removed": int(outcome.get("removed") or 0),
            "freedMB": round(float(outcome.get("freed") or 0) / 1024 / 1024, 1),
            "message": outcome.get("message") or "已清理",
        }
    except Exception as exc:
        return {"ok": False, "message": f"清理失败：{exc}"}


def run_startup_tasks(trim: int = 12) -> dict[str, Any]:
    """开机跑一轮：巡检 + 清临时文件 + 整理内存 + 两条建议。

    每一步都**独立 try**：某一步挂了不能连累后面的步骤 —— 自动化最怕的是
    "整理内存失败 → 整个任务崩 → 连巡检结果都没有"。
    """
    from .applog import log

    started = time.time()
    result: dict[str, Any] = {
        "ok": True,
        "startedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "steps": {},
    }
    plan = (
        ("checkup", run_checkup),
        ("cleanup", run_cleanup),
        ("memory", lambda: _trim_working_sets(limit=trim)),
        ("startup", _startup_advice),
        ("highUsage", _high_usage_advice),
    )
    for name, func in plan:
        try:
            result["steps"][name] = func()
        except Exception as exc:      # noqa: BLE001 —— 任何一步都不许连累其他
            result["steps"][name] = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
        result["steps"][name]["at"] = time.strftime("%H:%M:%S")

    result["elapsed"] = round(time.time() - started, 1)
    failures = [key for key, value in result["steps"].items() if not value.get("ok")]
    result["ok"] = not failures
    result["failedSteps"] = failures
    save_result(result)
    log(
        f"开机任务完成，用时 {result['elapsed']} 秒",
        "INFO" if not failures else "WARN",
        failed=",".join(failures),
    )
    return result


def should_run_on_start() -> bool:
    """当前配置下是否应该在启动时自动跑一轮。"""
    from .mode import is_auto

    return is_auto()

# ══════════════════════════════════════════════════════════════
# 上次结果（首页显示）
# ══════════════════════════════════════════════════════════════


def _result_path():
    from .paths import config_dir

    return config_dir() / "auto_tasks_last.json"


def save_result(result: dict) -> None:
    """留档给首页显示。写失败绝不影响本次任务已经做完的事。"""
    try:
        import json

        keep = {
            "ok": result.get("ok"),
            "startedAt": result.get("startedAt"),
            "elapsed": result.get("elapsed"),
            "failedSteps": result.get("failedSteps") or [],
            "steps": result.get("steps") or {},
        }
        _result_path().write_text(
            json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except (OSError, TypeError, ValueError):
        pass


def last() -> dict:
    """上次开机任务的结果；没有或读不出来就如实说没跑过。"""
    import json

    try:
        data = json.loads(_result_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "hasResult": False, "message": "还没有自动执行过"}
    if not isinstance(data, dict) or not data.get("startedAt"):
        return {"ok": False, "hasResult": False, "message": "还没有自动执行过"}
    data["hasResult"] = True
    return data