"""局域网放行 —— 状态判定与"是否真的加上"的确认逻辑。

这一块的坑是**假成功**：旧版只要提权进程起得来就报"已请求放行"，
用户在 UAC 上点"否"也显示成功 —— 于是"点了放行，手机还是连不上"。
所以测试重点：状态判定要准、失败路径要如实说。
"""
from __future__ import annotations

import json
import types

import backend.core.firewall as fw


def _snapshot(rules, categories):
    return {"rules": rules, "categories": categories}


def _rule(enabled: bool = True, inbound: bool = True):
    """一条"已启用 + 入站"的规则（netsh 查询后的形状）。"""
    return {"exists": True, "enabled": enabled, "inbound": inbound, "profiles": "专用, 域"}


# ── 状态判定 ────────────────────────────────────────────────


def test_status_unsupported_on_non_windows(monkeypatch):
    monkeypatch.setattr(fw, "os", types.SimpleNamespace(name="posix"))
    result = fw.status()
    assert result["supported"] is False
    assert result["allowed"] is True


def test_not_allowed_without_rules(monkeypatch):
    monkeypatch.setattr(fw, "_snapshot", lambda: _snapshot([], ["Private"]))
    result = fw.status()
    assert result["allowed"] is False
    assert "未放行" in result["message"]


def test_allowed_with_two_enabled_inbound_rules(monkeypatch):
    monkeypatch.setattr(fw, "_snapshot", lambda: _snapshot([_rule(), _rule()], ["Private"]))
    result = fw.status()
    assert result["allowed"] is True
    assert result["enabledCount"] == 2
    assert result["onlyPublic"] is False


def test_disabled_rules_do_not_count(monkeypatch):
    """规则在但被禁用 = 没放行，不能报成功。"""
    monkeypatch.setattr(
        fw, "_snapshot", lambda: _snapshot([_rule(enabled=False), _rule()], ["Private"])
    )
    assert fw.status()["allowed"] is False


def test_outbound_rules_do_not_count(monkeypatch):
    """要的是**入站**放行：出站规则再多也不解决"手机连不上"。"""
    monkeypatch.setattr(
        fw,
        "_snapshot",
        lambda: _snapshot([_rule(inbound=False), _rule(inbound=False)], ["Private"]),
    )
    assert fw.status()["allowed"] is False


def test_public_only_is_reported_explicitly(monkeypatch):
    """规则在、但网络是"公用" —— 这是"放行了却连不上"的常见原因，必须说出来。"""
    monkeypatch.setattr(fw, "_snapshot", lambda: _snapshot([_rule(), _rule()], ["Public"]))
    result = fw.status()
    assert result["allowed"] is True
    assert result["onlyPublic"] is True
    assert "公用" in result["message"]
    assert "改为专用" in result["message"]


def test_mixed_categories_not_flagged_public(monkeypatch):
    monkeypatch.setattr(
        fw, "_snapshot", lambda: _snapshot([_rule(), _rule()], ["Public", "Private"])
    )
    assert fw.status()["onlyPublic"] is False


def test_categories_are_localized(monkeypatch):
    monkeypatch.setattr(fw, "_snapshot", lambda: _snapshot([], ["Private", "Public"]))
    assert fw.status()["categories"] == ["专用", "公用"]


# ── netsh 输出解析（不用管理员权限的那条路）────────────────


def test_query_rule_parses_netsh_output(monkeypatch):
    payload = (
        "Rule Name:                            OpenClass-Box 局域网服务\n"
        "----------------------------------------------------------------------\n"
        "Enabled:                              Yes\n"
        "Direction:                            In\n"
        "Profiles:                             Domain,Private\n"
        "Protocol:                             TCP\n"
        "LocalPort:                            38610,38620,38900\n"
        "Action:                               Allow\n"
        "Ok.\n"
    )
    monkeypatch.setattr(fw, "_run", lambda *a, **k: payload)
    rule = fw._query_rule("OpenClass-Box 局域网服务")
    assert rule["exists"] is True
    assert rule["enabled"] is True
    assert rule["inbound"] is True
    assert rule["ports"] == "38610,38620,38900"


def test_query_rule_parses_chinese_output(monkeypatch):
    """netsh 的措辞跟随系统语言，中文输出也要认。"""
    payload = (
        "规则名称:                            OpenClass-Box 局域网服务\n"
        "已启用:                              是\n"
        "方向:                                入\n"
        "操作:                                允许\n"
    )
    monkeypatch.setattr(fw, "_run", lambda *a, **k: payload)
    rule = fw._query_rule("OpenClass-Box 局域网服务")
    assert rule["enabled"] is True
    assert rule["inbound"] is True


