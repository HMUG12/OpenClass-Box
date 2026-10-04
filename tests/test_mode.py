"""使用模式与开机自动化的测试。

重点在两条边界：
1. 模式只影响"显示什么"，不改变检测能力本身；
2. 自动化**只做无损的事** —— 尤其不结束进程。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core import auto_tasks, mode  # noqa: E402


def _cfg(monkeypatch, stored: dict):
    from backend.core import config as config_mod

    fake = dict(stored)
    monkeypatch.setattr(
        config_mod.config, "get", lambda key, default=None: fake.get(key, default),
        raising=False,
    )
    monkeypatch.setattr(
        config_mod.config, "set",
        lambda key, value: fake.__setitem__(key, value) or True,
        raising=False,
    )
    return fake


# ── 模式：可见性 ─────────────────────────────────────────


def test_normal_mode_hides_nothing(monkeypatch):
    _cfg(monkeypatch, {"ui_mode": "normal", "ui_mode_configured": True})
    assert mode.current() == "normal"
    for page in ("hardware", "lan", "maintenance", "tasks"):
        assert mode.page_visible(page) is True


def test_auto_mode_hides_exactly_four_pages(monkeypatch):
    """用户指定的四个：硬件信息 / 机房管理 / 维护 / 定时任务。"""
    _cfg(monkeypatch, {"ui_mode": "auto", "ui_mode_configured": True})
    all_pages = ("dashboard", "hardware", "lan", "maintenance", "chat",
                 "tasks", "tools", "music", "wallpaper", "security",
                 "settings", "about")
    hidden = [page for page in all_pages if not mode.page_visible(page)]
    assert hidden == ["hardware", "lan", "maintenance", "tasks"]


def test_auto_mode_keeps_the_pages_people_actually_use(monkeypatch):
    """自动化模式不能把"能自己修一下"的入口也藏了。"""
    _cfg(monkeypatch, {"ui_mode": "auto", "ui_mode_configured": True})
    for page in ("dashboard", "chat", "tools", "security", "settings", "about"):
        assert mode.page_visible(page) is True, page


def test_pro_mode_hides_nothing_but_adds_detail_blocks(monkeypatch):
    _cfg(monkeypatch, {"ui_mode": "pro", "ui_mode_configured": True})
    assert mode.page_visible("hardware") is True
    assert mode.is_pro() is True
    assert "manualRepair" in mode.describe()["proOnlyBlocks"]


def test_pro_only_blocks_empty_in_other_modes(monkeypatch):
    for value in ("normal", "auto"):
        _cfg(monkeypatch, {"ui_mode": value, "ui_mode_configured": True})
        assert mode.describe()["proOnlyBlocks"] == []

# ── 模式：首次启动与切换 ─────────────────────────────────


def test_first_run_true_when_never_chosen(monkeypatch):
    _cfg(monkeypatch, {})
    assert mode.is_first_run() is True


def test_first_run_false_after_explicitly_choosing_normal(monkeypatch):
    """用户主动选过"正常"之后，不该每次启动都被当成新机器再问一遍。"""
    store = _cfg(monkeypatch, {})
    assert mode.set_mode("normal")["ok"] is True
    assert store["ui_mode"] == "normal"
    assert store["ui_mode_configured"] is True
    assert mode.is_first_run() is False


def test_setting_mode_persists(monkeypatch):
    store = _cfg(monkeypatch, {})
    mode.set_mode("auto")
    assert store["ui_mode"] == "auto"
    assert mode.is_auto() is True
    assert mode.page_visible("maintenance") is False


def test_unknown_mode_is_refused_and_keeps_current(monkeypatch):
    store = _cfg(monkeypatch, {"ui_mode": "normal", "ui_mode_configured": True})
    result = mode.set_mode("无敌模式")
    assert result["ok"] is False
    assert "未知模式" in result["message"]
    assert store.get("ui_mode") == "normal"      # 没被改坏


def test_corrupted_mode_falls_back_to_normal(monkeypatch):
    """配置被写坏时要能起来 —— 宁可多显示几个入口，也不要卡在选模式上。"""
    _cfg(monkeypatch, {"ui_mode": "###", "ui_mode_configured": True})
    assert mode.current() == "normal"


# ── 自动化：安全边界 ─────────────────────────────────────


def test_startup_tasks_never_kill_processes(monkeypatch):
    """**核心安全断言**：开机任务里不许出现任何"结束进程"的调用。

    自动化意味着没人盯着它跑；结束进程会直接关掉老师正在用的软件。
    """
    from backend.core import procs

    def explode(*_a, **_k):  # pragma: no cover —— 被调用就是测试失败
        raise AssertionError("开机任务不许结束任何进程")

    monkeypatch.setattr(procs, "kill_process", explode)
    monkeypatch.setattr(auto_tasks, "run_cleanup", lambda: {"ok": True, "message": "跳过"})
    monkeypatch.setattr(auto_tasks, "_trim_working_sets", lambda limit=0: {"ok": True, "message": "跳过"})

    assert auto_tasks.run_startup_tasks()["ok"] is True


def test_each_step_failure_does_not_block_others(monkeypatch):
    """某一步挂了不能连累后面的 —— 否则"整理内存失败"会连巡检结果都丢。"""

    def boom():
        raise RuntimeError("这一步炸了")

    monkeypatch.setattr(auto_tasks, "run_checkup", boom)
    monkeypatch.setattr(auto_tasks, "run_cleanup", lambda: {"ok": True, "message": "已清理"})
    monkeypatch.setattr(auto_tasks, "_startup_advice", lambda: {"ok": True, "count": 0})
    monkeypatch.setattr(auto_tasks, "_high_usage_advice", lambda: {"ok": True, "count": 0})
    monkeypatch.setattr(auto_tasks, "_trim_working_sets", lambda limit=0: {"ok": True, "message": "x"})

    result = auto_tasks.run_startup_tasks()

    assert result["ok"] is False
    assert "checkup" in result["failedSteps"]
    # 关键：后面的步骤照跑完了
    assert result["steps"]["cleanup"].get("message") == "已清理"
    assert result["steps"]["startup"] is not None
    assert result["steps"]["highUsage"] is not None


def test_startup_task_list_has_no_destructive_entry():
    """把执行计划摊开检查：里面只能有"巡检/清理/整理/建议"四类。"""
    import inspect

    source = inspect.getsource(auto_tasks.run_startup_tasks)
    for word in ("kill", "terminate", "taskkill", "shutdown", "uninstall"):
        assert word not in source.lower(), f"开机任务里不该出现 {word}"


def test_trim_working_sets_reports_what_it_did():
    """整理内存要如实报告处理了几个、释放多少 —— 说不清就等于没做。"""
    if sys.platform != "win32":
        pytest.skip("仅 Windows")
    outcome = auto_tasks._trim_working_sets(limit=2)
    assert outcome["ok"] is True
    assert "trimmed" in outcome and "freedMB" in outcome


# ── 自动化：结果留档 ─────────────────────────────────────


def test_last_reports_not_run_before_any_run(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_tasks, "_result_path", lambda: tmp_path / "auto.json")
    data = auto_tasks.last()
    assert data["hasResult"] is False
    assert "还没有自动执行过" in data["message"]


def test_result_is_saved_and_readable(tmp_path, monkeypatch):
    """首页显示的是上次结果，所以跑完必须留档。"""
    monkeypatch.setattr(auto_tasks, "_result_path", lambda: tmp_path / "auto.json")
    monkeypatch.setattr(auto_tasks, "run_checkup", lambda: {"ok": True, "verdictLabel": "可以上课"})
    monkeypatch.setattr(auto_tasks, "run_cleanup", lambda: {"ok": True, "message": "已清理 12 项"})
    monkeypatch.setattr(auto_tasks, "_startup_advice", lambda: {"ok": True, "count": 3})
    monkeypatch.setattr(auto_tasks, "_high_usage_advice", lambda: {"ok": True, "count": 0})
    monkeypatch.setattr(auto_tasks, "_trim_working_sets", lambda limit=0: {"ok": True, "message": "x"})

    auto_tasks.run_startup_tasks()

    saved = auto_tasks.last()
    assert saved["hasResult"] is True
    assert saved["steps"]["checkup"]["verdictLabel"] == "可以上课"


def test_broken_result_file_reads_as_not_run(tmp_path, monkeypatch):
    """存档被写坏只当"没跑过"，绝不能抛错挡住首页。"""
    path = tmp_path / "auto.json"
    monkeypatch.setattr(auto_tasks, "_result_path", lambda: path)
    path.write_text("{ 这不是合法 JSON", encoding="utf-8")
    assert auto_tasks.last()["hasResult"] is False


def test_should_run_only_in_auto_mode(monkeypatch):
    _cfg(monkeypatch, {"ui_mode": "auto", "ui_mode_configured": True})
    assert auto_tasks.should_run_on_start() is True
    _cfg(monkeypatch, {"ui_mode": "normal", "ui_mode_configured": True})
    assert auto_tasks.should_run_on_start() is False

# ── 模式：设置页板块可见性 ───────────────────────────────


def test_normal_hides_machine_settings(monkeypatch):
    """普通使用者不需要配置机房连通，也不需要知道自己是 A 端还是 B 端。

    放出来只会让人对着"教师机地址 / 代理"不知道怎么填。
    """
    _cfg(monkeypatch, {"ui_mode": "normal", "ui_mode_configured": True})
    assert mode.panel_visible("settings.remote") is False
    assert mode.panel_visible("settings.join") is False
    assert set(mode.describe()["hiddenPanels"]) == {"settings.remote", "settings.join"}


def test_pro_shows_everything(monkeypatch):
    _cfg(monkeypatch, {"ui_mode": "pro", "ui_mode_configured": True})
    assert mode.panel_visible("settings.remote") is True
    assert mode.panel_visible("settings.join") is True
    assert mode.describe()["hiddenPanels"] == []


def test_auto_hides_machine_settings_too(monkeypatch):
    _cfg(monkeypatch, {"ui_mode": "auto", "ui_mode_configured": True})
    assert mode.panel_visible("settings.remote") is False
    # 自动化模式下"维护"整页没了，但它在设置里的这两块仍应按同一规则隐藏
    assert mode.page_visible("maintenance") is False


def test_other_panels_stay_visible(monkeypatch):
    """别把别的设置也一起藏了 —— 只针对明确列出的那两块。"""
    _cfg(monkeypatch, {"ui_mode": "normal", "ui_mode_configured": True})
    for panel in ("settings.appearance", "settings.mobile", "settings.storage"):
        assert mode.panel_visible(panel) is True, panel
