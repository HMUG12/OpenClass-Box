"""
统一路径管理 —— 整个应用唯一的路径真相来源。

数据目录的三条铁律（「设置改完下次又变回去」就是在这里翻车的）：

  1. **便携优先**：可移动介质（U 盘）上的程序，或程序目录里放了
     `portable.flag`，数据就跟随程序走 —— 插到哪台机器都是同一套设置；
  2. **受保护目录必须避开**：`C:\\Program Files` 下，管理员进程与普通用户
     看到的**不是同一份文件**（普通用户可能被 UAC 虚拟化重定向到
     VirtualStore）。把配置放进去，就会出现「这次保存成功、下次打开又变回去」。
     所以判定必须基于**路径本身**，而不是「试着写一下」——写测试在
     受保护目录里可能"成功"，但那份数据下次读不到；
  3. **开发模式跟随项目**：未打包时用项目下的 data/，方便调试。

打包模式不写 `sys._MEIPASS`：工具箱要求「拷贝到哪里都能跑」，运行时数据与
tools/ 必须跟随 exe。前端产物仍从 `_MEIPASS/frontend/dist` 读取（只读资源）。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

IS_FROZEN: bool = getattr(sys, "frozen", False)

# 在程序目录放一个同名空文件即可强制便携模式（绿色版分发时用）
PORTABLE_FLAG = "portable.flag"

# 需要从旧位置迁移过来的配置文件（只搬配置，不搬大文件）
_MIGRATE_FILES = (
    "app_config.json",
    "security_events.json",
    "webconsole.json",
)


def app_root() -> Path:
    """程序根目录。"""
    if IS_FROZEN:
        return Path(sys.executable).resolve().parent
    # backend/core/paths.py → core → backend → 根
    return Path(__file__).resolve().parents[2]


def resource_path(*parts: str) -> Path:
    return app_root().joinpath(*parts)


def tools_dir() -> Path:
    """外部工具与插件的根目录。"""
    return resource_path("tools")


def _local_appdata() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base)
    return Path.home() / "AppData" / "Local"


def local_data_dir() -> Path:
    """用户级数据目录（安装版固定使用这里）。"""
    return _local_appdata() / "OpenClass-Box"


def _in_protected_dir(path: Path) -> bool:
    """是否位于受保护目录（Program Files / Windows）。

    这些目录下「管理员」与「普通用户」拿到的文件不是同一份，
    配置放在这里必然时好时坏，因此直接避开。
    """
    try:
        target = path.resolve()
    except OSError:
        target = path

    roots: list[Path] = []
    for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "SystemRoot", "windir"):
        value = os.environ.get(key)
        if value:
            roots.append(Path(value))

    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        if target == resolved or resolved in target.parents:
            return True
    return False


def _is_writable(path: Path) -> bool:
    """真实写测试（仅用于可写位置之间的选择）。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".oc_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def is_writable(path: Path) -> bool:
    """公开的可写判断（供界面展示「配置能否落盘」）。"""
    return _is_writable(path)


def _candidate_dirs() -> list[Path]:
    """曾经可能存过数据的位置（用于一次性迁移旧配置）。"""
    candidates = [app_root() / "data", local_data_dir(), Path.home() / ".openclass-box"]
    unique: list[Path] = []
    for item in candidates:
        if item not in unique:
            unique.append(item)
    return unique


def _migrate_config(target: Path) -> str:
    """新位置还没有配置时，从旧位置搬一份过来（避免用户「设置全没了」）。"""
    if (target / "app_config.json").exists():
        return ""
    for candidate in _candidate_dirs():
        if candidate == target:
            continue
        source = candidate / "app_config.json"
        if not source.is_file():
            continue
        try:
            target.mkdir(parents=True, exist_ok=True)
            for name in _MIGRATE_FILES:
                item = candidate / name
                if item.is_file() and not (target / name).exists():
                    shutil.copy2(item, target / name)
            return str(candidate)
        except OSError:
            continue
    return ""


_data_root_cache: Path | None = None
_data_migrated_from = ""


def portable_mode() -> bool:
    """是否处于便携模式（数据跟随程序目录）。"""
    if (app_root() / PORTABLE_FLAG).exists():
        return True
    try:
        from .portable import is_portable_media

        return is_portable_media()
    except Exception:
        return False


def data_root() -> Path:
    """可写数据目录（配置 / 壁纸 / 安全事件 / 收发文件都放这里）。

    决策顺序：
      1. 便携模式（U 盘或 portable.flag）→ 程序目录 data/；
      2. 开发模式（未打包）→ 项目目录 data/；
      3. 安装到受保护目录 → %LOCALAPPDATA%\\OpenClass-Box；
      4. 其余（绿色版放在可写位置）→ 程序目录 data/，不可写再回退用户目录。
    """
    global _data_root_cache, _data_migrated_from
    if _data_root_cache is not None:
        return _data_root_cache

    portable_dir = app_root() / "data"
    local_dir = local_data_dir()

    if portable_mode():
        chosen = portable_dir if _is_writable(portable_dir) else local_dir
    elif not IS_FROZEN:
        chosen = portable_dir
    elif _in_protected_dir(app_root()):
        # 关键修复：受保护目录一律不用，避免 UAC 虚拟化造成的「配置时有时无」
        chosen = local_dir
    elif _is_writable(portable_dir):
        chosen = portable_dir
    else:
        chosen = local_dir

    try:
        chosen.mkdir(parents=True, exist_ok=True)
    except OSError:
        chosen = Path.home() / ".openclass-box"
        chosen.mkdir(parents=True, exist_ok=True)

    if chosen is not portable_dir:
        _data_migrated_from = _migrate_config(chosen)

    _data_root_cache = chosen
    return chosen


def migration_note() -> str:
    """若发生过配置迁移，返回来源路径（供界面提示用户）。"""
    return _data_migrated_from


def config_dir() -> Path:
    """用户配置与运行时数据目录（保证可写）。"""
    return data_root()


def frontend_dist() -> Path:
    """前端构建产物目录。

    打包态（PyInstaller 6.x onedir）下只读资源被放到 _internal（sys._MEIPASS），
    因此前端产物位于 <_MEIPASS>/frontend/dist；tools/data 仍跟随 exe 目录。
    """
    if IS_FROZEN:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass) / "frontend" / "dist"
    return resource_path("frontend", "dist")


def frontend_index() -> Path:
    return frontend_dist() / "index.html"


def config_file() -> Path:
    return config_dir() / "app_config.json"


def ensure_runtime_dirs() -> None:
    """确保可写目录存在（失败不阻断启动）。"""
    for directory in (tools_dir(), config_dir()):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
