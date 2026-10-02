"""文件 → 集成套件的路由。

这一层的坑在于**各机房装的东西不一样**：映射表写死一个套件名，
换个机房就变成"点了没反应"。所以测试重点放在"候选挑选"上。
"""
from __future__ import annotations

from backend.core import app_locator as al


# ── 路由表结构 ──────────────────────────────────────────────


def test_routes_are_candidate_lists():
    """每个扩展名的候选必须是非空的元组（防止改回单个字符串）。"""
    for suffix, candidates in al.FILE_ROUTES.items():
        assert isinstance(candidates, tuple), f"{suffix} 应该是候选元组"
        assert candidates, f"{suffix} 的候选不能为空"
        for app in candidates:
            assert app in al._APP_TO_TOOL, f"{suffix} 的候选 {app} 没有对应的工具 id"


def test_office_types_offer_fallback():
    """办公文档必须有不只一个候选 —— 这正是"换个机房就点不动"的修复点。"""
    for suffix in (".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"):
        assert len(al.FILE_ROUTES[suffix]) >= 2, f"{suffix} 应提供备用套件"


def test_pdf_only_libreoffice():
    """OpenOffice 打不开 PDF，不该把它列成候选。"""
    assert al.FILE_ROUTES[".pdf"] == ("libreoffice",)


# ── 挑选逻辑 ────────────────────────────────────────────────


def test_route_prefers_first_available(monkeypatch):
    monkeypatch.setattr(al, "_app_available", lambda app: app == "openoffice")
    # 首选 libreoffice 不可用 → 落到 openoffice
    assert al.route_file("课件.docx") == al._APP_TO_TOOL["openoffice"]


def test_route_uses_first_when_both_available(monkeypatch):
    monkeypatch.setattr(al, "_app_available", lambda app: True)
    assert al.route_file("课件.docx") == al._APP_TO_TOOL["libreoffice"]


def test_route_returns_none_when_none_available(monkeypatch):
    """一个都没装时返回 None，由调用方给出"去工具页装一个"的提示。"""
    monkeypatch.setattr(al, "_app_available", lambda app: False)
    assert al.route_file("课件.docx") is None


def test_route_unknown_suffix(monkeypatch):
    monkeypatch.setattr(al, "_app_available", lambda app: True)
    assert al.route_file("说明.xyz") is None


def test_route_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(al, "_app_available", lambda app: True)
    assert al.route_file("第1课.DOCX") == al._APP_TO_TOOL["libreoffice"]


def test_route_handles_no_suffix(monkeypatch):
    monkeypatch.setattr(al, "_app_available", lambda app: True)
    assert al.route_file("README") is None


# ── 提示用的候选列表 ────────────────────────────────────────


def test_candidates_lists_supported_tools():
    assert al.route_candidates("课件.docx") == ["libreoffice", "openoffice"]
    assert al.route_candidates("说明.xyz") == []


def test_candidates_ignore_installation_state(monkeypatch):
    """候选列表是"能选什么"，与装没装无关（提示里要列出全部可选）。"""
    monkeypatch.setattr(al, "_app_available", lambda app: False)
    assert al.route_candidates("课件.docx") == ["libreoffice", "openoffice"]


def test_supported_types_covers_all_keys():
    """右键菜单注册的扩展名来自 FILE_ROUTES 的键，必须齐全且不重复。"""
    from backend.system.shell_integration import _supported_types

    registered = _supported_types()
    assert set(registered) == set(al.FILE_ROUTES)
    assert len(registered) == len(set(registered))
