"""插件市场 —— 解压安全与清单读取（供应链风险的入口）。"""
from __future__ import annotations

import zipfile

from backend.core import market


def _zip(path, entries: dict[str, str]) -> zipfile.ZipFile:
    archive = zipfile.ZipFile(path, "w")
    for name, content in entries.items():
        archive.writestr(name, content)
    archive.close()
    return zipfile.ZipFile(path)


def test_safe_extract_blocks_path_traversal(tmp_path):
    """含 ../ 的恶意包不能让文件逃出目标目录。"""
    archive_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../escaped.txt", "bad")
        archive.writestr("ok.txt", "fine")

    target = tmp_path / "out"
    target.mkdir()
    with zipfile.ZipFile(archive_path) as archive:
        try:
            market._safe_extract(archive, target)
        except Exception:  # noqa: BLE001 —— 抛异常也是可接受的处理方式
            pass

    assert not (tmp_path / "escaped.txt").exists()


def test_peek_id_reads_manifest(tmp_path):
    archive = _zip(tmp_path / "p.zip", {"tool.json": '{"id": "demo-plugin", "name": "示例"}'})
    with archive:
        assert market._peek_id(archive) == "demo-plugin"


def test_peek_id_reads_nested_manifest(tmp_path):
    """包内多套一层目录也要能读到 id。"""
    archive = _zip(
        tmp_path / "p.zip", {"demo/tool.json": '{"id": "demo-plugin"}'}
    )
    with archive:
        assert market._peek_id(archive) == "demo-plugin"


def test_peek_id_rejects_dangerous_id(tmp_path):
    """id 里带路径分隔符一律不认（否则会用来当目录名）。"""
    archive = _zip(tmp_path / "p.zip", {"tool.json": '{"id": "../evil"}'})
    with archive:
        assert market._peek_id(archive) == ""


def test_peek_id_tolerates_broken_manifest(tmp_path):
    archive = _zip(tmp_path / "p.zip", {"tool.json": "{ 坏掉的 json"})
    with archive:
        assert market._peek_id(archive) == ""


def test_uninstall_refuses_non_plugin_directory(tmp_path, monkeypatch):
    """只能删带 tool.json 的插件目录，别把普通文件夹删掉。"""
    base = tmp_path / "tools"
    (base / "random-folder").mkdir(parents=True)
    monkeypatch.setattr(market, "tools_dir", lambda: base)

    result = market.uninstall("random-folder")
    assert result["ok"] is False
    assert (base / "random-folder").exists()


def test_uninstall_removes_plugin_directory(tmp_path, monkeypatch):
    base = tmp_path / "tools"
    plugin = base / "demo-plugin"
    plugin.mkdir(parents=True)
    (plugin / "tool.json").write_text('{"id": "demo-plugin"}', encoding="utf-8")
    monkeypatch.setattr(market, "tools_dir", lambda: base)

    result = market.uninstall("demo-plugin")
    assert result["ok"] is True
    assert not plugin.exists()


def test_install_rejects_non_https_url(tmp_path):
    """只允许 https 下载（教室网络里 http 更易被劫持替换）。"""
    result = market.install("demo-plugin", url="http://example.com/x.zip")
    assert result["ok"] is False
