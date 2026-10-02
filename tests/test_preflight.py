"""课前准备 —— 结论判定与「卡课 / 有影响」的分类。

测试重点是**判定逻辑**（哪些问题真的卡课），不是检测本身：
三类检测的结果用替身喂进去，保证结论可复现、可解释。
"""
from __future__ import annotations

from backend.core import preflight as pf


def _patch(monkeypatch, health=None, classroom=None, restore=None):
    """替换三类检测来源。

    preflight 在函数内 `from .health import run_checks`，也就是每次调用都会从
    模块对象取属性 —— 所以 patch **模块属性**即可。

    这里刻意用对象形式而不是 "backend.core.health.run_checks" 这种字符串路径：
    字符串形式在解析失败时会**静默不生效**（raising=False），测试会假装通过，
    而实际测的还是真实检测结果。
    """
    from backend.core import classroom as classroom_mod
    from backend.core import health as health_mod
    from backend.core import restore_watch as restore_mod

    # 注意包装：传进来的是"条目列表"，而检测函数返回的是 {"items": [...]} ——
    # 早先这里直接把列表返回，run_checks().get("items") 于是对列表调 .get 抛错、
    # 被 preflight 的容错分支吞掉，结果是"patch 好像没生效"
    monkeypatch.setattr(
        health_mod, "run_checks", lambda: {"items": list(health or [])}
    )
    monkeypatch.setattr(
        classroom_mod, "report", lambda: {"items": list(classroom or [])}
    )
    monkeypatch.setattr(
        restore_mod,
        "detect",
        lambda: restore if restore is not None else {"level": "ok", "detail": "保护中"},
    )


def _health_item(key, name, ok, detail="", suggest=""):
    return {"key": key, "name": name, "ok": ok, "detail": detail, "suggest": suggest}


# ── 结论判定 ────────────────────────────────────────────────


def test_network_down_blocks_class(monkeypatch):
    """网络不通是真的上不了课（互动课堂、资源、云课件都靠它）。"""
    _patch(
        monkeypatch,
        health=[_health_item("network", "网络连接", False, "未连接网线", "检查网线")],
    )
    result = pf.run()
    assert result["verdict"] == "blocked"
    assert result["verdictLabel"] == pf.VERDICTS["blocked"]
    assert result["blocking"][0]["id"] == "health.network"
    assert "网络连接" in result["headline"]


def test_camera_projector_missing_is_only_attention(monkeypatch):
    """投影没连不算"上不了课" —— 老师常常课前才接，一律报红只会让人不信这个灯。"""
    _patch(
        monkeypatch,
        classroom=[
            {"key": "screen", "name": "投影 / 显示", "ok": False, "detail": "主屏未接投影", "suggest": "接好投影线"}
        ],
    )
    result = pf.run()
    assert result["verdict"] == "attention"
    assert result["blocking"] == []
    assert result["attention"][0]["id"] == "classroom.screen"


def test_all_good_says_ready(monkeypatch):
    _patch(monkeypatch)
    result = pf.run()
    assert result["verdict"] == "ready"
    assert result["headline"] == "各项检查正常，可以直接开始上课"
    assert result["blocking"] == [] and result["attention"] == []


def test_attention_headline_previews_items(monkeypatch):
    _patch(
        monkeypatch,
        classroom=[
            {"key": "touch", "name": "触摸", "ok": False, "detail": "未检测到触屏", "suggest": "检查 USB 线"},
            {"key": "apps", "name": "教学软件", "ok": False, "detail": "缺少白板", "suggest": "安装白板"},
        ],
    )
    result = pf.run()
    assert result["verdict"] == "attention"
    assert "2 项建议课前看一眼" in result["headline"]


def test_blocking_takes_priority_over_attention(monkeypatch):
    """既有卡课问题又有小问题时，结论必须是"先处理"。"""
    _patch(
        monkeypatch,
        health=[_health_item("sound", "声音设备", False, "未检测到音频输出")],
        classroom=[{"key": "touch", "name": "触摸", "ok": False, "detail": "未检测到触屏"}],
    )
    result = pf.run()
    assert result["verdict"] == "blocked"
    assert result["blocking"][0]["id"] == "health.sound"
    assert len(result["attention"]) == 1


# ── 修复 / 人工 的分工 ──────────────────────────────────────


def test_fixable_and_manual_are_separated(monkeypatch):
    """老师看 fixable 是"点一下就好"，看 manual 是"该找谁"。"""
    _patch(
        monkeypatch,
        health=[_health_item("network", "网络连接", False, "未连接网线", "检查网线")],
        restore={"level": "warn", "reasons": ["保护已被禁用"], "advice": "在还原软件里重新开启"},
    )
    result = pf.run()
    assert [one["repairKey"] for one in result["fixable"]] == ["reset_network"]
    assert result["manual"][0]["id"] == "preflight.restore"
    assert "还原" in result["manual"][0]["title"]


def test_restore_stopped_does_not_block_class(monkeypatch):
    """还原保护被停：能上课，但必须人工处理 —— 属于课后该管的事。"""
    _patch(
        monkeypatch,
        restore={"level": "warn", "reasons": ["保护已被禁用"], "advice": "在还原软件里重新开启"},
    )
    result = pf.run()
    assert result["verdict"] == "attention"
    assert result["blocking"] == []


def test_restore_ok_is_listed_as_normal(monkeypatch):
    """还原保护正常时也要有一条：老师要看到"它是开着的"。"""
    _patch(monkeypatch, restore={"level": "ok", "detail": "保护中（冰点还原）"})
    result = pf.run()
    restore = next(one for one in result["items"] if one["id"] == "preflight.restore")
    assert restore["status"] == "ok"
    assert restore["summary"]


def test_restore_detection_failure_is_not_fatal(monkeypatch):
    """还原检测炸了：整份结论仍然要给出来，不能因此说"机器没问题"。"""
    from backend.core import restore_watch as restore_mod

    _patch(monkeypatch)

    def _boom():
        raise RuntimeError("服务读取被拒")

    monkeypatch.setattr(restore_mod, "detect", _boom)
    result = pf.run()
    assert result["ok"] is True
    restore = [one for one in result["items"] if one["id"] == "preflight.restore"]
    assert restore == []          # 测不出来就不列，不编造"正常"


# ── 来源失败的处理 ──────────────────────────────────────────


def test_source_failure_is_reported_but_not_fatal(monkeypatch):
    """一部分没测出来 ≠ 整台机器不能用：如实说明，其余照给。"""
    from backend.core import health as health_mod

    _patch(monkeypatch)

    def _boom():
        raise RuntimeError("WMI 挂了")

    monkeypatch.setattr(health_mod, "run_checks", _boom)
    result = pf.run()
    assert result["ok"] is True
    assert result["errors"] and "体检未完成" in result["errors"][0]
    assert result["verdict"] in pf.VERDICTS


def test_result_carries_summary_for_remote_and_mobile(monkeypatch):
    """手机端 / 远程汇总直接用这份结果：结论短、理由在、条目结构统一。"""
    _patch(
        monkeypatch,
        health=[_health_item("network", "网络连接", False, "未连接网线", "检查网线")],
    )
    result = pf.run()
    assert result["summary"]["level"] == "warn"
    assert result["summary"]["issueCount"] >= 1
    for one in result["items"]:
        assert {"id", "title", "status", "summary", "statusLabel"} <= set(one)
    assert result["checkedAt"]
