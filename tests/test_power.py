"""电源动作的命令构造。

⚠️ 红线：本文件**不导入也不调用** `power.execute()` ——
在开发机上"测试"真执行会真的关机。这里只断言命令是怎么拼出来的。
"""
from __future__ import annotations

import pytest

from backend.core import power


def test_common_actions_are_supported():
    for action in ("shutdown", "restart", "sleep", "hibernate", "cancel"):
        assert action in power.ACTIONS


def test_build_command_delays_execution():
    """必须带延迟：既给误操作留取消窗口，也避免命令"立刻落下"。"""
    command = power.build_command("shutdown", 60)
    joined = " ".join(command).lower()
    assert "shutdown" in joined
    assert "60" in joined
    assert "/s" in joined


def test_build_command_clamps_delay():
    """延迟被夹在安全区间内，不接受 0 或超大值。"""
    command = power.build_command("restart", -100)
    assert str(power.MIN_DELAY) in " ".join(command)


def test_cancel_command_is_not_destructive():
    command = power.build_command("cancel", 0)
    joined = " ".join(command).lower()
    assert "shutdown" in joined and "/a" in joined


def test_unknown_action_raises():
    with pytest.raises(ValueError):
        power.build_command("format-c-drive", 0)


def test_clamp_delay_bounds():
    assert power.clamp_delay(-5) == power.MIN_DELAY
    assert power.clamp_delay(10 ** 9) == power.MAX_DELAY
    assert power.MIN_DELAY <= power.clamp_delay(power.DEFAULT_DELAY) <= power.MAX_DELAY
    assert power.clamp_delay("不是数字") == power.DEFAULT_DELAY
