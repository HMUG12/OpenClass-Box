"""pack.py 里旧产物备份清理的测试。

背景：原来 pack.py 只统计不删（怕批量删除被系统拦截），
代价是 dist_build 堆到 18 GB / 14 份。现在改成逐份删、失败跳过。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pack  # noqa: E402


def _make_backup(base: Path, stamp: str, size: int = 1024) -> Path:
    folder = base / f"OpenClass-Box_old_{stamp}"
    folder.mkdir(parents=True)
    (folder / "payload.bin").write_bytes(b"\0" * size)
    return folder


@pytest.fixture
def dist(tmp_path, monkeypatch):
    """把 pack 的构建目录指向临时位置，避免测试动到真实产物。"""
    monkeypatch.setattr(pack, "DIST", tmp_path)
    return tmp_path


def test_keeps_only_the_newest_backups(dist):
    """按名称倒序保留最近 2 份（名字里的时间戳可直接排序）。"""
    for stamp in ("20260101_010000", "20260102_010000", "20260103_010000", "20260104_010000"):
        _make_backup(dist, stamp)

    pack.prune_old_backups()

    left = sorted(one.name for one in dist.glob("OpenClass-Box_old_*"))
    assert left == [
        "OpenClass-Box_old_20260103_010000",
        "OpenClass-Box_old_20260104_010000",
    ]


def test_returns_freed_bytes(dist):
    """返回释放的字节数，便于调用方确认效果。"""
    for stamp in ("20260101_010000", "20260102_010000", "20260103_010000"):
        _make_backup(dist, stamp, size=2048)

    freed = pack.prune_old_backups()

    assert freed == 2048          # 只删了最旧那一份
    assert (dist / "OpenClass-Box_old_20260102_010000").is_dir()


def test_nothing_to_do_when_within_limit(dist):
    """备份不足保留份数时不动手，也不报错。"""
    _make_backup(dist, "20260103_010000")

    assert pack.prune_old_backups() == 0
    assert (dist / "OpenClass-Box_old_20260103_010000").is_dir()


def test_current_output_dir_is_never_touched(dist):
    """正在用的产物目录绝不能被当成备份删掉。"""
    _make_backup(dist, "20260101_010000")
    (dist / "OpenClass-Box").mkdir()
    (dist / "OpenClass-Box" / "OpenClass-Box.exe").write_bytes(b"MZ")

    pack.prune_old_backups()

    assert (dist / "OpenClass-Box" / "OpenClass-Box.exe").is_file()


def test_undeletable_backup_is_skipped_not_fatal(dist, monkeypatch):
    """一份删不掉就跳过继续 —— 清理绝不能把打包带崩。

    这是这个函数存在的全部理由：某些环境会拦截批量删除，
    旧实现因此干脆不删，代价是堆到 19 GB。

    用 4 份备份（keep=2 → 该删 2 份）才能真正验证"跳过一份后继续处理下一份"：
    3 份时只有 1 份该删，拦下来就没有后续动作了。

    注意 patch 的是 ``scripts.safe_rm.safe_rmtree`` —— pack 是在函数内 import 它的，
    patch pack.shutil 不起作用（这条曾经骗过我们：测试全绿，真实环境照样删不掉）。
    """
    from scripts import safe_rm

    for stamp in (
        "20260101_010000",
        "20260102_010000",
        "20260103_010000",
        "20260104_010000",
    ):
        _make_backup(dist, stamp, size=1024)

    real = safe_rm.safe_rmtree
    blocked = "OpenClass-Box_old_20260101_010000"

    def fake(target, *args, **kwargs):
        if Path(target).name == blocked:
            return False          # 删不掉，但不该抛异常
        return real(target)

    monkeypatch.setattr(safe_rm, "safe_rmtree", fake)

    freed = pack.prune_old_backups()   # 不应抛异常

    # 拦下的那份仍在；另一份该删的照删（证明没有"一遇失败就整体放弃"）
    assert (dist / blocked).is_dir()
    assert not (dist / "OpenClass-Box_old_20260102_010000").exists()
    # 最近两份始终保住
    assert (dist / "OpenClass-Box_old_20260103_010000").is_dir()
    assert (dist / "OpenClass-Box_old_20260104_010000").is_dir()
    assert freed == 1024             # 只成功删了一份


def test_undeletable_backups_are_reported_with_manual_hint(dist, monkeypatch, capsys):
    """删不掉时必须**明说**，不能假装清过了。

    真实环境的删除保护是非交互进程拦不住的 —— 这时唯一有用的输出
    是"这 N 份没删掉，请手动处理"，而不是安静地跳过。
    """
    from scripts import safe_rm

    for stamp in ("20260101_010000", "20260102_010000", "20260103_010000"):
        _make_backup(dist, stamp, size=1024)
    monkeypatch.setattr(safe_rm, "safe_rmtree", lambda target, *a, **k: False)

    assert pack.prune_old_backups() == 0

    out = capsys.readouterr().out
    assert "删不掉" in out
    assert "手动清理" in out or "手动" in out


def test_respects_custom_keep(dist):
    """保留份数可调 —— 只想留 1 份时不必改常量。"""
    for stamp in ("20260101_010000", "20260102_010000", "20260103_010000"):
        _make_backup(dist, stamp)

    pack.prune_old_backups(keep=1)

    left = [one.name for one in dist.glob("OpenClass-Box_old_*")]
    assert left == ["OpenClass-Box_old_20260103_010000"]
