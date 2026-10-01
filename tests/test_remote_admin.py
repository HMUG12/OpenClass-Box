"""远程管理的安全约束 —— 三道闸、来源限制与凭据存储。

⚠️ 红线：本文件不启动 HTTP 服务、不下发任何真实指令；
电源相关用例一律在 OPENCLASS_SAFE_TEST=1 下运行（只记录、不执行）。
"""
from __future__ import annotations

import pytest

from backend.core import remote_admin


@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    """把配置写到临时目录，避免污染开发机的真实设置。"""
    from backend.core import config as config_module

    monkeypatch.setattr(config_module.config, "_path", tmp_path / "app_config.json")
    monkeypatch.setattr(config_module.config, "_data", {})
    return config_module.config


def test_actions_come_from_protocol():
    """动作白名单必须来自协议定义，避免两边各写一份而跑偏。"""
    actions = remote_admin._actions()
    assert "power" in actions
    assert "checkup" in actions


def test_short_access_code_rejected(isolated_config):
    result = remote_admin.set_code("1234")
    assert result["ok"] is False


def test_access_code_is_stored_hashed(isolated_config):
    assert remote_admin.set_code("OpenClass-Test-2026")["ok"] is True
    data = remote_admin._credential()
    assert data.get("hash") and data.get("salt")
    assert "OpenClass-Test-2026" not in str(data)


def test_power_requires_confirmation(monkeypatch):
    """第一道闸：没有确认字段一律拒绝。"""
    monkeypatch.setenv("OPENCLASS_SAFE_TEST", "1")
    result = remote_admin.dispatch("power", ["node-1"], {"mode": "shutdown"}, confirm="")
    assert result["ok"] is False
    assert "确认" in result["message"]


def test_power_rejects_unknown_mode(monkeypatch):
    """第二道闸：模式必须在白名单里。"""
    monkeypatch.setenv("OPENCLASS_SAFE_TEST", "1")
    result = remote_admin.dispatch(
        "power", ["node-1"], {"mode": "explode"}, confirm=remote_admin.CONFIRM_TOKEN
    )
    assert result["ok"] is False
    assert "explode" in result["message"]


def test_power_intercepted_in_safe_test_mode(monkeypatch):
    """第三道闸：安全测试模式下只记录、不下发。"""
    monkeypatch.setenv("OPENCLASS_SAFE_TEST", "1")
    assert remote_admin.safe_test() is True
    result = remote_admin.dispatch(
        "power", ["node-1"], {"mode": "shutdown"}, confirm=remote_admin.CONFIRM_TOKEN
    )
    assert result.get("safeTest") is True
    assert "未真正下发" in result["message"] or "拦截" in result["message"]


def test_unknown_action_and_empty_nodes_rejected():
    assert remote_admin.dispatch("rm-rf", ["n"], {})["ok"] is False
    empty = remote_admin.dispatch("checkup", [], {})
    assert empty["ok"] is False
    assert "设备" in empty["message"]


def test_source_rules_default_private_only():
    """默认只接受私有 / 回环来源；公网来源必须显式开关。"""
    assert remote_admin._source_allowed("127.0.0.1") is True
    assert remote_admin._source_allowed("192.168.1.10") is True
    assert remote_admin._source_allowed("8.8.8.8") is False


def test_audit_records_entries(isolated_config):
    """危险动作必须有审计记录（这里用安全模式下的拦截记录验证）。"""
    remote_admin._note("power（测试）", "只记录不下发", True, "127.0.0.1")
    items = remote_admin.audit(5)
    assert items and items[0]["action"].startswith("power")


def test_start_refuses_without_access_code(isolated_config):
    """没设访问码就不该允许启动服务（杜绝"默认无码"的公开后门）。"""
    result = remote_admin.start(port=38699)
    assert result["ok"] is False
    assert "访问码" in result["message"]
