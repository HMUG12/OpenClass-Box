"""扫一遍前端：找出「hook 出现在提前 return 之后」的地方。

这是 React error #300（Rendered more/fewer hooks than expected）的唯一成因，
而 tsc 查不出来 —— 类型对了，hook 顺序错了。

规则：组件函数体内，hook（useState/useEffect/useMemo/useCallback/useRef…）
必须写在所有 `return (...)` 之前。一旦某个 return 提前退出，后面还有 hook，
同一个组件在两种数据状态下渲染就会崩（我们就是这么丢过三次页面的）。

用法：python scripts/check_hooks.py [目录]
退出码 1 表示发现问题（可直接接进 CI）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HOOK = re.compile(r"\buse(State|Effect|Memo|Callback|Ref|Reducer|Context|LayoutEffect)\s*\(")
RETURN = re.compile(r"^(\s+)return\s*\($")
FUNC = re.compile(r"^(export default function|export function|function)\s+(\w+)")


def check_file(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    problems: list[str] = []

    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        found = FUNC.match(line)
        if found:
            starts.append((index, found.group(2)))
    if not starts:
        return problems

    for order, (start, name) in enumerate(starts):
        end = starts[order + 1][0] if order + 1 < len(starts) else len(lines)
        segment = lines[start:end]

        # 第一个"提前 return"（形如 `return (` 且有缩进）
        first_return: int | None = None
        for offset, line in enumerate(segment):
            found = RETURN.match(line)
            if found and len(found.group(1)) >= 2:
                first_return = offset
                break
        if first_return is None:
            continue

        for offset, line in enumerate(segment):
            if offset <= first_return or not HOOK.search(line):
                continue
            stripped = line.strip()
            if stripped.startswith(("//", "*", "/*")):
                continue
            problems.append(
                f"{path.name}:{start + offset + 1}  {name}() 的 hook 出现在提前 return 之后"
                f"（return 在第 {start + first_return + 1} 行）→ {stripped[:60]}"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    root = Path(argv[0]) if argv else Path(__file__).resolve().parents[1] / "frontend" / "src"

    files = sorted(root.rglob("*.tsx"))
    problems: list[str] = []
    for path in files:
        problems.extend(check_file(path))

    if problems:
        print(f"发现 {len(problems)} 处「hook 在提前 return 之后」的问题：\n")
        for item in problems:
            print("  " + item)
        print(
            "\n这类问题 tsc 查不出来（类型没错、顺序错了），"
            "但会让页面白屏 —— React error #300。\n"
            "修法：把 hook 移到所有 return 之前；确实只想在某种模式下跑的逻辑，"
            "放进 useEffect 里判断条件。"
        )
        return 1

    print(f"已检查 {len(files)} 个 .tsx：未发现 hook 顺序问题 [OK]")
    return 0


if __name__ == "__main__":
    sys.exit(main())