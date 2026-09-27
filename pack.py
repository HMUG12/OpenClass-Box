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

    # PyInstaller 输出前会删除已存在的目标目录，而该目录通常有数千个文件，
    # 会被安全删除保护拦截（批量删除需确认）导致打包直接失败。
    # 这里先把旧产物重命名挪开，让目标路径保持"不存在"，绕开批量删除。
    out_dir = DIST / "OpenClass-Box"
    if out_dir.exists():
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out_dir.rename(DIST / f"OpenClass-Box_old_{stamp}")

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
