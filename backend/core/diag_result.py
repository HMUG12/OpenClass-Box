"""
统一诊断结果模型 —— 一份结果，界面卡片 / 报修报告 / 远程汇总共用。

为什么要有这一层：

  在此之前，每个检测各返回一套形状：

    · 一键体检   {key, name, ok, detail, suggest}
    · 课堂检测   {key, name, ok, detail, suggest}（同形，但与体检分开维护）
    · 维护清单   {kind, level, levelLabel, title, detail, advice}（四级 + 另一套字段名）

  代价是四处重复：前端要为每种结果各写一遍卡片、报告导出要各写一遍文本拼装、
  手机端与远程汇总要再做一层映射。想加一条"按严重度排序"或"只显示要处理的项"，
  得在四个地方分别实现，然后慢慢漂移。

  统一成一种形状后，这些事情只发生一次。

状态五档（前四档的文案与 core/health_watch.py 的 LEVELS **逐字一致** ——
那是界面已经在用的说法，tests/test_diag_result.py 里有一致性断言防止漂移）：

  · replace  建议更换 / 报修 —— 软件解决不了，走人工
  · warn     预警 —— 影响使用，或很快会
  · watch    关注 —— 能用，但有苗头
  · info     信息 —— 中性结论（例如"未检测到触屏，本机是普通电脑"）
  · ok       正常

设计边界：
  · 这里**只做形状统一与汇总**，不执行任何检测、不调用修复；
  · 转换是**纯函数**（输入是各检测的原始结果），因此可以测试、可以重放；
  · 旧字段一律保留（各 API 的原始返回不变），统一结果作为**并列产物**
    返回（`unified` 字段），前端可以逐页切换，不需要一次性大改。
"""
from __future__ import annotations

import time
from typing import Any, Iterable

LEVELS = {
    "replace": "建议更换",
    "warn": "预警",
    "watch": "关注",
    "info": "信息",
    "ok": "正常",
}

# 越靠前越严重：排序、取"最严重项"、报告分组都用它
ORDER = ["replace", "warn", "watch", "info", "ok"]

SOURCE_LABELS = {
    "health": "一键体检",
    "classroom": "课堂检测",
    "watch": "维护清单",
    "restore": "还原保护",
    "remote": "远程检查",
    "compat": "兼容性知识库",
}

# 体检项 → 已有的一键修复动作（没有对应动作的就不给"修复"按钮，
# 免得点了没反应 —— 那正是用户最反感的一类交互）
HEALTH_REPAIR = {
    "network": "reset_network",
    "sound": "restart_audio",
    "disk": "clean_temp",
}


def rank(status: str) -> int:
    """严重度序号（越小越严重）；未知状态按 info 处理，绝不抛错。"""
    try:
        return ORDER.index(status)
    except ValueError:
        return ORDER.index("info")


def worse(a: str, b: str) -> str:
    """取两者中更严重的那个。"""
    return a if rank(a) <= rank(b) else b


def item(
    item_id: str,
    title: str,
    status: str,
    summary: str = "",
    *,
    advice: str = "",
    details: dict[str, Any] | None = None,
    repairable: bool = False,
    repair_key: str = "",
    source: str = "",
    manual: bool = False,
) -> dict[str, Any]:
    """构造一个统一诊断项。

    manual=True 表示"这件事软件做不了，需要人工/报修"。界面上会归到
    「请报修」区，而不是给出一个点了没反应的按钮 —— 说清楚"这得找人"
    比假装能修更有用。
    """
    status = status if status in LEVELS else "info"
    return {
        "id": str(item_id),
        "title": str(title),
        "status": status,
        "statusLabel": LEVELS[status],
        "summary": str(summary or ""),
        "advice": str(advice or ""),
        "details": dict(details or {}),
        "repairable": bool(repairable),
        "repairKey": str(repair_key or ""),
        "source": str(source or ""),
        "sourceLabel": SOURCE_LABELS.get(str(source or ""), str(source or "")),
        "manual": bool(manual),
    }


# ══════════════════════════════════════════════════════════════
# 各来源 → 统一项（纯函数，便于测试与重放）
# ══════════════════════════════════════════════════════════════


def from_health(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """一键体检结果 → 统一项。

    体检项的 ok=False 一律记为 warn（体检项都是"现在能不能上课"级别的：
    网络、声音、显示、磁盘、内存）。能一键修的带上修复动作。
    """
    result: list[dict[str, Any]] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "item")
        ok = bool(raw.get("ok"))
        repair = HEALTH_REPAIR.get(key, "")
        result.append(
            item(
                f"health.{key}",
                str(raw.get("name") or key),
                "ok" if ok else "warn",
                str(raw.get("detail") or ""),
                advice="" if ok else str(raw.get("suggest") or ""),
                details={"key": key},
                repairable=bool(repair) and not ok,
                repair_key=repair,
                source="health",
            )
        )
    return result


