"""配置备份与历史 —— 备份/保留策略/恢复的边界与安全。

测试全部在临时目录里做，不碰开发机的真实配置。
"""
from __future__ import annotations

import json

import pytest

from backend.core import config_backup as cb


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """把备份目录与配置来源都指向临时目录。"""
    folder = tmp_path / "config_backup"
    monkeypatch.setattr(cb, "backup_dir", lambda: folder)
    monkeypatch.setattr(
        cb, "current_snapshot", lambda: {"theme": "dark", "startup_mode": "window"}
    )
    return folder


def test_create_and_list(sandbox):
    result = cb.create_backup("manual")
    assert result["ok"] is True
    assert result["keys"] == 2

    items = cb.list_backups()
    assert len(items) == 1
    assert items[0]["valid"] is True
    assert items[0]["keys"] == 2
    assert items[0]["reason"] == "手动备份"


def test_auto_backup_once_per_day(sandbox):
    """同一天重复启动不该刷出一堆无意义快照。"""
    first = cb.create_backup("auto")
    assert first["ok"] is True
    second = cb.create_backup("auto")
    assert second.get("skipped") is True
    assert len(cb.list_backups()) == 1


def test_manual_backup_always_writes(sandbox):
    cb.create_backup("manual")
    cb.create_backup("manual")
    assert len(cb.list_backups()) == 2


def test_prune_keeps_recent(sandbox, monkeypatch):
    monkeypatch.setattr(cb, "KEEP", 3)
    for index in range(5):
        folder = cb.backup_dir()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"app_config_2026100{index}_120000_manual.json").write_text(
            json.dumps({"i": index}), encoding="utf-8"
        )
    removed = cb.prune(keep=3)
    assert removed == 2
    assert len(cb.list_backups()) == 3


def test_restore_rejects_missing_file(sandbox):
    result = cb.restore("app_config_20260101_000000_manual.json")
    assert result["ok"] is False
    assert "不存在" in result["message"]


def test_restore_rejects_broken_content(sandbox):
    folder = cb.backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    name = "app_config_20260101_000000_manual.json"
    (folder / name).write_text("{ 这不是合法 JSON", encoding="utf-8")
    result = cb.restore(name)
    assert result["ok"] is False
    assert "解析" in result["message"]


def test_restore_rejects_empty_object(sandbox):
    folder = cb.backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    name = "app_config_20260101_000000_manual.json"
    (folder / name).write_text("{}", encoding="utf-8")
    result = cb.restore(name)
    assert result["ok"] is False
    assert "为空" in result["message"] or "格式" in result["message"]


def test_restore_only_accepts_file_name(sandbox, tmp_path):
    """恢复时只取文件名，防路径穿越（不能靠 ../ 读到别处）。"""
    folder = cb.backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "secret.json"
    outside.write_text(json.dumps({"token": "x"}), encoding="utf-8")
    result = cb.restore("../secret.json")
    assert result["ok"] is False


def test_restore_writes_backup_before_replacing(sandbox, monkeypatch):
    """恢复前必须留一份"恢复前快照"，否则误恢复就回不去了。"""
    folder = cb.backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    name = "app_config_20260101_000000_manual.json"
    (folder / name).write_text(json.dumps({"theme": "light"}), encoding="utf-8")

    applied: dict = {}

    class FakeConfig:
        def replace(self, data):
            applied.update(data)
            return True

    monkeypatch.setattr("backend.core.config.config", FakeConfig(), raising=False)

    result = cb.restore(name)
    assert result["ok"] is True
    assert applied == {"theme": "light"}
    assert result["safetyBackup"], "应当留下恢复前快照"
    reasons = [item["reason"] for item in cb.list_backups()]
    assert "恢复前快照" in reasons


def test_status_reports_totals(sandbox):
    cb.create_backup("manual")
    info = cb.status()
    assert info["count"] == 1
    assert info["keep"] == cb.KEEP
    assert info["totalKB"] >= 0
