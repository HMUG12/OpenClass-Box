"""
配置自动备份与历史 —— 改乱了能回退。

为什么需要：

  配置是这个软件的"记忆"：外观、启动行为、受保护页面、机房参数、
  学期模式进度、密码保护范围……一旦被改乱（学生乱点、误操作、
  换机器时被覆盖），用户只能一项项重新设。按天留一份快照，
  任何时候都能退回之前那份。

设计取舍：

  · **只备份配置本身** —— 不含日志、缓存、插件与临时文件。
    那些东西混进来只会让备份体积失控，而且它们本来也不该被"回退"；
  · **每天最多一份**（启动时检查）—— 避免频繁重启把历史刷成一堆
    毫无意义的快照，把有价值的旧版本挤掉；
  · **保留最近 30 份**（约一个月）；
  · **恢复前先存一份"恢复前快照"** —— 恢复本身也可能是个误操作，
    得让自己也能反悔；
  · 删除**逐个进行并容错**：某些环境对批量删除有安全拦截，
    删不掉就留着 —— 绝不因为清理失败而影响主流程。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .paths import config_dir

KEEP = 30               # 保留最近多少份
# 末尾的可选序号用于"同一秒内的多次备份"（否则会互相覆盖、丢历史）
_NAME_RE = re.compile(r"^app_config_(\d{8})_(\d{6})(?:_([a-z]+))?(?:_(\d+))?\.json$")

REASON_LABELS = {
    "auto": "每日自动",
    "manual": "手动备份",
    "prerestore": "恢复前快照",
}


def backup_dir() -> Path:
    return config_dir() / "config_backup"


def _stamp() -> tuple[str, str]:
    now = time.localtime()
    return time.strftime("%Y%m%d", now), time.strftime("%H%M%S", now)


def _reason_label(name: str) -> str:
    found = _NAME_RE.match(name)
    if not found:
        return ""
    return REASON_LABELS.get(found.group(3) or "auto", found.group(3) or "")


def _describe(name: str) -> dict[str, Any]:
    """读一份备份的摘要信息（不返回全部内容，列表不需要）。"""
    path = backup_dir() / name
    info: dict[str, Any] = {
        "name": name,
        "reason": _reason_label(name),
        "size": 0,
        "keys": 0,
        "time": "",
        "valid": False,
    }
    try:
        stat = path.stat()
        info["size"] = stat.st_size
        info["time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
    except OSError:
        return info
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return info
    if isinstance(data, dict):
        info["keys"] = len(data)
        info["valid"] = True
    return info


def list_backups() -> list[dict[str, Any]]:
    """备份列表（新 → 旧）。"""
    folder = backup_dir()
    if not folder.is_dir():
        return []
    names = [item.name for item in folder.glob("app_config_*.json") if _NAME_RE.match(item.name)]
    names.sort(reverse=True)
    return [_describe(name) for name in names[:KEEP + 10]]


def current_snapshot() -> dict[str, Any]:
    """当前配置内容（优先读文件，读不到就用内存里的）。"""
    from .config import config

    try:
        from .paths import config_file

        path = config_file()
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, ValueError):
        pass
    return dict(config.snapshot())


def create_backup(reason: str = "manual") -> dict[str, Any]:
    """写一份备份。当天已有同类自动备份时不重复写（除非手动）。"""
    day, clock = _stamp()
    folder = backup_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "message": f"备份目录不可写：{exc}"}

    if reason == "auto":
        existing = [item.name for item in folder.glob(f"app_config_{day}_*_auto.json")]
        if existing:
            return {"ok": True, "message": "今天已经自动备份过", "name": existing[0], "skipped": True}

    # 文件名精确到秒，同一秒内连点两次「立即备份」会撞名 —— 撞了就加序号，
    # 绝不让新备份覆盖旧备份（那正是用户点第二次的原因）
    base = f"app_config_{day}_{clock}_{reason}"
    name = f"{base}.json"
    suffix = 1
    while (folder / name).exists():
        suffix += 1
        name = f"{base}_{suffix}.json"

    path = folder / name
    data = current_snapshot()
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "message": f"备份写入失败：{exc}"}

    removed = prune()
    return {
        "ok": True,
        "message": f"已备份 {len(data)} 项设置" + (f"，清理了 {removed} 份旧备份" if removed else ""),
        "name": name,
        "keys": len(data),
    }


def prune(keep: int = KEEP) -> int:
    """只保留最近 keep 份，其余逐个删除（失败就留着）。"""
    folder = backup_dir()
    if not folder.is_dir():
        return 0
    names = [item.name for item in folder.glob("app_config_*.json") if _NAME_RE.match(item.name)]
    names.sort(reverse=True)
    removed = 0
    for name in names[keep:]:
        try:
            (folder / name).unlink()
            removed += 1
        except OSError:
            continue
    return removed


def restore(name: str) -> dict[str, Any]:
    """恢复某份备份（恢复前自动留一份当前配置）。"""
    folder = backup_dir()
    path = folder / Path(str(name)).name       # 只允许取文件名，防路径穿越
    if not path.is_file():
        return {"ok": False, "message": "备份文件不存在（可能已被清理）"}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": f"备份内容无法解析：{exc}"}
    if not isinstance(data, dict) or not data:
        return {"ok": False, "message": "备份内容为空或格式不对，已取消恢复"}

    # 恢复也可能是个误操作 —— 先把"现在"存下来，随时能退回来
    safety = create_backup("prerestore")

    from .config import config

    if not config.replace(data):
        return {"ok": False, "message": "写入配置失败（数据目录不可写），当前设置未被改动"}

    return {
        "ok": True,
        "message": f"已恢复到 {name}（{len(data)} 项设置）"
        + ("；恢复前的配置已另存一份" if safety.get("ok") else ""),
        "keys": len(data),
        "safetyBackup": safety.get("name", ""),
    }


def auto_backup() -> dict[str, Any]:
    """程序启动时调用：当天没有自动备份就补一份。"""
    try:
        return create_backup("auto")
    except Exception as exc:  # noqa: BLE001 —— 备份失败绝不能影响启动
        return {"ok": False, "message": f"自动备份跳过：{exc}"}


def status() -> dict[str, Any]:
    """给界面用的状态汇总。"""
    items = list_backups()
    total = sum(item["size"] for item in items)
    return {
        "ok": True,
        "dir": str(backup_dir()),
        "count": len(items),
        "totalKB": round(total / 1024, 1),
        "keep": KEEP,
        "items": items,
        "hasAutoToday": any(
            item["name"].startswith(f"app_config_{_stamp()[0]}_") and item["reason"] == REASON_LABELS["auto"]
            for item in items
        ),
    }
