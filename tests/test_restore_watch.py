"""还原保护状态判定 —— 只验证规则，不碰真实服务（用替身注入）。

契约：`restore_watch._services()` 返回的字典以**小写服务名**为键
（真实实现里就是 `name.lower()`），替身数据必须遵循同一契约。
"""
from __future__ import annotations

import pytest

from backend.core import restore_watch as rw


@pytest.fixture()
def fake(monkeypatch):
    """用替身替换服务 / 进程快照。"""

    def _install(services: dict, processes: set[str]):
        monkeypatch.setattr(rw, "_services", lambda: services)
        monkeypatch.setattr(rw, "_processes", lambda: set(processes))

    return _install


def test_no_guard_is_not_a_problem(fake):
    """没装软件类保护不算问题（硬件还原卡 / 云桌面本来就不在软件层）。"""
    fake({}, set())
    result = rw.detect()
    assert result["installed"] is False
    assert result["level"] == "ok"
    assert "硬件还原卡" in result["detail"]


def test_healthy_guard_is_ok(fake):
    fake(
        {"dfserv": {"name": "DFServ", "status": "running", "startType": "automatic"}},
        {"dfserv.exe"},
    )
    result = rw.detect()
    assert result["installed"] is True
    assert result["level"] == "ok"
    assert result["products"][0]["verdict"] == "ok"


def test_disabled_service_is_warning(fake):
    """服务被禁用 = 保护已关闭，这是最需要提醒的情况。"""
    fake(
        {"dfserv": {"name": "DFServ", "status": "stopped", "startType": "disabled"}},
        set(),
    )
    result = rw.detect()
    assert result["level"] == "warn"
    assert result["products"][0]["verdict"] == "disabled"
    assert any("已禁用" in reason for reason in result["reasons"])
    assert result["advice"]


def test_stopped_service_is_warning(fake):
    fake(
        {
            "shadowdefender": {
                "name": "ShadowDefender",
                "status": "stopped",
                "startType": "manual",
            }
        },
        set(),
    )
    result = rw.detect()
    assert result["level"] == "warn"
    assert result["products"][0]["verdict"] == "stopped"


def test_service_without_process_is_watch_only(fake):
    """服务在跑但进程没起来：先列为"关注"，别直接判死刑（可能只是改了进程名）。"""
    fake(
        {"hdguard": {"name": "HDGuard", "status": "running", "startType": "automatic"}},
        set(),
    )
    result = rw.detect()
    assert result["level"] == "watch"
    assert result["products"][0]["verdict"] == "no-process"


def test_service_key_case_is_normalized(fake):
    """服务名大小写不该影响识别（真实系统返回的大小写并不统一）。"""
    fake(
        {"DFSERV": {"name": "DFSERV", "status": "running", "startType": "automatic"}},
        {"DFServ.exe"},
    )
    result = rw.detect()
    assert result["installed"] is True
    assert result["level"] in ("ok", "watch")


def test_detect_never_toggles_protection():
    """边界检查：模块里不应出现开关保护的调用（只读）。"""
    source = rw.__file__ or ""
    assert source
    text = open(source, encoding="utf-8").read()
    for forbidden in ("win_service_start", "win_service_stop", "start_service", "stop_service"):
        assert forbidden not in text
