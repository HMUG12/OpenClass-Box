"""
定位本机已安装的第三方应用，并把文件扩展名路由到对应工具。

设计立场（图吧式工具箱）：**不内置** 7-Zip / VLC / LibreOffice / OpenOffice
这些数百 MB 级的大型软件，而是「发现并调度」本机已安装的版本，或 tools/
目录下的便携版。找不到时相关工具标记为不可用，而非报错。

这样 OpenClass 本身保持轻量（几 MB），又能把专业软件统一收编进右键菜单。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

from .paths import app_root

try:
    import winreg
except ImportError:  # 非 Windows（仅用于开发预览）
    winreg = None


# 每个应用的定位策略，按顺序尝试：App Paths 注册表 → 软件注册表 → 常见目录 → 便携版
_APPS: dict[str, dict] = {
    "7zip": {
        "app_paths": "7zFM.exe",
        "reg_keys": (
            [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\7-Zip", ("Path64", "Path32"), "7zFM.exe")]
            if winreg else []
        ),
        "program_dirs": ["7-Zip\\7zFM.exe"],
        "portable": ("7zip", ("7zFM.exe", "7zG.exe")),
    },
    "vlc": {
        "app_paths": "vlc.exe",
        "reg_keys": [],
        "program_dirs": ["VideoLAN\\VLC\\vlc.exe"],
        "portable": ("vlc", ("vlc.exe",)),
    },
    "libreoffice": {
        "app_paths": "soffice.exe",
        # 与 OpenOffice 同名，必须用目录关键字区分，否则会误指向 OpenOffice
        "app_paths_hint": "libreoffice",
        "reg_keys": (
            [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\LibreOffice\LibreOffice", ("Path",), "program\\soffice.exe")]
            if winreg else []
        ),
        "program_dirs": ["LibreOffice\\program\\soffice.exe"],
        "portable": ("libreoffice", ("program\\soffice.exe",)),
    },
    "openoffice": {
        "app_paths": "soffice.exe",
        "app_paths_hint": "openoffice",
        # 随包携带便携版；路径含中文时由 ensure_openoffice_portable() 迁移到英文目录
        "openoffice_portable": True,
        "reg_keys": [],
        # OpenOffice 4.1.x 无法在非 ASCII（中文）路径下运行，会报
        # "central configuration" 错误。因此除 tools/ 便携版外，额外支持
        # 放到纯 ASCII 目录的完整副本，并优先于中文路径下的便携版。
        "abs_paths": [
            "D:\\OpenClassOO\\program\\soffice.exe",
            "C:\\OpenClassOO\\program\\soffice.exe",
            "C:\\Program Files\\OpenOffice 4\\program\\soffice.exe",
            "C:\\Program Files (x86)\\OpenOffice 4\\program\\soffice.exe",
            "D:\\Program Files\\OpenOffice 4\\program\\soffice.exe",
            "C:\\Program Files\\OpenOffice.org 3\\program\\soffice.exe",
        ],
        "program_dirs": [
            "OpenOffice 4\\program\\soffice.exe",
            "OpenOffice\\program\\soffice.exe",
        ],
        "portable": ("openoffice", ("program\\soffice.exe",)),
    },
}

# 应用名 → 工具 id（与 tools/<name>/tool.json 的 id 对应）
_APP_TO_TOOL: dict[str, str] = {
    "7zip": "seven_zip",
    "vlc": "vlc",
    "libreoffice": "libreoffice",
    "openoffice": "openoffice",
}

# 扩展名 → **候选应用**（按本机实际可用性挑第一个能用的）
#
# 这里以前是一对一硬编码（比如 .docx 固定给 libreoffice）。问题是各机房装的
# 东西不一样：学校里多半是 WPS 或 MS Office，随包带的又常是 OpenOffice ——
# 硬编码的那个没装，用户看到的就是"点了没反应"，而这恰恰最容易被当成软件坏了。
# 改成候选列表后，装了哪个就用哪个；一个都没有时由调用方明确告知还缺什么。
FILE_ROUTES: dict[str, tuple[str, ...]] = {
    ".zip": ("7zip",), ".7z": ("7zip",), ".rar": ("7zip",), ".tar": ("7zip",),
    ".gz": ("7zip",), ".bz2": ("7zip",), ".xz": ("7zip",), ".iso": ("7zip",), ".001": ("7zip",),
    ".mp4": ("vlc",), ".mkv": ("vlc",), ".avi": ("vlc",), ".mov": ("vlc",), ".flv": ("vlc",),
    ".wmv": ("vlc",), ".mpg": ("vlc",), ".mpeg": ("vlc",), ".webm": ("vlc",),
    ".mp3": ("vlc",), ".flac": ("vlc",), ".wav": ("vlc",), ".m4a": ("vlc",),
    ".aac": ("vlc",), ".ogg": ("vlc",), ".wma": ("vlc",), ".ape": ("vlc",),
    ".doc": ("libreoffice", "openoffice"), ".docx": ("libreoffice", "openoffice"),
    ".xls": ("libreoffice", "openoffice"), ".xlsx": ("libreoffice", "openoffice"),
    ".ppt": ("libreoffice", "openoffice"), ".pptx": ("libreoffice", "openoffice"),
    ".odt": ("libreoffice", "openoffice"), ".ods": ("libreoffice", "openoffice"),
    ".odp": ("libreoffice", "openoffice"), ".rtf": ("libreoffice", "openoffice"),
    ".csv": ("libreoffice", "openoffice"),
    # OpenOffice 不能打开 PDF，所以只列 LibreOffice
    ".pdf": ("libreoffice",),
}


def _from_app_paths(exe_name: str, hint: str = "") -> Optional[Path]:
    """从 App Paths 注册表定位。

    hint 是路径必须包含的关键字（小写）：OpenOffice 与 LibreOffice 的
    可执行文件同名（都是 soffice.exe），只按文件名匹配会把两者搞混，
    必须用目录关键字区分。
    """
    if not winreg or not exe_name:
        return None
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}",
        )
        value, _ = winreg.QueryValueEx(key, None)
        path = Path(value)
        if path.exists() and (not hint or hint in str(path).lower()):
            return path
    except OSError:
        pass
    return None


def _from_reg_keys(specs: list) -> Optional[Path]:
    if not winreg:
        return None
    for hkey, sub, valnames, suffix in specs:
        try:
            key = winreg.OpenKey(hkey, sub)
        except OSError:
            continue
        try:
            for v in valnames:
                try:
                    base, _ = winreg.QueryValueEx(key, v)
                except OSError:
                    continue
                path = Path(base) / suffix
                if path.exists():
                    return path
        finally:
            winreg.CloseKey(key)
    return None


def _from_program_dirs(rel_names: list[str]) -> Optional[Path]:
    bases = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        "C:\\Program Files",
        "C:\\Program Files (x86)",
    ]
    for base in bases:
        if not base:
            continue
        for rel in rel_names:
            path = Path(base) / rel
            if path.exists():
                return path
    return None


def _from_abs_paths(paths: list[str]) -> Optional[Path]:
    """按绝对路径直接查找。

    用于无法放在中文路径下运行的软件（如 OpenOffice 4.1.x），
    允许把它们放到纯 ASCII 目录后再被发现。
    """
    for raw in paths:
        path = Path(raw)
        if path.is_file():
            return path
    return None


def _is_ascii_path(path: Path) -> bool:
    """路径是否全为 ASCII。

    OpenOffice 4.1.x 在含中文等非 ASCII 字符的路径下必定启动失败
    （central configuration 错误），因此必须先做这个判断。
    """
    try:
        str(path).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _openoffice_workdir(create: bool = False) -> Optional[Path]:
    """可承载 OpenOffice 的英文可写目录（优先 C:\\ProgramData）。

    只在需要（create=True）时创建与写测试，避免扫描阶段产生磁盘动作。
    """
    candidates = [
        Path(os.environ.get("ProgramData", r"C:\ProgramData"))
        / "OpenClass-Box"
        / "openoffice",
        Path(os.environ.get("LOCALAPPDATA", "")) / "OpenClass-Box" / "openoffice",
    ]
    for path in candidates:
        if not _is_ascii_path(path):
            continue
        if path.is_dir() and (path / "program" / "soffice.exe").is_file():
            return path
        if not create:
            continue
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".oc_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return path
        except OSError:
            continue
    return None


def _find_openoffice_portable() -> Optional[Path]:
    """查找「能直接运行」的 OpenOffice 便携版（纯查找，不做迁移）。"""
    portable = app_root() / "tools" / "openoffice" / "program" / "soffice.exe"
    if portable.is_file() and _is_ascii_path(portable):
        return portable

    migrated_dir = _openoffice_workdir(create=False)
    if migrated_dir is not None:
        migrated = migrated_dir / "program" / "soffice.exe"
        if migrated.is_file():
            return migrated
    return None


def ensure_openoffice_portable() -> tuple[Optional[Path], str]:
    """确保有一个可运行的 OpenOffice（必要时把便携版迁移到英文目录）。

    返回 (soffice.exe 路径, 说明)。仅在用户点击启动时调用，
    因为迁移需要复制数百 MB，不能放在启动扫描里。
    """
    found = _find_openoffice_portable()
    if found is not None:
        return found, ""

    portable_root = app_root() / "tools" / "openoffice"
    portable_exe = portable_root / "program" / "soffice.exe"
    if not portable_exe.is_file():
        return None, "未随包携带 OpenOffice 便携版"

    # 随包版本存在但路径含中文 → 迁移到英文目录
    workdir = _openoffice_workdir(create=True)
    if workdir is None:
        return None, "程序目录含中文，且系统盘没有可写的英文目录，无法运行 OpenOffice"

    try:
        shutil.copytree(portable_root, workdir, dirs_exist_ok=True)
    except OSError as exc:
        return None, f"迁移 OpenOffice 失败：{exc}"

    target = workdir / "program" / "soffice.exe"
    if target.is_file():
        return target, ""
    return None, "迁移 OpenOffice 后仍找不到 soffice.exe"


def _from_portable(subdir: str, names: tuple[str, ...]) -> Optional[Path]:
    root = app_root()  # 全局路径真相：开发态为项目根，打包态为 exe 目录
    folder = root / "tools" / subdir
    if not folder.is_dir():
        return None
    # 递归查找，容忍不同便携版解压出的目录层级（如 vlc-3.0.21\vlc.exe）
    for name in names:
        for hit in folder.rglob(name):
            if hit.is_file():
                return hit
    return None


def find_app(name: str) -> Optional[Path]:
    """返回应用可执行文件路径；找不到返回 None。"""
    spec = _APPS.get(name)
    if not spec:
        return None
    for finder in (
        lambda: _from_abs_paths(spec.get("abs_paths", [])),
        lambda: _from_app_paths(spec.get("app_paths", ""), spec.get("app_paths_hint", "")),
        lambda: _from_reg_keys(spec.get("reg_keys", [])),
        lambda: _from_program_dirs(spec.get("program_dirs", [])),
        lambda: (_find_openoffice_portable() if spec.get("openoffice_portable") else None),
        lambda: _from_portable(*spec.get("portable", ("", ()))),
    ):
        path = finder()
        if path:
            return path
    return None


def _app_available(app: str) -> bool:
    """该应用当前是否可用（能定位到可执行文件）。"""
    return find_app(app) is not None


def route_file(path: str | os.PathLike) -> Optional[str]:
    """按扩展名挑一个**本机真的能用**的集成套件；一个都用不了返回 None。

    与旧实现的区别：旧的是"查表得到唯一答案"，现在是"在候选里找第一个
    真正装了的"。这样同一份配置在不同机房都能工作。
    """
    suffix = Path(path).suffix.lower()
    for app in FILE_ROUTES.get(suffix, ()):
        tool_id = _APP_TO_TOOL.get(app)
        if tool_id and _app_available(app):
            return tool_id
    return None


def route_candidates(path: str | os.PathLike) -> list[str]:
    """该文件类型**支持**的集成套件 id（不判断是否已安装）。

    给"这台机器上还没有能打开它的套件"这类提示用：告诉用户有哪些可选，
    比只说一句"失败"有用得多。
    """
    suffix = Path(path).suffix.lower()
    result: list[str] = []
    for app in FILE_ROUTES.get(suffix, ()):
        tool_id = _APP_TO_TOOL.get(app)
        if tool_id and tool_id not in result:
            result.append(tool_id)
    return result
