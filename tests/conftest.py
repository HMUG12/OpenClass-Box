"""pytest 公共配置。

把仓库根加入 sys.path，让测试能以 `backend.xxx` 的方式导入业务模块
（与程序运行时一致，而不是把 backend 当成顶层包）。

测试原则（写在这里，避免以后跑偏）：
  1. **不碰真实系统**：不写注册表、不改系统设置、不启动外部程序；
  2. **绝不执行电源操作**：电源相关只断言 `build_command` 的构造结果；
  3. **不依赖网络**：涉及联网的用例一律 mock 或跳过；
  4. 需要文件系统时统一用 pytest 的 tmp_path。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
