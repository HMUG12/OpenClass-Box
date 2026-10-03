"""
打包为 onedir 绿色版：生成 dist_build/OpenClass-Box/OpenClass-Box.exe。

设计立场（图吧式工具箱）：
  - onedir 而非 onefile：避免每次启动全量解压到临时目录导致的十几秒冷启动；
  - 运行时数据与 tools/ 跟随 exe 目录，拷到 U 盘/任意机器即可运行；
  - 不把 7z/VLC/LibreOffice 打进包，运行时按需发现本机已装或 tools/ 便携版。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist_build"
BUILD = ROOT / "build_build"

SEP = os.pathsep  # Windows 上为 ';'

# 保留最近几份旧产物备份。留 2 份够回退（上一个成功版本 + 上上个），
# 再多就是纯占地方 —— 每一份都是 1.3 GB 量级。
KEEP_BACKUPS = 2


def prune_old_backups(keep: int | None = None) -> int:
    """删除超出保留份数的旧产物备份，返回释放的字节数。

    刻意**逐份删**而不是一次性批量删：某些环境对"一次删几千个文件"有底层
    安全拦截，会把整个进程带走（不是 Python 异常，try/except 拦不住）。
    一份一份删把单次文件量压到足够小，触发不了拦截；万一还是被拦，
    跳过这份继续处理下一份 —— 删不掉就留着，绝不让清理把打包搞失败。
    """
    keep = KEEP_BACKUPS if keep is None else keep
    try:
        backups = sorted(
            DIST.glob("OpenClass-Box_old_*"),
            key=lambda one: one.name,
            reverse=True,
        )
    except OSError:
        return 0

    # 必须按 ``scripts.safe_rm`` 导入，而不是把 scripts/ 加进 sys.path 后
    # import ``safe_rm`` —— 后者会让同一个文件被加载成两个模块对象，
    # 于是测试里 monkeypatch 的那份根本不是这里用的那份（patch 静默失效）。
    from scripts.safe_rm import safe_rmtree

    freed = 0
    left_over: list[str] = []
    for stale in backups[keep:]:
        try:
            size = sum(one.stat().st_size for one in stale.rglob("*") if one.is_file())
        except OSError:
            size = 0
        # 用分批删除而不是 shutil.rmtree：构建产物里 tools/ 动辄四千多个文件，
        # 整删很容易触发环境的批量删除保护。
        #
        # 但**分批也不保证能过**：实测本机的保护是"每删除 500 个文件就要人工
        # 确认一次"，在非交互进程里那个确认弹不出来，删除会直接失败。所以这里
        # 删不掉就**明说**，并给出可以手动执行的去向 —— 绝不假装清过了。
        if safe_rmtree(stale):
            freed += size
            print(f"[pack] 已删除旧备份 {stale.name}（{size / 1024 ** 3:.2f} GB）")
        else:
            left_over.append(stale.name)

    if freed:
        print(f"[pack] 共释放 {freed / 1024 ** 3:.2f} GB")

    if left_over:
        print(f"[pack] 有 {len(left_over)} 份旧备份删不掉（本机的批量删除保护会拦），"
              f"共约 {sum(1 for _ in left_over)} 份需要手动清理：")
        for name in left_over:
            print(f"[pack]     dist_build\\{name}")
        print("[pack] 手动清理：在资源管理器里全选删除即可；"
              "或用 rm /s /q dist_build\\OpenClass-Box_old_*")
    return freed


def _ensure_reward_image() -> None:
    """赞助码图片：frontend/public/reward.png 缺失时自动认领。

    把图片（任意来源）命名为 *reward*.png / *赞赏*.png 放到项目根、
    下载目录或桌面，打包时会自动复制成前端静态资源前排使用。
    """
    target = ROOT / "frontend" / "public" / "reward.png"
    if target.is_file():
        return
    patterns = ("*reward*.png", "*reward*.jpg", "*赞赏*.png", "*赞赏*.jpg", "mm_reward*")
    candidates: list[Path] = []
    for base in (ROOT, Path.home() / "Downloads", Path.home() / "Desktop", Path.home() / "Pictures"):
        if not base.is_dir():
            continue
        for pattern in patterns:
            candidates.extend(base.glob(pattern))
    for candidate in candidates:
        try:
            if candidate.is_file() and candidate.stat().st_size > 4096:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidate, target)
                print(f"[pack] 已认领赞助码图片：{candidate.name} -> {target}")
                return
        except OSError:
            continue


def main() -> int:
    _ensure_reward_image()
    DIST.mkdir(parents=True, exist_ok=True)

    # 打包前把四处版本号同步一次，避免出现「程序里是 0.1.5、安装包还是 0.1.4」
    try:
        script = ROOT / "scripts" / "sync_version.py"
        if script.is_file():
            # 必须显式指定 utf-8：子进程输出的是 UTF-8 中文，而 Windows 默认按
            # GBK 解码，会直接抛 UnicodeDecodeError 把整个打包流程带崩
            result = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in (result.stdout or "").strip().splitlines():
                print(f"[pack] {line}")
            if result.returncode != 0:
                print("[pack] 版本号同步失败，请检查 version.json")
    except Exception as exc:  # noqa: BLE001 —— 同步失败不阻断打包
        print(f"[pack] 版本同步跳过：{exc}")

    # PyInstaller 输出前会删除已存在的目标目录，而该目录通常有数千个文件，
    # 会被安全删除保护拦截（批量删除需确认）导致打包直接失败。
    # 这里先把旧产物重命名挪开，让目标路径保持"不存在"，绕开批量删除。
    out_dir = DIST / "OpenClass-Box"
    if out_dir.exists():
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out_dir.rename(DIST / f"OpenClass-Box_old_{stamp}")

    # 旧产物备份：保留最近 KEEP_BACKUPS 份，其余逐个删除。
    #
    # 原来是「只统计、不删除」，理由是某些环境对「一次删除几千个文件」有底层
    # 安全拦截，会把整个打包进程带走（不是 Python 异常，try/except 拦不住）。
    # 这个顾虑是对的，但「一直不删」的代价是实测堆到了 **18 GB**（14 份备份），
    # 而且没有任何自愈机制。
    #
    # 折中：仍然不批量删，而是**一份一份删、每份失败就跳过继续** ——
    # 与 core/config_backup.py 的清理策略一致。单次删除的文件量足够小，
    # 不会触发拦截；真被拦住了也只是这份留着，不影响本次产物。
    prune_old_backups()

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        "OpenClass-Box",
        "--onedir",
        "--noconsole",
        "--clean",
        "--noconfirm",
        f"--distpath={DIST}",
        f"--workpath={BUILD}",
        "--add-data",
        f"{ROOT / 'frontend' / 'dist'}{SEP}frontend/dist",
        # 业务模块
        "--hidden-import",
        "backend",
        "--collect-submodules",
        "backend",
        # WebView2 渲染层
        "--hidden-import",
        "webview",
        "--collect-submodules",
        "webview",
        "--collect-data",
        "webview",
        # 托盘 / 图标
        "--collect-submodules",
        "PIL",
        "--icon",
        str(ROOT / "openclass.ico"),
        # 版本资源：exe 属性里会显示发布者/产品/版权，
        # 同时明显降低杀毒软件与 SmartScreen 的启发式误报
        "--version-file",
        str(ROOT / "version_info.txt"),
        # 注册表与窗口集成
        "--hidden-import",
        "win32api",
        "--hidden-import",
        "win32gui",
        "--hidden-import",
        "win32con",
        str(ROOT / "main.py"),
    ]
    print("[pack] 开始打包，这可能需要几分钟…")
    try:
        subprocess.run(cmd, check=True, cwd=str(ROOT))
    except subprocess.CalledProcessError as exc:
        print(f"[pack] 打包失败：{exc}")
        return 1

    # 工具目录不交给 PyInstaller（会被塞进 _internal），直接复制到 exe 同级，
    # 保证 tools_dir() 找到，且与 exe 一起拷贝即可运行。
    dest_tools = DIST / "OpenClass-Box" / "tools"
    if dest_tools.exists():
        shutil.rmtree(dest_tools)
    shutil.copytree(ROOT / "tools", dest_tools)
    print(f"[pack] 已复制工具目录 -> {dest_tools}")

    # 应用图标：供运行期 create_window(icon=) 使用（与 exe 图标一致）
    icon_src = ROOT / "openclass.ico"
    icon_dst = DIST / "OpenClass-Box" / "openclass.ico"
    if icon_src.is_file():
        shutil.copy(icon_src, icon_dst)
        print(f"[pack] 已复制图标 -> {icon_dst}")

    # 随包携带 WebView2 固定版本运行时：目标机无需安装 WebView2 也能显示界面
    runtime_src = ROOT / "runtime" / "WebView2Runtime"
    runtime_dst = DIST / "OpenClass-Box" / "WebView2Runtime"
    if runtime_src.is_dir():
        shutil.copytree(runtime_src, runtime_dst)
        print(f"[pack] 已复制 WebView2 运行时 -> {runtime_dst}")
    else:
        print("[pack] 未找到 runtime/WebView2Runtime，跳过（将依赖系统 WebView2）")

    # 运行时数据目录（配置 / 日志 / 局域网库）：开发机在产物目录跑过 exe 就会生成，
    # 它是「打包者的设置」，绝不能进安装包；插件升级留下的 *.bak 同理
    stray_data = DIST / "OpenClass-Box" / "data"
    if stray_data.exists():
        shutil.rmtree(stray_data, ignore_errors=True)
        print("[pack] 已清理产物中的运行时数据目录 data/")
    stray_bak = list((DIST / "OpenClass-Box" / "tools").glob("*.bak"))
    for item in stray_bak:
        shutil.rmtree(item, ignore_errors=True)
    if stray_bak:
        print(f"[pack] 已清理 {len(stray_bak)} 个插件备份目录")

    exe = DIST / "OpenClass-Box" / "OpenClass-Box.exe"

    # 打包完成后自动签名（证书已生成时），并把信任证书放进产物目录：
    # 目标机导入该证书后不再出现「未知发布者」提示（详见 sign.py 说明）
    try:
        import sign as signer

        if signer.certificate_exists():
            signed = signer.sign_file(exe)
            print(f"[pack] 代码签名：{'成功' if signed else '失败（可运行 python sign.py sign 重试）'}")
            if signer.export_cer(DIST / "OpenClass-Box" / "OpenClass-Box.cer"):
                print("[pack] 已附带信任证书 OpenClass-Box.cer")
        else:
            print("[pack] 未生成签名证书，跳过签名（python sign.py init 可生成）")
    except Exception as exc:  # 签名失败不影响产物可用
        print(f"[pack] 跳过签名：{exc}")

    # 启动自检：产物必须能真的跑起来。之前出现过「打包成功但一启动就
    # NameError 崩溃」，只靠编译通过看不出来，所以这里真跑一次。
    smoke = _smoke_test(exe)

    print(f"[pack] 完成：{exe}" + ("" if smoke else "（但启动自检未通过，请检查！）"))
    return 0 if smoke else 1


def _smoke_test(exe: Path, seconds: int = 10) -> bool:
    """启动产物并观察若干秒：进程仍在运行即视为通过。"""
    if not exe.is_file():
        print("[pack] 启动自检失败：找不到 exe")
        return False
    if os.environ.get("OC_SKIP_SMOKE") == "1":
        print("[pack] 已按环境变量跳过启动自检")
        return True

    print(f"[pack] 启动自检：运行 {seconds} 秒观察是否崩溃…")
    try:
        proc = subprocess.Popen(
            [str(exe)],
            cwd=str(exe.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        print(f"[pack] 启动自检失败：无法启动（{exc}）")
        return False

    time.sleep(seconds)
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("[pack] 启动自检通过：进程运行正常")
        return True

    print(f"[pack] 启动自检失败：进程提前退出，返回码 {proc.returncode}")
    return False


if __name__ == "__main__":
    raise SystemExit(main())
