"""
工具注册表 —— 全应用唯一的工具发现与索引中心。

设计目标：**新增一个工具不需要修改任何 Python 代码**。
只要往 tools/ 里放一个文件夹 + 一份 tool.json，扫描时就会自动出现。

工具来源：
  - builtin  : backend/tools/ 下的自带工具（随主程序发布）
  - plugin   : tools/ 下带 tool.json 的目录
  - external : tools/ 下可直接执行的第三方程序（无清单，自动识别）
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import app_root, tools_dir
from .runner import is_executable

CATEGORY_NAMES: dict[str, str] = {
    "classroom": "课堂",
    "document": "文档",
    "file": "文件",
    "media": "媒体",
    "system": "系统",
    "network": "网络",
    "external": "外部",
    "other": "其他",
}

_MANIFEST = "tool.json"
_SCRIPT_SUFFIXES = (".py", ".exe", ".bat", ".cmd", ".ps1")

# 已知工具类型：builtin=自带、plugin=插件、external=外部程序、web=网页工具。
# 清单里写的 kind 必须先过白名单 —— 前端按 kind 渲染标签，出现未知值
# 会让整页崩掉（工具箱、系统状态都复用工具卡片，曾经真的崩过）。
_KNOWN_KINDS = {"builtin", "plugin", "external", "web"}


def _normalize_kind(value: object, fallback: str = "plugin") -> str:
    text = str(value or "").strip().lower()
    return text if text in _KNOWN_KINDS else fallback


@dataclass
class ToolSpec:
    id: str
    name: str
    description: str
    category: str
    icon: str
    version: str
    author: str
    kind: str
    entry: str
    args: list[str] = field(default_factory=list)
    admin: bool = False
    available: bool = True
    reason: str | None = None
    tags: list[str] = field(default_factory=list)
    app: str | None = None  # 关联的应用名（7zip/vlc/...），由 app_locator 动态定位
    download: str | None = None  # 未检测到时的官方下载地址

    # 内部字段：清单所在目录，不暴露给前端
    base_dir: Path = field(default=Path("."), repr=False)

    @property
    def category_name(self) -> str:
        return CATEGORY_NAMES.get(self.category, self.category)

    @property
    def entry_path(self) -> Path:
        if Path(self.entry).is_absolute():
            return Path(self.entry)
        return self.base_dir / self.entry

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "icon": self.icon,
            "version": self.version,
            "author": self.author,
            "kind": self.kind,
            "entry": self.entry,
            "args": self.args,
            "admin": self.admin,
            "available": self.available,
            "reason": self.reason,
            "tags": self.tags,
            "app": self.app,
            "download": self.download,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    # ── 扫描 ────────────────────────────────────────────

    def scan(self) -> list[ToolSpec]:
        self._tools.clear()
        self._scan_dir(app_root() / "backend" / "tools", "builtin")
        self._scan_dir(tools_dir(), None)  # None → 由清单决定 plugin/external
        return self.list()

    def _scan_dir(self, root: Path, forced_kind: str | None) -> None:
        if not root.is_dir():
            return
        try:
            children = sorted(root.iterdir())
        except OSError:
            return

        for child in children:
            if child.name.startswith((".", "__")):
                continue

            if child.is_dir():
                spec = self._load_manifest_dir(child, forced_kind)
                if spec is None and forced_kind is None:
                    spec = self._guess_external(child)
                if spec is None and forced_kind is not None:
                    spec = self._guess_external(child)
            elif is_executable(child):
                spec = self._make_auto_spec(child, forced_kind or "external")
            else:
                continue

            if spec is not None and spec.id not in self._tools:
                self._tools[spec.id] = spec

    def _load_manifest_dir(self, folder: Path, kind: str | None) -> ToolSpec | None:
        manifest = folder / _MANIFEST
        if not manifest.is_file():
            return None
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(raw, dict):
            return None

        name = raw.get("name") or folder.name
        app = raw.get("app")
        download = raw.get("download")
        spec = ToolSpec(
            id=raw.get("id") or folder.name,
            name=name,
            description=raw.get("description", ""),
            category=raw.get("category", "other"),
            icon=raw.get("icon", "🧩"),
            version=raw.get("version", "1.0.0"),
            author=raw.get("author", "未知"),
            kind=_normalize_kind(kind or raw.get("kind"), "plugin"),
            entry=raw.get("entry", ""),
            args=list(raw.get("args", [])),
            admin=bool(raw.get("admin", False)),
            tags=list(raw.get("tags", [])),
            base_dir=folder,
        )

        # 应用桥接：动态定位本机已安装的第三方软件（7zip/vlc/...）
        if app:
            from .app_locator import find_app

            spec.app = app
            spec.kind = "external"
            exe = find_app(app)
            if exe:
                spec.entry = str(exe)
                spec.available = True
            else:
                spec.entry = ""
                spec.available = False
                spec.reason = f"未检测到 {app}，请安装后重试或在设置中指定路径"
            return spec

        # 未显式指定入口 → 自动挑选同名或首个可执行文件
        if not spec.entry:
            candidate = self._find_entry(folder, preferred=folder.name)
            if candidate is None:
                return None
            spec.entry = candidate.name

        if not (folder / spec.entry).exists():
            spec.available = False
            spec.reason = f"缺少入口文件：{spec.entry}"
        return spec

    def _find_entry(self, folder: Path, preferred: str) -> Path | None:
        for suffix in _SCRIPT_SUFFIXES:
            candidate = folder / f"{preferred}{suffix}"
            if candidate.is_file():
                return candidate
        try:
            entries = [
                p
                for p in sorted(folder.iterdir())
                if p.is_file() and p.suffix.lower() in _SCRIPT_SUFFIXES
            ]
        except OSError:
            return None
        return entries[0] if entries else None

    def _guess_external(self, folder: Path) -> ToolSpec | None:
        """目录中没有 tool.json：当作第三方绿色工具自动识别。"""
        try:
            candidates = [
                p for p in sorted(folder.iterdir()) if p.is_file() and is_executable(p)
            ]
        except OSError:
            return None
        if not candidates:
            return None
        entry = candidates[0]
        return ToolSpec(
            id=folder.name,
            name=folder.name,
            description="第三方绿色工具（自动识别）",
            category="external",
            icon="📦",
            version="-",
            author="第三方",
            kind="external",
            entry=entry.name,
            base_dir=folder,
        )

    def _make_auto_spec(self, file: Path, kind: str) -> ToolSpec:
        return ToolSpec(
            id=file.stem,
            name=file.stem,
            description="独立可执行文件（自动识别）",
            category="external",
            icon="📦",
            version="-",
            author="未知",
            kind=kind,
            entry=file.name,
            base_dir=file.parent,
        )

    # ── 查询 ────────────────────────────────────────────

    def list(self) -> list[ToolSpec]:
        return sorted(self._tools.values(), key=lambda t: (t.kind != "builtin", t.category, t.name))

    def get(self, tool_id: str) -> ToolSpec | None:
        return self._tools.get(tool_id)

    def count(self) -> int:
        return len(self._tools)


#: 全局唯一实例。命名为 tool_registry 是为了避免与模块同名造成混淆。
tool_registry = ToolRegistry()
