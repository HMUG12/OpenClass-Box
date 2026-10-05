"""外观设置的读写测试。

这个文件存在是因为出过一次真实事故：前端把**一个 dict** 传给了期望字符串的
参数，后端照单全收、str() 之后存进配置，存成了 "{'glass': False}"。
读回来时匹配不到任何配色 id，于是**整个外观**都失效 ——
表现就是"改了没反应、重启又变回去"，而配置里看不出任何异常。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api import Api  # noqa: E402
from backend.core import config as config_mod  # noqa: E402


@pytest.fixture
def store(monkeypatch):
    """把配置读写换成内存字典，不碰真实配置。"""
    data: dict = {}
    monkeypatch.setattr(config_mod.config, "get", lambda k, d=None: data.get(k, d), raising=False)
    monkeypatch.setattr(
        config_mod.config, "set", lambda k, v: data.__setitem__(k, v) or True, raising=False
    )
    return data


def test_accepts_dict_payload(store):
    """前端习惯把要改的项打包成对象传过来，不是一个个位置参数。"""
    result = Api().set_appearance({"accent": "mint", "glass": True})
    assert result["ok"] is True
    assert store["appearance_accent"] == "mint"
    assert store["appearance_glass"] is True


def test_dict_payload_partial_update(store):
    Api().set_appearance({"radius": "round"})
    assert store["appearance_radius"] == "round"
    assert "appearance_accent" not in store


def test_positional_still_works(store):
    """旧的位置参数调用不能被这次修改弄坏。"""
    Api().set_appearance("peach", "compact", "large", False)
    assert store["appearance_accent"] == "peach"
    assert store["appearance_radius"] == "compact"
    assert store["appearance_font"] == "large"
    assert store["appearance_glass"] is False


def test_legacy_dirty_value_is_ignored(store):
    """历史遗留的脏值不能让整个外观失效 —— 退回默认即可。"""
    store["appearance_accent"] = "{'glass': False}"
    assert Api().get_appearance()["accent"] == "default"


def test_unknown_values_fall_back(store):
    store["appearance_radius"] = "超大圆角"
    store["appearance_font"] = "巨字号"
    result = Api().get_appearance()
    assert result["radius"] == "standard"
    assert result["font"] == "standard"


def test_valid_values_are_kept(store):
    store["appearance_accent"] = "sakura"
    store["appearance_radius"] = "round"
    store["appearance_font"] = "small"
    store["appearance_glass"] = True
    assert Api().get_appearance() == {
        "accent": "sakura", "radius": "round", "font": "small", "glass": True,
    }


def test_rejects_unknown_accent(store):
    """非法值要当场拒绝并说清楚，而不是默默存进去等下次启动才发现。"""
    result = Api().set_appearance({"accent": "chartreuse"})
    assert result["ok"] is False
    assert "accent" in result["message"]
    assert "appearance_accent" not in store


def test_rejects_unknown_radius_and_font(store):
    assert Api().set_appearance({"radius": "huge"})["ok"] is False
    assert Api().set_appearance({"font": "huge"})["ok"] is False
    assert store == {}


def test_none_means_no_change(store):
    """None 表示"这一项不改"，不能被当成要写入。"""
    Api().set_appearance({"accent": None, "radius": "round"})
    assert "appearance_accent" not in store
    assert store["appearance_radius"] == "round"