"""版本号单一来源：`version.json` → 所有需要写版本号的地方。

为什么需要它：版本号原先散落在四处（Python 包 / 前端 package.json /
Inno Setup 脚本 / exe 版本资源），发一次版要记得改四个地方 ——
现实里 `frontend/package.json` 就长期停在 0.1.0，和程序里显示的版本对不上。

同步目标：
  1. backend/__init__.py   __version__ = "0.1.5 Beta"
  2. frontend/package.json "version": "0.1.5"（npm 版本号不能带空格与 Beta）
  3. OpenClass.iss         AppVersion=0.1.5 Beta
  4. version_info.txt      filevers/prodvers + FileVersion + ProductVersion

用法：
    python scripts/sync_version.py           # 同步四处（默认不改 version.json）
    python scripts/sync_version.py --check   # 只校验不修改，有差异则退出码 1（供 CI 用）
    python scripts/sync_version.py --show    # 打印当前各处版本
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Windows 控制台默认 GBK：不指定编码时中文与符号会直接抛 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "version.json"

PY_FILE = ROOT / "backend" / "__init__.py"
PKG_FILE = ROOT / "frontend" / "package.json"
ISS_FILE = ROOT / "OpenClass.iss"
VI_FILE = ROOT / "version_info.txt"


def load_version() -> dict[str, str]:
    data = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
    required = ("version", "numeric", "semver", "tag")
    missing = [key for key in required if not str(data.get(key) or "").strip()]
    if missing:
        raise SystemExit(f"version.json 缺少字段：{'、'.join(missing)}")
    return {key: str(data[key]) for key in data}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


# ── 各处的「读当前值 / 写新值」────────────────────────────────

def patch_python(text: str, ver: dict[str, str]) -> str:
    return re.sub(r'__version__\s*=\s*"[^"]*"', f'__version__ = "{ver["version"]}"', text, count=1)


def read_python(text: str) -> str:
    found = re.search(r'__version__\s*=\s*"([^"]*)"', text)
    return found.group(1) if found else ""


def patch_package(text: str, ver: dict[str, str]) -> str:
    return re.sub(r'"version"\s*:\s*"[^"]*"', f'"version": "{ver["semver"]}"', text, count=1)


def read_package(text: str) -> str:
    found = re.search(r'"version"\s*:\s*"([^"]*)"', text)
    return found.group(1) if found else ""


def patch_iss(text: str, ver: dict[str, str]) -> str:
    return re.sub(r"^AppVersion=.*$", f'AppVersion={ver["version"]}', text, count=1, flags=re.M)


def read_iss(text: str) -> str:
    found = re.search(r"^AppVersion=(.*)$", text, flags=re.M)
    return found.group(1).strip() if found else ""


def patch_version_info(text: str, ver: dict[str, str]) -> str:
    numeric = ver["numeric"]
    parts = ", ".join(numeric.split("."))
    text = re.sub(r"filevers=\([^)]*\)", f"filevers=({parts})", text, count=1)
    text = re.sub(r"prodvers=\([^)]*\)", f"prodvers=({parts})", text, count=1)
    text = re.sub(r"StringStruct\('FileVersion', '[^']*'\)", f"StringStruct('FileVersion', '{numeric}')", text, count=1)
    text = re.sub(
        r"StringStruct\('ProductVersion', '[^']*'\)",
        f"StringStruct('ProductVersion', '{ver['version']}')",
        text,
        count=1,
    )
    return text


def read_version_info(text: str) -> str:
    found = re.search(r"StringStruct\('ProductVersion', '([^']*)'\)", text)
    return found.group(1) if found else ""


TARGETS = (
    ("backend/__init__.py", PY_FILE, read_python, patch_python),
    ("frontend/package.json", PKG_FILE, read_package, patch_package),
    ("OpenClass.iss", ISS_FILE, read_iss, patch_iss),
    ("version_info.txt", VI_FILE, read_version_info, patch_version_info),
)


def expected(path_name: str, ver: dict[str, str]) -> str:
    if path_name == "frontend/package.json":
        return ver["semver"]
    if path_name == "version_info.txt":
        return ver["version"]
    return ver["version"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="同步版本号（单一来源：version.json）")
    parser.add_argument("--check", action="store_true", help="只校验不修改，有差异返回 1")
    parser.add_argument("--show", action="store_true", help="只打印各处版本")
    args = parser.parse_args(argv)

    ver = load_version()
    print(f"version.json: {ver['version']}（{ver['numeric']} · {ver['tag']}）")

    mismatched: list[str] = []
    changed: list[str] = []

    for name, path, reader, patcher in TARGETS:
        if not path.is_file():
            print(f"  [跳过] {name} 不存在")
            continue
        text = _read(path)
        current = reader(text)
        want = expected(name, ver)

        if args.show:
            flag = "[OK]" if current == want else "[!!]"
            print(f"  {flag} {name}: {current or '（未找到）'}")
            continue

        if current == want:
            print(f"  [OK] {name}: {current}")
            continue

        mismatched.append(name)
        if args.check:
            print(f"  [!!] {name}: {current or '（未找到）'} -> 应为 {want}")
            continue

        _write(path, patcher(text, ver))
        changed.append(name)
        print(f"  [FIX] {name}: {current or '（未找到）'} -> {want}")

    if args.check:
        if mismatched:
            print(f"\n版本不一致：{'、'.join(mismatched)}（执行 python scripts/sync_version.py 修复）")
            return 1
        print("\n所有位置的版本号一致 [OK]")
        return 0

    if args.show:
        return 0

    if changed:
        print(f"\n已同步 {len(changed)} 处；请把改动一起提交，避免下次打包又漂移")
    else:
        print("\n所有位置的版本号本来就一致，无需改动")
    return 0


if __name__ == "__main__":
    sys.exit(main())
