"""
密码保护 —— 给关键页面上一把锁。

场景：一体机就摆在教室里，学生随手就能点开「设置」「安全」「定时任务」改配置。
开启密码保护后，点这些页面前会要求输入密码；日常使用（体检、修复、工具箱、
音乐、壁纸、系统状态）完全不受影响。

实现要点：
  · 密码只保存 **PBKDF2-HMAC-SHA256 散列**（含随机盐），不存明文；
  · 连续输错 5 次锁定 1 分钟，挡住慢慢试；
  · 解锁状态只在内存里，程序重启后需要重新输入；闲置 30 分钟自动重新上锁；
  · 忘记密码也不用重装：删掉 data/passcode.json 即可复位（界面上会写明）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from .paths import config_dir

ITERATIONS = 200_000
MAX_FAILS = 5
LOCK_SECONDS = 60
AUTO_RELOCK_SECONDS = 1800   # 闲置 30 分钟自动重新上锁

# 可保护页面（与前端 PageId 对应）
PAGES: dict[str, str] = {
    "settings": "设置",
    "security": "安全",
    "tasks": "定时任务",
    "lan": "机房管理",
}

_lock = threading.RLock()
_state: dict[str, Any] = {"unlockedAt": 0.0, "fails": []}


def _file() -> Path:
    folder = config_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "passcode.json"


def _read() -> dict[str, Any]:
    path = _file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict[str, Any]) -> bool:
    try:
        _file().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def _hash(code: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", code.encode("utf-8"), bytes.fromhex(salt), ITERATIONS)
    return digest.hex()


def _unlocked() -> bool:
    with _lock:
        stamp = float(_state.get("unlockedAt") or 0)
    return bool(stamp) and (time.time() - stamp) < AUTO_RELOCK_SECONDS


def _mark_unlocked() -> None:
    with _lock:
        _state["unlockedAt"] = time.time()
        _state["fails"] = []


def lock() -> dict[str, Any]:
    """立刻重新上锁（离开电脑时用）。"""
    with _lock:
        _state["unlockedAt"] = 0.0
    return {"ok": True, "message": "已重新上锁"}


def _too_many_fails() -> bool:
    with _lock:
        now = time.time()
        recent = [t for t in _state.get("fails", []) if now - t < LOCK_SECONDS]
        _state["fails"] = recent
        return len(recent) >= MAX_FAILS


def _note_fail() -> None:
    with _lock:
        _state.setdefault("fails", []).append(time.time())


def status() -> dict[str, Any]:
    """当前状态：是否启用、保护哪些页面、是否已解锁。"""
    data = _read()
    enabled = bool(data.get("enabled")) and bool(data.get("hash"))
    protected = data.get("protected") if isinstance(data.get("protected"), list) else []
    return {
        "enabled": enabled,
        "protected": [p for p in protected if p in PAGES] if enabled else [],
        "unlocked": _unlocked() if enabled else True,
        "pages": PAGES,
        "hint": "忘记密码：删除数据目录下的 passcode.json 即可复位",
    }


def verify(code: str) -> dict[str, Any]:
    """校验密码（成功后本次运行内保持解锁）。"""
    data = _read()
    if not data.get("hash"):
        return {"ok": True, "message": "尚未设置密码", "enabled": False}
    if _too_many_fails():
        return {"ok": False, "message": f"输错次数过多，请 {LOCK_SECONDS} 秒后再试"}
    if not code:
        return {"ok": False, "message": "请输入密码"}
    if hmac.compare_digest(_hash(code, str(data.get("salt") or "")), str(data.get("hash"))):
        _mark_unlocked()
        return {"ok": True, "message": "已解锁", "enabled": True}
    _note_fail()
    return {"ok": False, "message": "密码不正确"}


def set_passcode(current: str, new_code: str, protected: list[str] | None = None) -> dict[str, Any]:
    """设置或修改密码（已有密码时必须提供当前密码）。"""
    new_code = (new_code or "").strip()
    if len(new_code) < 4:
        return {"ok": False, "message": "密码至少 4 位"}
    if len(new_code) > 32:
        return {"ok": False, "message": "密码最多 32 位"}

    data = _read()
    if data.get("hash"):
        if _too_many_fails():
            return {"ok": False, "message": f"输错次数过多，请 {LOCK_SECONDS} 秒后再试"}
        if not hmac.compare_digest(
            _hash(current or "", str(data.get("salt") or "")), str(data.get("hash"))
        ):
            _note_fail()
            return {"ok": False, "message": "当前密码不正确"}

    salt = secrets.token_hex(16)
    pages = [p for p in (protected if protected is not None else data.get("protected") or ["settings", "security", "tasks"]) if p in PAGES]
    payload = {
        "enabled": True,
        "salt": salt,
        "hash": _hash(new_code, salt),
        "iterations": ITERATIONS,
        "protected": pages or ["settings"],
        "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if not _write(payload):
        return {"ok": False, "message": "保存失败（数据目录不可写）"}
    _mark_unlocked()
    names = "、".join(PAGES.get(p, p) for p in payload["protected"])
    return {"ok": True, "message": f"密码已设置，进入「{names}」时需要输入", "status": status()}


def set_protected(pages: list[str]) -> dict[str, Any]:
    """调整受保护的页面（需要已解锁）。"""
    data = _read()
    if data.get("hash") and not _unlocked():
        return {"ok": False, "message": "请先解锁再修改保护范围"}
    valid = [p for p in (pages or []) if p in PAGES]
    if not valid:
        return {"ok": False, "message": "至少选择一个要保护的页面"}
    data["protected"] = valid
    if not _write(data):
        return {"ok": False, "message": "保存失败"}
    return {"ok": True, "message": "保护范围已更新", "status": status()}


def clear(current: str) -> dict[str, Any]:
    """关闭密码保护（需要当前密码）。"""
    data = _read()
    if not data.get("hash"):
        return {"ok": True, "message": "本来就没有设置密码"}
    if _too_many_fails():
        return {"ok": False, "message": f"输错次数过多，请 {LOCK_SECONDS} 秒后再试"}
    if not hmac.compare_digest(_hash(current or "", str(data.get("salt") or "")), str(data.get("hash"))):
        _note_fail()
        return {"ok": False, "message": "密码不正确"}
    # 必须把散列与盐一起清掉：只把 enabled 置 False 会留下「密码还写着」的
    # 错觉，手工看文件或换个版本的界面读都会以为没关干净
    payload = {
        "enabled": False,
        "protected": [],
        "salt": "",
        "hash": "",
        "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if not _write(payload):
        return {"ok": False, "message": "保存失败"}
    with _lock:
        _state["unlockedAt"] = 0.0
    return {"ok": True, "message": "已关闭密码保护"}


def needs_unlock(page: str) -> bool:
    """这个页面当前是否需要输入密码。"""
    data = _read()
    if not (data.get("enabled") and data.get("hash")):
        return False
    pages = data.get("protected") or []
    if page not in pages:
        return False
    return not _unlocked()


def reset() -> dict[str, Any]:
    """直接复位（供忘记密码时手工删文件用，这里也提供一个函数）。"""
    path = _file()
    try:
        if path.exists():
            os.remove(path)
    except OSError as exc:
        return {"ok": False, "message": f"删除失败：{exc}"}
    with _lock:
        _state["unlockedAt"] = 0.0
    return {"ok": True, "message": "密码保护已复位"}
