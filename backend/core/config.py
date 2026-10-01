"""
配置持久化 —— 单个 JSON 文件，跟随程序目录（便携模式）。

刻意保持极简：工具箱不需要复杂的配置体系，
一份扁平的 key-value JSON 足够，且方便用户手工编辑与迁移。

「设置改完下次又变回去」的最后一道保险在这里：
  1. **原子写**（临时文件 + os.replace）：写到一半断电 / 被杀软中断，
     也不会毁掉旧配置，更不会留下半个文件；
  2. **失败自动回退**：主目录不可写时，改写到用户级目录并切换后续读写 ——
     宁可换位置，也不让设置悄悄丢掉；
  3. **记录最近一次保存结果**（时间 / 错误 / 回退位置），
     设置页可以直接展示，用户不用猜「到底存进去了没有」。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from typing import Any

from .paths import config_file, ensure_runtime_dirs, local_data_dir

_DEFAULTS: dict[str, Any] = {
    "theme": "system",
    "lastCategory": "all",
}


class Config:
    def __init__(self) -> None:
        ensure_runtime_dirs()
        self._lock = threading.RLock()
        self._path = config_file()
        self._data: dict[str, Any] = dict(_DEFAULTS)
        self._last_save_ok = True
        self._last_save_at = 0.0
        self._last_error = ""
        self._fallback = ""
        self.load()

    # ── 读 ────────────────────────────────────────────────

    def load(self) -> None:
        try:
            if self._path.exists():
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data.update(raw)
        except json.JSONDecodeError:
            # 配置损坏不应阻断启动：先把坏文件留一份备份，方便找回，
            # 再回落到默认值（而不是静默覆盖成默认）
            try:
                self._path.replace(self._path.with_suffix(".json.bad"))
            except OSError:
                pass
            self._data = dict(_DEFAULTS)
        except OSError:
            self._data = dict(_DEFAULTS)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, _DEFAULTS.get(key, default))

    def snapshot(self) -> dict[str, Any]:
        return dict(self._data)

    # ── 写 ────────────────────────────────────────────────

    def _write_to(self, path) -> None:
        """原子写：同目录临时文件 → fsync → 整体替换。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=".oc_config_",
            delete=False,
        )
        try:
            json.dump(self._data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        finally:
            handle.close()
        os.replace(handle.name, path)

    def save(self) -> bool:
        """写盘；失败会尝试回退位置，仍失败返回 False（不抛异常）。"""
        with self._lock:
            try:
                self._write_to(self._path)
                self._last_save_ok = True
                self._last_save_at = time.time()
                self._last_error = ""
                return True
            except OSError as exc:
                self._last_error = str(exc)

            # 回退：主目录不可写时写到用户级目录，并切换后续读写位置
            try:
                spare = local_data_dir() / "app_config.json"
                if str(spare) != str(self._path):
                    self._write_to(spare)
                    self._fallback = str(spare)
                    self._path = spare
                    self._last_save_ok = True
                    self._last_save_at = time.time()
                    try:
                        from .applog import log

                        log("配置目录不可写，已回退到用户目录", "WARN", path=str(spare))
                    except Exception:
                        pass
                    return True
            except OSError as exc:
                self._last_error = f"{self._last_error}；备用位置也失败：{exc}"

            self._last_save_ok = False
            self._last_save_at = time.time()
            try:
                from .applog import log

                log("配置保存失败", "ERROR", path=str(self._path), error=self._last_error)
            except Exception:
                pass
            return False

    def replace(self, data: dict[str, Any]) -> bool:
        """用给定数据整体替换当前配置（供「恢复历史备份」使用）。

        设计要点：**落盘失败就把内存也回滚** —— 否则会出现
        "界面显示已恢复、实际文件还是旧的" 这种最难排查的状态。
        调用方负责先给当前配置留一份快照（见 config_backup）。
        """
        if not isinstance(data, dict):
            return False
        previous = dict(self._data)
        self._data = dict(data)
        if self.save():
            return True
        self._data = previous
        return False

    def set(self, key: str, value: Any) -> bool:
        with self._lock:
            self._data[key] = value
        return self.save()

    # ── 诊断 ──────────────────────────────────────────────

    def diag(self) -> dict[str, Any]:
        """存储诊断：给设置页展示「配置文件在哪、上次保存成没成」。"""
        return {
            "path": str(self._path),
            "savedOk": bool(self._last_save_ok),
            "lastSaveAt": self._last_save_at,
            "lastError": self._last_error,
            "fallback": self._fallback,
            "keyCount": len(self._data),
        }


config = Config()