def test_query_rule_reports_missing_rule(monkeypatch):
    """没有规则时 netsh 会给一句提示（中英文两种），不能当成存在。"""
    monkeypatch.setattr(fw, "_run", lambda *a, **k: "没有与指定条件相匹配的规则。\n")
    assert fw._query_rule("x")["exists"] is False

    monkeypatch.setattr(fw, "_run", lambda *a, **k: "No rules match the specified criteria.\n")
    assert fw._query_rule("x")["exists"] is False


def test_query_rule_handles_disabled(monkeypatch):
    """规则存在但被禁用 ≠ 放行（status 依赖这个区分）。"""
    payload = "Enabled:                              No\nDirection:                            In\n"
    monkeypatch.setattr(fw, "_run", lambda *a, **k: payload)
    rule = fw._query_rule("x")
    assert rule["exists"] is True
    assert rule["enabled"] is False


def test_snapshot_survives_garbage(monkeypatch):
    """PowerShell 被策略挡住、输出乱码时，只能当"查不到"，不能崩。"""
    monkeypatch.setattr(fw, "_run_ps", lambda *a, **k: "")
    monkeypatch.setattr(fw, "_query_rule", lambda name: {"exists": False})
    snap = fw._snapshot()
    assert snap["rules"] == []
    assert snap["categories"] == []


# ── 放行流程（关键：不能假成功）────────────────────────────


def _paths(monkeypatch, tmp_path):
    monkeypatch.setattr(fw, "_script_path", lambda: tmp_path / "a.bat")
    monkeypatch.setattr(fw, "_result_path", lambda: tmp_path / "a.result")
    monkeypatch.setattr(fw.time, "sleep", lambda _seconds: None)   # 别真等 30 秒


def test_allow_reports_cancelled_uac(monkeypatch, tmp_path):
    """UAC 被取消必须如实报失败 —— 旧版这里会显示成功。"""
    _paths(monkeypatch, tmp_path)
    monkeypatch.setattr(fw, "_runas", lambda *a: 5)       # <=32 = 取消 / 失败
    result = fw.allow()
    assert result["ok"] is False
    assert "取消" in result["message"]


def test_allow_does_not_trust_exit_code(monkeypatch, tmp_path):
    """提权成功但规则没真正加上时，仍须报失败（复核 status）。"""
    _paths(monkeypatch, tmp_path)
    monkeypatch.setattr(fw, "_runas", lambda *a: 42)
    monkeypatch.setattr(fw, "status", lambda: {"allowed": False, "message": "未放行"})
    result = fw.allow()
    assert result["ok"] is False
    assert "未生效" in result["message"]


def test_allow_succeeds_only_after_recheck(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    monkeypatch.setattr(fw, "_runas", lambda *a: 42)
    monkeypatch.setattr(fw, "status", lambda: {"allowed": True, "onlyPublic": False})
    result = fw.allow()
    assert result["ok"] is True
    assert "已确认存在" in result["message"]


def test_allow_warns_about_public_network(monkeypatch, tmp_path):
    """加上了但网络是公用：算成功，但必须把"规则不会生效"说出来。"""
    _paths(monkeypatch, tmp_path)
    monkeypatch.setattr(fw, "_runas", lambda *a: 42)
    monkeypatch.setattr(fw, "status", lambda: {"allowed": True, "onlyPublic": True})
    result = fw.allow()
    assert result["ok"] is True
    assert "公用" in result["message"]


def test_set_private_reports_cancelled(monkeypatch, tmp_path):
    monkeypatch.setattr(fw, "_runas", lambda *a: 5)
    result = fw.set_private()
    assert result["ok"] is False
    assert "取消" in result["message"]


def test_script_is_written_in_gbk(monkeypatch, tmp_path):
    """cmd 按本地代码页读脚本：中文规则名用 UTF-8 写会变乱码。"""
    _paths(monkeypatch, tmp_path)
    monkeypatch.setattr(fw, "_runas", lambda *a: 5)       # 直接取消，只看脚本内容
    fw.allow()
    text = (tmp_path / "a.bat").read_text(encoding="gbk")
    assert fw.RULE_TCP in text
    for port in fw.TCP_PORTS:
        assert str(port) in text
    assert "profile=private,domain" in text
