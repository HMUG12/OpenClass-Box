"""统一诊断结果模型 —— 转换、排序、汇总、报告文本。

这一层的价值全在"稳定"上：一旦形状漂了，前端四份渲染与报告导出会一起坏，
所以用测试把形状与语义钉住。
"""
from __future__ import annotations

from backend.core import diag_result as dr


# ── 状态语义 ────────────────────────────────────────────────


def test_levels_align_with_health_watch():
    """两处各有一份 LEVELS，文案必须一致（防漂移）。"""
    from backend.core import health_watch

    for level in ("replace", "warn", "watch", "ok"):
        assert dr.LEVELS[level] == health_watch.LEVELS[level]


def test_rank_orders_by_severity():
    assert dr.rank("replace") < dr.rank("warn") < dr.rank("watch") < dr.rank("ok")
    assert dr.worse("warn", "ok") == "warn"
    assert dr.worse("ok", "watch") == "watch"


def test_unknown_status_falls_back_to_info():
    """未知状态不能抛错，也不能被当成"正常"藏起来。"""
    assert dr.rank("没见过的状态") == dr.rank("info")
    one = dr.item("x", "标题", "没见过的状态")
    assert one["status"] == "info"


def test_item_shape_is_stable():
    one = dr.item(
        "health.network",
        "网络连接",
        "warn",
        "未连接",
        advice="插网线",
        repairable=True,
        repair_key="reset_network",
        source="health",
    )
    for field in (
        "id",
        "title",
        "status",
        "statusLabel",
        "summary",
        "advice",
        "details",
        "repairable",
        "repairKey",
        "source",
        "sourceLabel",
        "manual",
    ):
        assert field in one, f"统一项缺少字段 {field}"
    assert one["statusLabel"] == "预警"
    assert one["sourceLabel"] == "一键体检"


# ── 各来源转换 ──────────────────────────────────────────────


def test_from_health_maps_ok_and_offers_repair():
    raw = [
        {"key": "network", "name": "网络连接", "ok": False, "detail": "未连接", "suggest": "插网线"},
        {"key": "memory", "name": "内存", "ok": True, "detail": "占用 30%", "suggest": ""},
    ]
    items = dr.from_health(raw)
    assert len(items) == 2

    net = next(one for one in items if one["id"] == "health.network")
    assert net["status"] == "warn"
    assert net["repairable"] is True and net["repairKey"] == "reset_network"
    assert net["advice"] == "插网线"

    mem = next(one for one in items if one["id"] == "health.memory")
    assert mem["status"] == "ok"
    # 正常的项不给"修复"按钮 —— 没有可修的东西
    assert mem["repairable"] is False and mem["repairKey"] == ""


def test_from_health_skips_broken_entries():
    """来源数据脏了也不能让整个汇总崩掉：非字典条目直接跳过。"""
    items = dr.from_health([None, "坏数据", {"key": "x"}])
    assert len(items) == 1
    assert items[0]["status"] == "warn"    # 缺 ok 视为不通过，宁可多说一句


def test_from_classroom_never_offers_repair():
    """课堂检测的问题（线没插 / 投屏没开 / 还原被禁）都不是软件能一键修的。"""
    raw = [
        {"key": "touch", "name": "触摸", "ok": False, "detail": "未检测到触屏", "suggest": "检查 USB 线"},
    ]
    items = dr.from_classroom(raw)
    assert items[0]["repairable"] is False
    assert items[0]["manual"] is True
    assert items[0]["sourceLabel"] == "课堂检测"


def test_from_watch_marks_replace_as_manual():
    """replace 级别（例如硬盘衰退）是报修级别的事，软件不该假装能修。"""
    raw = [
        {"kind": "disk", "level": "replace", "levelLabel": "需要更换", "title": "硬盘",
         "detail": "重映射扇区增多", "advice": "尽快备份并更换"},
        {"kind": "space", "level": "watch", "levelLabel": "留意", "title": "D 盘空间偏低",
         "detail": "已用 91%", "advice": "清理临时文件"},
    ]
    items = dr.from_watch(raw)
    disk = next(one for one in items if one["id"] == "watch.disk")
    space = next(one for one in items if one["id"] == "watch.space")
    assert disk["status"] == "replace" and disk["manual"] is True
    assert space["status"] == "watch" and space["manual"] is False


