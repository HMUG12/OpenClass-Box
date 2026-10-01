"""
还原保护状态检测 —— 不替代还原卡，只盯住它**有没有真的在工作**。

为什么需要它：

  教室一体机大多装了冰点还原 / 影子系统 / 慧盾 / 希沃一键还原，但
  **"装了"不等于"在保护"**：
    · 有人为了装软件临时关了保护，装完忘了开；
    · 保护服务被安全软件禁用或结束；
    · 只剩服务在、保护进程没起来（半死状态）。

  这几种情况下，学生改的东西会**真的写进系统盘** —— 下次上课可能莫名
  出问题，而没人知道原因。我们要做的就是把它标进维护清单。

边界（写在明处）：

  · **只读取状态，绝不代它开关保护** —— 那是还原软件自己的职责，
    越权操作一旦出错就是"把保护关掉了"，责任无法承担；
  · 检测不到软件类保护时**不判为问题** —— 硬件还原卡（BIOS 级）与云桌面
    本来就不在软件层，不能因为没检测到就说人家没保护；
  · 服务状态读取在部分环境下需要管理员权限，读不到就如实说明。
"""
from __future__ import annotations

from typing import Any

# 复用课堂检测里的识别名单，避免两处各维护一份而漂移
from .classroom import _GUARDS as GUARDS

VERDICTS = {
    "ok": "保护中",
    "no-process": "状态可疑",
    "stopped": "保护已停止",
    "disabled": "保护已被禁用",
}


def _services() -> dict[str, dict[str, Any]]:
    """系统服务快照：名称（小写）→ 状态 / 启动类型 / 显示名。"""
    result: dict[str, dict[str, Any]] = {}
    try:
        import psutil

        for service in psutil.win_service_iter():
            try:
                info = service.as_dict()
            except Exception:
                continue
            name = str(info.get("name") or "").strip().lower()
            if not name:
                continue
            result[name] = {
                "name": str(info.get("name") or ""),
                "status": str(info.get("status") or "").lower(),
                "startType": str(info.get("start_type") or "").lower(),
                "displayName": str(info.get("display_name") or ""),
            }
    except Exception:
        pass
    return result


def _processes() -> set[str]:
    names: set[str] = set()
    try:
        import psutil

        for proc in psutil.process_iter(["name"]):
            try:
                names.add(str(proc.info.get("name") or "").lower())
            except Exception:
                continue
    except Exception:
        pass
    return names


def _normalized(services: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """把服务快照的键统一成小写。

    真实系统返回的服务名大小写并不统一（DFServ / dfserv / DFSERV 都可能），
    这里做一层归一化，避免因为一个大小写差异就"检测不到保护" ——
    那会导致我们在维护清单里漏报，比误报更糟。
    """
    return {str(key).lower(): value for key, value in services.items()}


def detect() -> dict[str, Any]:
    """检测还原保护软件及其**是否有效**。"""
    services = _normalized(_services())
    processes = _processes()
    products: list[dict[str, Any]] = []

    for guard in GUARDS:
        service_hits = [n for n in guard.get("services", ()) if n.lower() in services]
        process_hits = [p for p in guard.get("processes", ()) if p.lower() in processes]
        if not service_hits and not process_hits:
            continue

        states = [services[name.lower()] for name in service_hits if name.lower() in services]
        running_service = any(state["status"] == "running" for state in states)
        disabled = any("disabled" in state["startType"] for state in states)

        if disabled:
            verdict, level = "disabled", "warn"
        elif not running_service and not process_hits:
            verdict, level = "stopped", "warn"
        elif not process_hits:
            # 服务在跑但没有保护进程：可能是新版改了进程名，也可能是半死状态
            verdict, level = "no-process", "watch"
        else:
            verdict, level = "ok", "ok"

        products.append(
            {
                "name": guard.get("name", "还原保护"),
                "verdict": verdict,
                "verdictLabel": VERDICTS.get(verdict, verdict),
                "level": level,
                "services": [
                    {
                        "name": state["name"],
                        "status": state["status"],
                        "startType": state["startType"],
                    }
                    for state in states
                ],
                "processRunning": bool(process_hits),
            }
        )

    if not products:
        return {
            "installed": False,
            "level": "ok",
            "levelLabel": "正常",
            "reasons": [],
            "products": [],
            "detail": "未检测到软件类还原保护（冰点 / 影子系统 / 慧盾 / 希沃一键还原）"
            "—— 若这台机器用的是硬件还原卡或云桌面，请忽略这一项",
            "advice": "",
        }

    problems = [item for item in products if item["verdict"] != "ok"]
    level = "ok"
    if any(item["level"] == "warn" for item in problems):
        level = "warn"
    elif problems:
        level = "watch"

    reasons: list[str] = []
    for item in problems:
        if item["verdict"] == "disabled":
            reasons.append(f"{item['name']}：服务启动类型为「已禁用」")
        elif item["verdict"] == "stopped":
            reasons.append(f"{item['name']}：服务未在运行")
        else:
            reasons.append(f"{item['name']}：服务在运行但保护进程未启动")

    advice = ""
    if level == "warn":
        advice = (
            "这台机器的还原保护可能已经失效 —— 学生改动会真正写入系统盘。"
            "请打开还原软件确认保护状态；如果确实是「为装软件临时关闭」，装完记得重新开启"
        )
    elif level == "watch":
        advice = "建议打开还原软件确认一下保护状态（可能是版本更新改了进程名）"

    labels = {"ok": "正常", "watch": "关注", "warn": "预警"}
    return {
        "installed": True,
        "level": level,
        "levelLabel": labels.get(level, level),
        "reasons": reasons,
        "products": products,
        "detail": "、".join(
            f"{item['name']}（{item['verdictLabel']}）" for item in products
        ),
        "advice": advice,
    }


def summary_line() -> str:
    """给状态页/诊断包用的一句话。"""
    result = detect()
    if not result["installed"]:
        return "未检测到软件类还原保护"
    return result["detail"]
