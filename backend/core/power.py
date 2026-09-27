"""
电源控制 —— 关闭 / 重启 / 睡眠 / 休眠 / 取消。

这是远程管理里**最危险**的一类动作：一台机器上可能跑着别人没保存的课件。
所以这里的每条设计都是为了「不会误伤」：

  1. **默认关闭**：必须在设置里显式开启「允许远程电源控制」才会执行，
     「取消关机」不受开关限制（误操作时总要有退路）；
  2. **延迟执行**：关机 / 重启默认 30 秒倒计时，期间随时可取消；
  3. **动作白名单**：只认 ACTIONS 里定义的键，其余一律拒绝；
  4. **全程留痕**：每次电源操作都写运行日志（动作 / 延迟 / 来源）；
  5. **构造与执行分离**：`build_command()` 是纯函数，可以在任何机器上
     安全断言命令是否正确；`execute()` 才真正动手。

⚠️ 维护提醒：**不要为了测试在本机调用 execute()**。要验证就测
   build_command / allowed / clamp_delay 的参数与开关逻辑，
   真实执行交给用户在目标机器上验证。
"""
from __future__ import annotations

import ctypes
import platform
import subprocess
from typing import Any

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 动作白名单：key 是对外唯一标识，界面据此渲染
ACTIONS: dict[str, dict[str, Any]] = {
    "shutdown": {"name": "关闭计算机", "danger": True, "delayable": True},
    "restart": {"name": "重新启动", "danger": True, "delayable": True},
    "sleep": {"name": "睡眠", "danger": False, "delayable": False},
    "hibernate": {"name": "休眠", "danger": False, "delayable": False},
    "cancel": {"name": "取消关机", "danger": False, "delayable": False},
}

MIN_DELAY = 15
MAX_DELAY = 600
DEFAULT_DELAY = 30


def supported_actions() -> list[dict[str, Any]]:
    """当前平台支持的动作（只读查询，不执行任何操作）。"""
    system = platform.system()
    items: list[dict[str, Any]] = []
    for key, spec in ACTIONS.items():
        if system != "Windows" and key in ("sleep", "hibernate", "cancel"):
            continue
        items.append({"key": key, **spec})
    return items


def clamp_delay(value: Any) -> int:
    """把延迟限制在安全区间：太短来不及取消，太长失去意义。"""
    try:
        delay = int(value)
    except (TypeError, ValueError):
        return DEFAULT_DELAY
    return max(MIN_DELAY, min(MAX_DELAY, delay))


def build_command(action: str, delay: int = DEFAULT_DELAY) -> list[str]:
    """构造要执行的命令行 —— 纯函数，不执行任何东西。

    安全测试入口：可以在开发机上断言命令是否正确，而不必真的关机。
    """
    action = (action or "").lower()
    if action not in ACTIONS:
        raise ValueError(f"不支持的电源操作：{action}")

    seconds = clamp_delay(delay)
    system = platform.system()

    if system == "Windows":
        if action == "shutdown":
            return ["shutdown", "/s", "/t", str(seconds), "/c", "OpenClass 远程关机"]
        if action == "restart":
            return ["shutdown", "/r", "/t", str(seconds), "/c", "OpenClass 远程重启"]
        if action == "cancel":
            return ["shutdown", "/a"]
        # 睡眠 / 休眠走系统 API（SetSuspendState），没有命令行形式
        raise ValueError(f"{action} 在 Windows 上通过系统 API 调用")

    if action == "shutdown":
        return ["shutdown", "-h", f"+{max(1, seconds // 60)}"]
    if action == "restart":
        return ["shutdown", "-r", f"+{max(1, seconds // 60)}"]
    if action == "sleep":
        return ["systemctl", "suspend"]
    raise ValueError(f"{action} 当前平台不支持")


# ══════════════════════════════════════════════════════════════
# 开关（默认关闭，必须在设置里显式开启）
# ══════════════════════════════════════════════════════════════

def allowed() -> bool:
    """是否允许远程电源控制。"""
    from .config import config

    return bool(config.get("lan_allow_power", False))


def set_allowed(value: bool) -> dict[str, Any]:
    from .config import config

    ok = config.set("lan_allow_power", bool(value))
    return {
        "ok": ok,
        "allowed": bool(value),
        "message": "已允许远程电源控制" if value else "已关闭远程电源控制（仅「取消关机」仍可用）",
    }


# ══════════════════════════════════════════════════════════════
# 执行（只在目标机上调用）
# ══════════════════════════════════════════════════════════════

def execute(action: str, delay: int = DEFAULT_DELAY, source: str = "") -> dict[str, Any]:
    """真正执行电源动作。

    ⚠️ 开发机不要调用本函数来「测试」—— 它会真的关机。
    """
    action = (action or "").lower()
    if action not in ACTIONS:
        return {"ok": False, "message": f"不支持的电源操作：{action}", "data": {}}

    spec = ACTIONS[action]
    seconds = clamp_delay(delay)

    # 全程留痕：出问题时能从日志看出是谁在什么时候下的指令
    try:
        from .applog import log

        log(
            f"电源操作：{spec['name']}",
            "WARN",
            delay=seconds if spec.get("delayable") else "-",
            source=source or "本机",
        )
    except Exception:
        pass

    try:
        if action in ("sleep", "hibernate") and platform.system() == "Windows":
            # SetSuspendState(bHibernate, bForce, bWakeupEventsDisabled)
            # 睡眠用 0、休眠用 1；bForce=1 避免「有程序阻止睡眠」而静默失败
            result = ctypes.windll.powrprof.SetSuspendState(  # type: ignore[attr-defined]
                1 if action == "hibernate" else 0, 1, 0
            )
            if not result:
                return {"ok": False, "message": f"{spec['name']}失败（系统拒绝了请求）", "data": {}}
            return {"ok": True, "message": f"已进入{spec['name']}", "data": {}}

        cmd = build_command(action, seconds)
        subprocess.Popen(cmd, creationflags=_CREATE_NO_WINDOW)

        if action == "shutdown":
            return {
                "ok": True,
                "message": f"{seconds} 秒后关机（期间可点「取消关机」）",
                "data": {"delay": seconds},
            }
        if action == "restart":
            return {
                "ok": True,
                "message": f"{seconds} 秒后重启（期间可点「取消关机」）",
                "data": {"delay": seconds},
            }
        if action == "cancel":
            return {"ok": True, "message": "已取消计划中的关机 / 重启", "data": {}}
        return {"ok": True, "message": f"已执行：{spec['name']}", "data": {}}
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError) as exc:
        return {"ok": False, "message": f"执行失败：{exc}", "data": {}}