def test_from_watch_normalizes_unknown_level():
    items = dr.from_watch([{"kind": "x", "level": "爆炸", "title": "x"}])
    assert items[0]["status"] == "watch"    # 未知级别按"留意"，不静默丢项


# ── 排序与汇总 ──────────────────────────────────────────────


def test_sort_puts_worst_first():
    items = [
        dr.item("a", "正常项", "ok"),
        dr.item("b", "报修项", "replace"),
        dr.item("c", "留意项", "watch"),
    ]
    ordered = dr.sort_items(items)
    assert [one["id"] for one in ordered] == ["b", "c", "a"]


def test_only_issues_filters_out_ok_and_info():
    items = [
        dr.item("a", "正常", "ok"),
        dr.item("b", "信息", "info"),
        dr.item("c", "留意", "watch"),
    ]
    assert [one["id"] for one in dr.only_issues(items)] == ["c"]


def test_summarize_counts_and_headline():
    items = [
        dr.item("a", "正常", "ok"),
        dr.item("b", "可修", "warn", repairable=True),
        dr.item("c", "报修", "replace", manual=True),
    ]
    info = dr.summarize(items)
    assert info["total"] == 3
    assert info["counts"]["warn"] == 1 and info["counts"]["ok"] == 1
    assert info["level"] == "replace"
    assert info["issueCount"] == 2
    assert "2 项需要关注" in info["headline"]
    assert "1 项可一键修复" in info["headline"]
    assert "1 项需要人工处理" in info["headline"]


def test_summarize_all_ok_headline():
    info = dr.summarize([dr.item("a", "正常", "ok")])
    assert info["level"] == "ok"
    assert info["headline"] == "全部正常，没有需要处理的项目"


def test_summarize_empty_is_safe():
    info = dr.summarize([])
    assert info["total"] == 0
    assert info["level"] == "ok"


# ── 报告文本 ────────────────────────────────────────────────


def test_report_is_pasteable_text():
    sections = {
        "一键体检": [
            dr.item("health.network", "网络连接", "warn", "未连接网线", advice="检查网线"),
            dr.item("health.memory", "内存", "ok", "占用 32%"),
        ],
        "维护清单": [
            dr.item("watch.disk", "硬盘", "replace", "重映射扇区增多", advice="尽快备份并更换", manual=True),
        ],
    }
    text = dr.to_report(sections, machine="TEACHER-PC（Windows 10）")

    assert "【OpenClass-Box 诊断报告】" in text
    assert "TEACHER-PC" in text
    assert "需要处理" in text
    assert "网络连接" in text and "检查网线" in text
    assert "硬盘" in text
    assert "正常（1 项）" in text
    # 报告要能直接粘进聊天窗口：不能出现 JSON 括号之类的机器格式
    assert "{" not in text and "}" not in text


def test_report_without_issues():
    text = dr.to_report({"一键体检": [dr.item("a", "内存", "ok", "占用 30%")]})
    assert "全部正常" in text
    # 没有问题时不该出现"需要处理"或空的"不需要处理"标题
    assert "── 需要处理 ──" not in text
    assert "不需要处理" not in text
    assert "── 正常（1 项）──" in text


# ── 与真实检测对接（冒烟）──────────────────────────────────


def test_health_run_checks_exposes_unified():
    """一键体检必须带上统一结果，字段齐全。"""
    from backend.core.health import run_checks

    result = run_checks()
    assert "unified" in result and isinstance(result["unified"], list)
    assert result["unified"], "体检至少要产出若干统一项"
    first = result["unified"][0]
    assert {"id", "title", "status", "summary", "statusLabel"} <= set(first)
    summary = result["unifiedSummary"]
    assert summary["total"] == len(result["unified"])
    assert summary["level"] in dr.LEVELS


def test_watch_from_watch_conversion_is_pure():
    """转换是纯函数：同样的输入必须给同样的输出（可重放、可测试）。"""
    raw = [{"kind": "space", "level": "watch", "title": "D 盘", "detail": "已用 91%"}]
    assert dr.from_watch(raw) == dr.from_watch(raw)