def from_classroom(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """课堂检测结果 → 统一项。

    课堂项（投影拓扑 / 触摸 / 无线投屏 / 教学软件 / 还原环境）都**没有**
    可自动修复的动作：线没插、驱动没装、还原被禁用，都得人工判断或装东西。
    所以这里只给建议，不给"修复"按钮 —— 点不动比没有更让人恼火。
    """
    result: list[dict[str, Any]] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "item")
        ok = bool(raw.get("ok"))
        result.append(
            item(
                f"classroom.{key}",
                str(raw.get("name") or key),
                "ok" if ok else "warn",
                str(raw.get("detail") or ""),
                advice="" if ok else str(raw.get("suggest") or ""),
                details={"key": key},
                source="classroom",
                manual=not ok,
            )
        )
    return result


def from_watch(actions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """维护清单的 actions[] → 统一项。

    清单本身就是四级制（replace/warn/watch），直接映射；
    kind=replace 级别的项（例如硬盘衰退）标为 manual —— 那是报修级别的事，
    软件不该假装能修。
    """
    result: list[dict[str, Any]] = []
    for raw in actions or []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "item")
        status = str(raw.get("level") or "watch")
        if status not in LEVELS:
            status = "watch"
        result.append(
            item(
                f"watch.{kind}",
                str(raw.get("title") or kind),
                status,
                str(raw.get("detail") or ""),
                advice=str(raw.get("advice") or ""),
                details={"kind": kind, "levelLabel": raw.get("levelLabel", "")},
                source="watch",
                manual=status == "replace",
            )
        )
    return result


# ══════════════════════════════════════════════════════════════
# 汇总 / 排序 / 报告
# ══════════════════════════════════════════════════════════════


def sort_items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """按严重度排序（最严重在前），同档保持原顺序。"""
    return sorted(items or [], key=lambda one: rank(str(one.get("status") or "info")))


def only_issues(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """只留下需要关注的项目（replace / warn / watch）。"""
    return [one for one in sort_items(items) if one.get("status") in ("replace", "warn", "watch")]


def summarize(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """汇总：各档计数 + 最严重档 + 一句话结论。"""
    materialized = list(items or [])
    counts = {level: 0 for level in LEVELS}
    for one in materialized:
        status = str(one.get("status") or "info")
        counts[status] = counts.get(status, 0) + 1

    worst = "ok"
    for one in materialized:
        worst = worse(worst, str(one.get("status") or "info"))

    issues = only_issues(materialized)
    return {
        "total": len(materialized),
        "counts": counts,
        "level": worst,
        "levelLabel": LEVELS.get(worst, worst),
        "issueCount": len(issues),
        "headline": headline(worst, issues),
    }


def headline(worst: str, issues: list[dict[str, Any]]) -> str:
    """一句话结论（给报告抬头、远程汇总、手机端用）。"""
    if not issues:
        return "全部正常，没有需要处理的项目"
    repairable = sum(1 for one in issues if one.get("repairable"))
    manual = sum(1 for one in issues if one.get("manual"))
    text = f"{len(issues)} 项需要关注"
    extra: list[str] = []
    if repairable:
        extra.append(f"{repairable} 项可一键修复")
    if manual:
        extra.append(f"{manual} 项需要人工处理")
    if extra:
        text += "（" + "，".join(extra) + "）"
    return text


def to_report(
    sections: dict[str, list[dict[str, Any]]],
    *,
    machine: str = "",
    extra_lines: Iterable[str] = (),
) -> str:
    """把诊断结果拼成**可以直接发给维修人员**的文本。

    为什么是文本而不是 JSON：老师报修用的是微信、钉钉、短信 ——
    他们要的是"能直接粘过去的一段话"，不是一个需要对方装工具才能看的文件。
    所以这里生成的是人读的文本；结构化数据由调用方另行保存（诊断包里）。
    """
    all_items: list[dict[str, Any]] = []
    for group in sections.values():
        all_items.extend(group or [])

    info = summarize(all_items)
    lines: list[str] = ["【OpenClass-Box 诊断报告】"]
    lines.append(f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    if machine:
        lines.append(f"机器：{machine}")
    lines.append(f"结论：{info['headline']}（共检查 {info['total']} 项）")
    for line in extra_lines or ():
        lines.append(str(line))

    issues = only_issues(all_items)
    if issues:
        lines.append("")
        lines.append("── 需要处理 ──")
        for one in issues:
            lines.append(f"[{one.get('statusLabel', '')}] {one.get('title', '')}：{one.get('summary', '')}")
            if one.get("advice"):
                lines.append(f"    怎么办：{one['advice']}")
    # 全正常时不单独打印"不需要处理"这种空标题 —— 开头的结论已经说清楚了

    healthy = [one for one in all_items if one.get("status") == "ok"]
    if healthy:
        lines.append("")
        lines.append(f"── 正常（{len(healthy)} 项）──")
        lines.append("　" + "；".join(f"{one.get('title', '')}：{one.get('summary', '')}" for one in healthy))

    curious = [one for one in all_items if one.get("status") == "info"]
    if curious:
        lines.append("")
        lines.append(f"── 其他信息（{len(curious)} 项）──")
        for one in curious:
            lines.append(f"　{one.get('title', '')}：{one.get('summary', '')}")

    return "\n".join(lines)
