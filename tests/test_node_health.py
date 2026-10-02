"""B 端上报的轻量健康摘要 + 远程体检的统一结论。

这两处都是"远程侧"能看到的东西，共同要求是：
  · 结论与桌面端同源（不能各算一套）；
  · 拿不到的数据如实说明，不编造"正常"。
"""
from __future__ import annotations

import backend.net.client as client
import backend.net.commands as commands


# ── 轻量健康摘要（B 端上报）────────────────────────────────


def test_quick_health_shape(monkeypatch):
    client._health_cache["at"] = 0.0
    client._health_cache["data"] = None

    data = client._quick_health()
    assert data["level"] in ("ok", "watch", "warn", "replace", "info")
    assert data["label"]
    assert isinstance(data["issues"], list)
    # 必须说明只是摘要，别让老师以为整机都查过了
    assert "轻量摘要" in data["scope"]


def test_quick_health_is_cached(monkeypatch):
    """上报是每几秒一次的后台动作：摘要必须缓存，不能每次都重算。"""
    client._health_cache["at"] = 0.0
    client._health_cache["data"] = None

    calls = {"n": 0}

    import backend.core.restore_watch as restore_mod

    real = restore_mod.detect

    def counted():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(restore_mod, "detect", counted)

    client._quick_health()
    first = calls["n"]
    client._quick_health()
    assert calls["n"] == first, "第二次应命中缓存"


def test_quick_health_survives_probe_failure(monkeypatch):
    """探测失败不能让上报整体挂掉，也不能谎报正常。"""
    client._health_cache["at"] = 0.0
    client._health_cache["data"] = None

    import backend.core.restore_watch as restore_mod

    def boom():
        raise RuntimeError("服务读取被拒")

    monkeypatch.setattr(restore_mod, "detect", boom)
    data = client._quick_health()
    # 还原项测不出来就不列，其余照给
    assert "还原保护" not in [item["title"] for item in data["issues"]]


def test_status_payload_includes_health(monkeypatch):
    monkeypatch.setattr(client, "_quick_health", lambda: {"level": "ok", "label": "正常"})
    payload = client._status_payload()
    assert payload["health"]["label"] == "正常"


def test_status_payload_health_failure_is_not_fatal(monkeypatch):
    """健康摘要算不出来也不能影响上报（上报一断，设备就从列表里掉了）。"""

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(client, "_quick_health", boom)
    payload = client._status_payload()
    assert "health" not in payload
    assert payload["id"], "基本字段仍要上报"


# ── 远程体检结论 ────────────────────────────────────────────


def test_run_checkup_carries_verdict():
    """A 端下发体检后拿到的结果要能直接按结论分组（哪些机器上不了课）。"""
    result = commands.run_checkup()
    assert result["ok"] is True
    data = result["data"]
    assert "verdict" in data and data["verdict"] in ("ready", "attention", "blocked")
    assert data["verdictLabel"]
    assert data["unified"], "应带上统一项"
    assert data["summary"]["total"] == len(data["unified"])
    # 旧字段保留：老版本 A 端仍在读 items / healthy
    assert "items" in data and "healthy" in data


def test_run_checkup_message_uses_verdict_label():
    result = commands.run_checkup()
    assert result["data"]["verdictLabel"] in result["message"]
