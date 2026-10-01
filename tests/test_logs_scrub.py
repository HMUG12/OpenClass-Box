"""诊断包脱敏 —— 发给别人的东西不能带老师的真名。"""
from __future__ import annotations

from backend.core import logs


def test_scrub_replaces_home_directory(monkeypatch):
    monkeypatch.setenv("USERNAME", "TeacherZhang")
    text = r"路径：C:\Users\TeacherZhang\Desktop\课件.pptx"
    result = logs._scrub(text)
    assert "\\Users\\TeacherZhang" not in result
    assert "<用户>" in result or "%USERPROFILE%" in result


def test_scrub_replaces_username_before_host(monkeypatch):
    monkeypatch.setenv("USERNAME", "TeacherZhang")
    result = logs._scrub("登录来源 TeacherZhang@CLASSROOM-PC")
    assert "TeacherZhang@" not in result
    assert "<用户>@" in result


def test_scrub_does_not_mangle_similar_words(monkeypatch):
    """只做精确替换：Administrator / System 这类词不能被误伤。"""
    monkeypatch.setenv("USERNAME", "Admin")
    text = "Administrator 组、System 账户、C:\\Windows\\System32"
    result = logs._scrub(text)
    assert "Administrator" in result
    assert "System32" in result


def test_privacy_note_mentions_what_is_excluded():
    note = logs._PRIVACY_NOTE
    assert "不包含" in note
    assert "访问码" in note
