"""配置持久化 —— 原子写、损坏恢复、写入失败回退。"""
from __future__ import annotations

from backend.core.config import Config


def _fresh(tmp_path) -> Config:
    """构造一个指向临时目录的配置实例（不碰真实 data/）。"""
    cfg = Config()
    cfg._path = tmp_path / "app_config.json"
    cfg._data = {}
    return cfg


def test_set_then_reload_roundtrip(tmp_path):
    cfg = _fresh(tmp_path)
    assert cfg.set("theme", "dark") is True

    reopened = Config()
    reopened._path = cfg._path
    reopened._data = {}
    reopened.load()
    assert reopened.get("theme") == "dark"


def test_atomic_write_leaves_no_temp_files(tmp_path):
    cfg = _fresh(tmp_path)
    cfg.set("theme", "light")
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".oc_config_")]
    assert leftovers == []


def test_corrupt_config_is_backed_up_not_silently_lost(tmp_path):
    """配置坏了要留一份 .bad 备份，同时回落到默认值而不是卡死启动。"""
    path = tmp_path / "app_config.json"
    path.write_text("{ 这不是合法 JSON", encoding="utf-8")

    cfg = _fresh(tmp_path)
    cfg.load()

    assert (tmp_path / "app_config.json.bad").exists()
    assert isinstance(cfg.get("theme"), str) and cfg.get("theme")


def test_diag_exposes_path_and_result(tmp_path):
    cfg = _fresh(tmp_path)
    cfg.set("theme", "dark")
    info = cfg.diag()
    assert info["path"].endswith("app_config.json")
    assert info["savedOk"] is True
    assert info["lastError"] == ""
    assert info["keyCount"] >= 1


def test_save_reports_failure_instead_of_pretending(tmp_path, monkeypatch):
    """主位置与备用位置都写不进去时必须返回 False，并记下错误原因。"""
    cfg = _fresh(tmp_path)

    def _boom(_path):
        raise OSError("模拟：目录不可写")

    spare = tmp_path / "spare"
    spare.mkdir()
    monkeypatch.setattr(cfg, "_write_to", _boom)
    monkeypatch.setattr("backend.core.config.local_data_dir", lambda: spare)

    assert cfg.save() is False
    assert cfg.diag()["savedOk"] is False
    assert cfg.diag()["lastError"]


def test_snapshot_is_a_copy(tmp_path):
    cfg = _fresh(tmp_path)
    cfg.set("theme", "dark")
    snap = cfg.snapshot()
    snap["theme"] = "tampered"
    assert cfg.get("theme") == "dark"
