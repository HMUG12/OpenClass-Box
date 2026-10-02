"""
课前准备 —— 一次点击，回答"这台机器现在能不能上课"。

为什么值得单独做一层：

  平时老师是这样用的：点体检看一遍 → 切到课堂检测再看一遍 → 再想想还原保护
  是不是开着……三个页面、十几条信息，最后得自己做判断。而课前那几分钟恰恰
  最紧张（学生已经坐在教室里了）。

  这个模块把三类检测串成一条流程，并**替老师做那个判断**：分出
  「真的上不了课」和「能上课但有影响」，前者顶到最前面并给处理建议，
  后者收成一句提醒。

判断依据是显式的（见 BLOCKING）并且可解释 —— 工具不该用红黄绿灯吓人，
而该说清"为什么"。也正因为如此，这里的结论可以放心显示在手机端和
远程汇总里：结论短、理由在，老师不需要理解检测细节。
"""
from __future__ import annotations

import time
from typing import Any

from .diag_result import (
    from_classroom,
    from_health,
    item,
    only_issues,
    sort_items,
    summarize,
)

# 真正"上不了课"的问题：网络断、没有显示设备、没有声音设备。
# 其余（投影还没接、触摸不灵、教学软件缺失、还原保护停了）都归"能用但有影响"——
# 老师在紧急情况下常常是"先凑合上完这节课"，工具要把这个区别说清楚，
# 而不是一律报红。
BLOCKING: dict[str, str] = {
    "health.network": "网络不通：互动课堂、资源下载、云课件都会受影响",
    "health.display": "没有可用显示设备：屏幕或投影可能没接好",
    "health.sound": "没有音频输出设备：需要放音的课会受影响",
}

VERDICTS = {
    "ready": "可以上课",
    "attention": "可以上课，有几点建议看一眼",
    "blocked": "建议先处理再上课",
}


def _restore_item() -> dict[str, Any] | None:
    """还原保护：正常也给一条（老师要看到"它是开着的"），异常则说明原因。"""
    try:
        from .restore_watch import detect

        result = detect()
    except Exception:
        return None

    level = str(result.get("level") or "ok")
    reasons = "；".join(result.get("reasons") or []) or str(result.get("detail") or "")
    if level in ("watch", "warn", "replace"):
        return item(
            "preflight.restore",
            "还原保护",
            level,
            reasons or "保护状态异常",
            advice=str(result.get("advice") or ""),
            details={"level": level},
            source="restore",
            manual=True,       # 还原软件怎么开，各家不一样，得人工确认
        )
    return item(
        "preflight.restore",
        "还原保护",
        "ok",
        reasons or "保护中",
        details={"level": level},
        source="restore",
    )


def _classify(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把问题分成「卡课」与「有影响」两组。"""
    issues = only_issues(items)
    blocking = [one for one in issues if one["id"] in BLOCKING]
    others = [one for one in issues if one["id"] not in BLOCKING]
    return blocking, others


def _headline(
    verdict: str, blocking: list[dict[str, Any]], others: list[dict[str, Any]]
) -> str:
    if verdict == "blocked" and blocking:
        first = blocking[0]
        why = BLOCKING.get(first["id"], "")
        text = f"{first['title']}：{first['summary']}"
        if len(blocking) > 1:
            text += f"（另有 {len(blocking) - 1} 项同类问题）"
        return text + (f"　—　{why}" if why else "")
    if verdict == "attention":
        preview = "；".join(f"{one['title']} {one['summary']}" for one in others[:2])
        return f"有 {len(others)} 项建议课前看一眼：{preview}"
    return "各项检查正常，可以直接开始上课"


def verdict_for(items: list[dict[str, Any]]) -> dict[str, Any]:
    """只做判定、不跑检测 —— 给已经拿到统一项的场景复用。

    为什么要把判定拆出来：手机控制台只跑体检（几秒），桌面的课前准备还会跑
    课堂检测（几十秒）。**但两处的判定规则必须完全一样** —— 同一个问题在
    手机上说"可以上课"、在电脑上说"建议先处理"，是最伤信任的一类 bug。
    所以结论从这里出，两边都只负责"收集条目"。
    """
    blocking, others = _classify(items)
    verdict = "blocked" if blocking else ("attention" if others else "ready")
    return {
        "verdict": verdict,
        "verdictLabel": VERDICTS[verdict],
        "headline": _headline(verdict, blocking, others),
        "blocking": blocking,
        "attention": others,
    }


def run() -> dict[str, Any]:
    """跑一遍课前检查（体检 + 课堂 + 还原保护），返回可直接上屏的结论。

    耗时约 10~30 秒（各项检测本身耗时），由用户点击触发。
    任一来源失败都会在 errors 里说明，其余结果照常返回 ——
    "一部分没测出来"和"整台机器不能用"是两回事。
    """
    items: list[dict[str, Any]] = []
    errors: list[str] = []

    try:
        from .health import run_checks

        items += from_health(run_checks().get("items") or [])
    except Exception as exc:  # noqa: BLE001 —— 单项失败不影响整体结论
        errors.append(f"体检未完成（{exc}）")

    try:
        from .classroom import report as classroom_report

        items += from_classroom(classroom_report().get("items") or [])
    except Exception as exc:  # noqa: BLE001
        errors.append(f"课堂检测未完成（{exc}）")

    restore = _restore_item()
    if restore:
        items.append(restore)

    blocking, others = _classify(items)
    verdict = "blocked" if blocking else ("attention" if others else "ready")
    issues = blocking + others

    return {
        "ok": True,
        "checkedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "verdict": verdict,
        "verdictLabel": VERDICTS[verdict],
        "headline": _headline(verdict, blocking, others),
        "blocking": blocking,
        "attention": others,
        # 能一键修的（带 repairKey）与必须人工的，分开列：
        # 老师看前者是"点一下就好"，看后者是"该找谁"
        "fixable": [one for one in issues if one["repairable"]],
        "manual": [one for one in issues if one["manual"] and not one["repairable"]],
        "items": sort_items(items),
        "summary": summarize(items),
        "errors": errors,
    }
