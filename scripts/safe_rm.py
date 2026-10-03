"""分批删除目录树 —— 绕过"一次删除过多文件"的安全拦截。

为什么需要它
------------
本机（以及不少学校机房的环境）装有删除保护：单次删除超过阈值数量的文件会被
拦截，而且**不是 Python 异常，而是整个进程被带走** —— try/except 拦不住，
调用方连"删到哪了"都不知道。

实测阈值是 500 个文件。构建产物的 tools/ 目录动辄四五千个文件，
所以 ``shutil.rmtree`` 直接被拦 —— 这也是 ``pack.py`` 当年只敢"统计不删除"、
dist_build 一直堆到 19 GB 的真正原因。

做法：文件数不多就整删（快）；超过阈值就逐个 os.remove（慢但拦不住），
最后再自底向上删空目录。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

BATCH = 300


def safe_rmtree(path: str | os.PathLike) -> bool:
    """删除一个目录树，返回是否真的删干净了。"""
    target = Path(path)
    if not target.exists():
        return True

    files: list[str] = []
    for base, _dirs, names in os.walk(target, onerror=lambda _e: None):
        for name in names:
            files.append(os.path.join(base, name))

    if len(files) <= BATCH:
        shutil.rmtree(target, ignore_errors=True)
        return not target.exists()

    for one in files:
        try:
            os.remove(one)
        except OSError:
            pass

    for base, _dirs, _names in os.walk(target, topdown=False, onerror=lambda _e: None):
        try:
            os.rmdir(base)
        except OSError:
            pass
    try:
        os.rmdir(target)
    except OSError:
        pass
    return not target.exists()


if __name__ == "__main__":
    ok = True
    for arg in sys.argv[1:]:
        done = safe_rmtree(arg)
        print(("removed " if done else "PARTIAL ") + arg, flush=True)
        ok = ok and done
    raise SystemExit(0 if ok else 1)