"""
内网穿透套件 —— 把本机端口映射到公网，供远程管理与 Web 端访问。

教室一体机通常没有公网 IP，甚至不在同一网段；要让 A 端的管理端在教室外
也能打开，就需要一条「穿透」通道。这里不重复造轮子，而是把成熟开源工具
管起来：

  · cloudflared —— Cloudflare Tunnel：免费、自带 HTTPS（有域名即可）；
  · frp         —— 自建 frps 服务器：可控性最强，内网穿透最常见做法；
  · ngrok       —— 开箱即用，免费版给随机域名；
  · 自定义       —— 任何命令行工具，命令模板里用 {port} 占位。

统一能力：选方案 → 填参数 → 一键启动 → **自动从输出里抓出公网地址** →
复制到手机/浏览器即可管理；随时停止；参数本地保存（含令牌，只写在数据目录）。

安全提示（界面与文档都会写明）：
  穿透之后管理端就暴露在公网了 —— 必须在「远程管理」里设置足够长的访问码，
  并确认是否真的需要「允许公网来源」。默认只允许私有地址来源。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .paths import app_root

try:
    from .config import config
except ImportError:  # pragma: no cover - 极端导入顺序兜底
    config = None

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 读取进程输出的线程上限（防止某些工具刷屏占内存）
_MAX_OUTPUT_LINES = 400

PROVIDERS: dict[str, dict[str, Any]] = {
    "cloudflared": {
        "name": "Cloudflare Tunnel",
        "desc": "免费、自带 HTTPS。需要一个托管在 Cloudflare 的域名；快速隧道可直接拿到临时域名。",
        "exe": ["tools/tunnel/cloudflared.exe", "tools/cloudflared/cloudflared.exe"],
        "urlHint": "在上方填「隧道令牌」可固定域名；留空则使用临时域名（重启会变）。",
        "fields": [
            {"key": "token", "label": "隧道令牌（可留空）", "secret": True, "placeholder": "eyJhIjoi..."},
        ],
    },
    "frp": {
        "name": "frp（自建服务器）",
        "desc": "需要一台有公网 IP 的服务器运行 frps。控制力最强，适合学校自有服务器。",
        "exe": ["tools/tunnel/frpc.exe", "tools/frp/frpc.exe"],
        "urlHint": "公网地址按「服务器地址 + 远程端口」拼出，无需从输出抓取。",
        "fields": [
            {"key": "host", "label": "服务器地址", "placeholder": "frp.example.com"},
            {"key": "serverPort", "label": "服务端口", "placeholder": "7000"},
            {"key": "remotePort", "label": "远程端口", "placeholder": "38630"},
            {"key": "token", "label": "令牌（可留空）", "secret": True},
        ],
    },
    "ngrok": {
        "name": "ngrok",
        "desc": "开箱即用，免费版分配随机域名；需要在官网获取 authtoken。",
        "exe": ["tools/tunnel/ngrok.exe", "tools/ngrok/ngrok.exe"],
        "urlHint": "免费版每次重启域名都会变，请以启动后显示的地址为准。",
        "fields": [
            {"key": "token", "label": "authtoken", "secret": True},
            {"key": "region", "label": "区域（可留空）", "placeholder": "ap"},
        ],
    },
    "custom": {
        "name": "自定义命令",
        "desc": "任意穿透工具。命令里用 {port} 占位本地端口；从输出里自动抓第一个 http(s) 地址。",
        "exe": [],
        "urlHint": "把工具放到 tools/tunnel/ 下，然后在「命令」里写完整命令行。",
        "fields": [
            {"key": "command", "label": "命令（含 {port}）", "placeholder": r'"tools\tunnel\mytool.exe" --local {port}'},
        ],
    },
}

_URL_RE = re.compile(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")

_proc: subprocess.Popen | None = None
_proc_started = 0.0
_provider = ""
_url = ""
_output: list[str] = []
_message = ""
_lock = threading.RLock()


# ══════════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════════

def _settings() -> dict[str, Any]:
    if config is None:
        return {}
    data = config.get("tunnel_settings", {})
    return data if isinstance(data, dict) else {}


def settings() -> dict[str, Any]:
    """当前配置（令牌不回显明文，只给「是否已填」）。"""
    data = _settings()
    safe: dict[str, Any] = {"provider": data.get("provider") or "cloudflared"}
    for key, value in (data.get("fields") or {}).items():
        provider = PROVIDERS.get(safe["provider"], {})
        secret = any(
            field.get("key") == key and field.get("secret") for field in provider.get("fields", [])
        )
        safe[key] = "******" if (secret and value) else (value or "")
    return safe


def save_settings(provider: str, fields: dict[str, Any] | None = None) -> dict[str, Any]:
    """保存方案与参数（令牌等原样存本地，不回显）。"""
    provider = str(provider or "").strip().lower()
    if provider not in PROVIDERS:
        return {"ok": False, "message": f"未知的穿透方案：{provider}"}

    current = dict(_settings())
    merged = dict(current.get("fields") or {})
    for key, value in (fields or {}).items():
        if value == "******":       # 界面回显的占位符：不改动已保存的值
            continue
        merged[str(key)] = str(value or "")
    data = {"provider": provider, "fields": merged}

    if config is None or not config.set("tunnel_settings", data):
        return {"ok": False, "message": "保存失败（数据目录不可写）"}
    return {"ok": True, "message": "已保存", "settings": settings()}


# ══════════════════════════════════════════════════════════════
# 可执行文件定位
# ══════════════════════════════════════════════════════════════

def resolve_exe(provider: str) -> Path | None:
    """在约定目录里找命令对应的可执行文件。"""
    spec = PROVIDERS.get(provider)
    if not spec:
        return None
    for relative in spec.get("exe", []):
        candidate = app_root() / relative
        if candidate.is_file():
            return candidate
    # 兜底：PATH 里找（如全局安装的 cloudflared / ngrok）
    name = Path(spec.get("exe", ["x.exe"])[0]).name if spec.get("exe") else ""
    found = shutil.which(name) if name else None
    return Path(found) if found else None


# ══════════════════════════════════════════════════════════════
# 命令行构造（纯函数，便于在没有网络的开发机上直接断言）
# ══════════════════════════════════════════════════════════════

def build_command(provider: str, port: int, fields: dict[str, Any] | None = None) -> list[str]:
    """构造穿透命令。不启动任何进程，方便测试。"""
    provider = (provider or "").strip().lower()
    if provider not in PROVIDERS:
        raise ValueError(f"未知的穿透方案：{provider}")
    values = dict(fields or {})
    port = int(port)

    if provider == "cloudflared":
        exe = resolve_exe("cloudflared")
        if exe is None:
            raise FileNotFoundError("没有找到 cloudflared.exe（放到 tools/tunnel/ 或加入 PATH）")
        args = [str(exe), "tunnel"]
        token = str(values.get("token") or "").strip()
        if token:
            args += ["run", "--token", token]
        else:
            args += ["--url", f"http://127.0.0.1:{port}", "--no-autoupdate"]
        return args

    if provider == "frp":
        exe = resolve_exe("frp")
        if exe is None:
            raise FileNotFoundError("没有找到 frpc.exe（放到 tools/tunnel/ 或加入 PATH）")
        host = str(values.get("host") or "").strip()
        if not host:
            raise ValueError("请先填 frps 服务器地址")
        ini = frp_config_text(
            host=host,
            server_port=str(values.get("serverPort") or "7000"),
            token=str(values.get("token") or ""),
            local_port=port,
            remote_port=str(values.get("remotePort") or port),
        )
        path = _frp_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(ini, encoding="utf-8")
        return [str(exe), "-c", str(path)]

    if provider == "ngrok":
        exe = resolve_exe("ngrok")
        if exe is None:
            raise FileNotFoundError("没有找到 ngrok.exe（放到 tools/tunnel/ 或加入 PATH）")
        args = [str(exe), "http", str(port), "--log", "stdout"]
        token = str(values.get("token") or "").strip()
        if token:
            args += ["--authtoken", token]
        region = str(values.get("region") or "").strip()
        if region:
            args += ["--region", region]
        return args

    # 自定义：命令模板里 {port} 占位
    template = str(values.get("command") or "").strip()
    if not template:
        raise ValueError("请填写自定义命令")
    return split_command(template.replace("{port}", str(port)))


def split_command(text: str) -> list[str]:
    """把命令行文本拆成参数列表（支持双引号包裹的含空格路径）。

    自己实现而不是用 shlex：Windows 路径里的反斜杠在 shlex 下会被当转义符，
    这里只处理双引号，行为更可预期。
    """
    parts: list[str] = []
    current = ""
    in_quotes = False
    for char in text:
        if char == '"':
            in_quotes = not in_quotes
            continue
        if char.isspace() and not in_quotes:
            if current:
                parts.append(current)
                current = ""
            continue
        current += char
    if current:
        parts.append(current)
    if not parts:
        raise ValueError("命令为空")
    return parts


def _frp_config_path() -> Path:
    from .paths import config_dir

    return config_dir() / "tunnel" / "frpc.ini"


def frp_config_text(
    host: str, server_port: str, token: str, local_port: int, remote_port: str
) -> str:
    """frpc 的 ini 配置（纯文本，单独出来方便测试）。"""
    lines = [
        "[common]",
        f"server_addr = {host}",
        f"server_port = {server_port}",
    ]
    if token:
        lines.append(f"token = {token}")
    lines += [
        "",
        "[openclass-admin]",
        "type = tcp",
        "local_ip = 127.0.0.1",
        f"local_port = {local_port}",
        f"remote_port = {remote_port}",
    ]
    return "\n".join(lines) + "\n"


# ══════════════════════════════════════════════════════════════
# 启动 / 停止 / 状态
# ══════════════════════════════════════════════════════════════

def _reader(proc: subprocess.Popen) -> None:
    """持续读取输出：抓公网地址、留最近若干行供界面显示。"""
    global _url, _message
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").rstrip()
        if not line:
            continue
        with _lock:
            _output.append(line)
            if len(_output) > _MAX_OUTPUT_LINES:
                del _output[: len(_output) - _MAX_OUTPUT_LINES]
        if not _url:
            for match in _URL_RE.finditer(line):
                candidate = match.group(0).rstrip(".,)")
                if "127.0.0.1" in candidate or "localhost" in candidate:
                    continue
                with _lock:
                    _url = candidate
                break
        if "error" in line.lower() or "failed" in line.lower():
            with _lock:
                _message = line[:200]
    with _lock:
        if not _message:
            code = proc.poll()
            if code not in (None, 0):
                _message = f"穿透进程已退出（退出码 {code}），请检查参数或查看输出"


def start(port: int, provider: str = "", fields: dict[str, Any] | None = None) -> dict[str, Any]:
    """启动穿透。port 是要暴露的本地端口（一般是远程管理端口）。"""
    global _proc, _proc_started, _provider, _url, _output, _message

    with _lock:
        if _proc is not None and _proc.poll() is None:
            return {"ok": False, "message": "穿透已在运行，请先停止"}

    data = _settings()
    provider = (provider or data.get("provider") or "cloudflared").strip().lower()
    values = dict(data.get("fields") or {})
    if fields:
        values.update(fields)

    try:
        command = build_command(provider, port, values)
    except (ValueError, FileNotFoundError) as exc:
        return {"ok": False, "message": str(exc)}

    try:
        proc = subprocess.Popen(
            command,
            cwd=str(app_root()),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=_CREATE_NO_WINDOW,
        )
    except OSError as exc:
        return {"ok": False, "message": f"启动失败：{exc}"}

    with _lock:
        _proc = proc
        _proc_started = time.time()
        _provider = provider
        _url = ""
        _output = []
        _message = ""

    threading.Thread(target=_reader, args=(proc,), daemon=True, name="oc-tunnel-read").start()

    # frp 的地址可以直接拼（不必等输出）
    if provider == "frp":
        host = str(values.get("host") or "").strip()
        remote_port = str(values.get("remotePort") or port)
        if host:
            with _lock:
                _url = f"http://{host}:{remote_port}"

    return {"ok": True, "message": f"已启动 {PROVIDERS[provider]['name']}", "command": command}


def stop() -> dict[str, Any]:
    """停止穿透进程。"""
    global _proc, _url, _message
    with _lock:
        proc = _proc
        _proc = None
    if proc is None or proc.poll() is not None:
        return {"ok": True, "message": "穿透未在运行"}
    try:
        proc.terminate()
    except OSError:
        pass
    for _ in range(20):
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
    with _lock:
        _url = ""
        _message = ""
    return {"ok": True, "message": "已停止穿透"}


def status() -> dict[str, Any]:
    """穿透状态：是否运行、方案、公网地址、可执行文件是否就绪。"""
    with _lock:
        proc = _proc
        url = _url
        provider = _provider or str(_settings().get("provider") or "cloudflared")
        output = list(_output[-40:])
        message = _message
        started = _proc_started
    running = bool(proc is not None and proc.poll() is None)
    return {
        "running": running,
        "provider": provider,
        "providerName": PROVIDERS.get(provider, {}).get("name", provider),
        "url": url if running else "",
        "exeReady": resolve_exe(provider) is not None,
        "startedAt": started if running else 0,
        "output": output,
        "message": message,
        "settings": settings(),
        "providers": [
            {
                "id": key,
                "name": value["name"],
                "desc": value["desc"],
                "urlHint": value.get("urlHint", ""),
                "fields": value.get("fields", []),
                "exeReady": resolve_exe(key) is not None,
            }
            for key, value in PROVIDERS.items()
        ],
    }
