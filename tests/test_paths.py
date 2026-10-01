"""路径与数据目录选择 —— 「设置保存不了」的根因都在这一层。"""
from __future__ import annotations

from pathlib import Path

from backend.core import paths


def test_app_root_is_repo_root():
    assert paths.app_root() == Path(__file__).resolve().parents[1]


def test_writable_probe_creates_and_cleans(tmp_path):
    target = tmp_path / "a" / "b"
    assert paths.is_writable(target) is True
    assert not (target / ".oc_write_test").exists()   # 探针文件必须清掉


def test_protected_dir_detection_is_path_based(monkeypatch, tmp_path):
    """受保护目录必须按路径判定（"试着写一下"在 UAC 虚拟化下会误判成功）。"""
    fake = tmp_path / "Program Files"
    fake.mkdir()
    monkeypatch.setenv("ProgramFiles", str(fake))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("ProgramW6432", raising=False)
    assert paths._in_protected_dir(fake / "OpenClass-Box") is True
    assert paths._in_protected_dir(tmp_path / "elsewhere") is False


def test_portable_flag_forces_program_directory(monkeypatch, tmp_path):
    """放了 portable.flag 就必须用程序目录下的 data/（数据随程序走）。"""
    prog = tmp_path / "prog"
    prog.mkdir()
    (prog / paths.PORTABLE_FLAG).write_text("", encoding="utf-8")

    monkeypatch.setattr(paths, "app_root", lambda: prog)
    monkeypatch.setattr(paths, "IS_FROZEN", True)
    monkeypatch.setattr(paths, "_data_root_cache", None)
    monkeypatch.setattr(paths, "_data_migrated_from", "")

    assert paths.portable_mode() is True
    assert paths.data_root() == prog / "data"


def test_config_file_lives_under_data_root(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "app_root", lambda: tmp_path)
    monkeypatch.setattr(paths, "IS_FROZEN", False)
    monkeypatch.setattr(paths, "_data_root_cache", None)
    monkeypatch.setattr(paths, "_data_migrated_from", "")
    assert paths.config_file().parent == paths.data_root()
