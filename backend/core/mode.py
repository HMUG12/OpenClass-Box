"""使用模式 —— 这台机器交给谁用，决定界面里出现什么。

三种模式
--------
``auto``（自动化）
    给不需要动手的场景：开机自己巡检、自己清理，界面只留真正要用的入口。
    隐藏「硬件信息 / 机房管理 / 维护 / 定时任务」—— 这四块要么是给维护人员看的，
    要么是能自己改配置的（自动化模式下不该给人改的机会）。

``normal``（正常，默认）
    全部功能可见，不做任何自动动作。

``pro``（专业）
    全部功能可见，另外展开技术细节：命令行、原始检测数据、事件日志、
    进程列表、手动执行修复。

为什么模式存配置而不是编译期常量：同一台机器的角色会变 —— 期末考试周是
自动化，平时是正常，维修来了临时切专业。做成需要重装的东西就不好用了。

**模式的唯一影响是"界面显示什么"和"要不要自动做事"，不改变任何检测与修复
能力的实现** —— 三种模式跑的是同一套检测逻辑，不存在"专业模式更准"。
"""
from __future__ import annotations

from typing import Any

AUTO = "auto"
NORMAL = "normal"
PRO = "pro"

MODES = (AUTO, NORMAL, PRO)

# 每种模式的说明（前端直接显示，避免文案两处各写一份）
LABELS: dict[str, dict[str, str]] = {
    AUTO: {
        "label": "自动化",
        "summary": "开机自己巡检、自己清理，不需要动手",
        "detail": "隐藏硬件信息、机房管理、维护、定时任务四项；"
                  "开机时自动跑一轮巡检与安全清理，结果显示在首页。",
    },
    NORMAL: {
        "label": "正常",
        "summary": "全部功能都在，不自动做任何事",
        "detail": "适合自己管这台机器：所有页面可见，所有动作都由你点。",
    },
    PRO: {
        "label": "专业",
        "summary": "全部功能，另外展开技术细节",
        "detail": "命令行、原始检测数据、事件日志、进程列表、手动执行修复都放开，"
                  "适合电教与维修人员排查。",
    },
}

# 自动化模式下隐藏的页面（页面级）
HIDDEN_IN_AUTO = ("hardware", "lan", "maintenance", "tasks")

# 设置页里要按模式隐藏的板块（panel 级）。
# 「正常」模式也隐藏 —— 一个普通使用者不需要配置机房连通、也不需要知道自己
# 是 A 端还是 B 端；那些是给电教和维修人员用的，放出来只会让人不知道该填什么。
HIDDEN_IN_NORMAL = ("settings.remote", "settings.join")
HIDDEN_IN_AUTO_PANELS = HIDDEN_IN_NORMAL

# 专业化才展开的细节区块（前端按这个开关显示）
PRO_ONLY_BLOCKS = ("command", "rawData", "eventLog", "processList", "manualRepair")


def _config():
    from .config import config

    return config


def current() -> str:
    """当前模式；读不到或值不认识时回落到 normal（宁可多显示，不要卡住）。"""
    value = str(_config().get("ui_mode", NORMAL) or NORMAL).strip().lower()
    return value if value in MODES else NORMAL


def is_first_run() -> bool:
    """是否还没选过模式。

    用独立的 configured 标记而不是"mode 是不是默认值"：用户**主动**选过
    "正常"之后，不该每次启动都被当成新机器再问一遍。
    """
    return not bool(_config().get("ui_mode_configured", False))


def set_mode(mode: str) -> dict[str, Any]:
    """切换模式。会顺带标记「已配置」，首次启动的选择框不再出现。"""
    value = str(mode or "").strip().lower()
    if value not in MODES:
        return {"ok": False, "message": f"未知模式：{mode}", "mode": current()}
    cfg = _config()
    cfg.set("ui_mode", value)
    cfg.set("ui_mode_configured", True)
    return {
        "ok": True,
        "mode": value,
        "label": LABELS[value]["label"],
        "hiddenPages": list(HIDDEN_IN_AUTO) if value == AUTO else [],
        "hiddenPanels": list(HIDDEN_IN_AUTO_PANELS) if value == AUTO else (
            list(HIDDEN_IN_NORMAL) if value == NORMAL else []
        ),
    }


def is_auto() -> bool:
    return current() == AUTO


def is_pro() -> bool:
    return current() == PRO


def page_visible(page_id: str) -> bool:
    """该页面在当前模式下是否显示。"""
    if current() == AUTO and page_id in HIDDEN_IN_AUTO:
        return False
    return True


def panel_visible(panel_id: str) -> bool:
    """设置页里的某个板块在当前模式下是否显示（只有专业模式全开）。"""
    mode = current()
    if mode == PRO:
        return True
    hidden = HIDDEN_IN_AUTO_PANELS if mode == AUTO else HIDDEN_IN_NORMAL
    return panel_id not in hidden


def _hidden_panels() -> list[str]:
    mode = current()
    if mode == PRO:
        return []
    return list(HIDDEN_IN_AUTO_PANELS if mode == AUTO else HIDDEN_IN_NORMAL)


def describe() -> dict[str, Any]:
    """给前端的完整描述：当前模式、各模式说明、可见性。"""
    mode = current()
    return {
        "ok": True,
        "mode": mode,
        "label": LABELS[mode]["label"],
        "isFirstRun": is_first_run(),
        "hiddenPages": list(HIDDEN_IN_AUTO) if mode == AUTO else [],
        "hiddenPanels": _hidden_panels(),
        "proOnlyBlocks": list(PRO_ONLY_BLOCKS) if mode == PRO else [],
        "options": [
            {"id": one, **LABELS[one]} for one in MODES
        ],
    }