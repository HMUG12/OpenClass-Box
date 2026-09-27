"""
手机 Web 控制台 —— 局域网内零安装的手机端运维页面。

场景：老师上课时投屏断了 / 机器卡了，不用等电教委员跑过来。手机连上教室
WiFi，浏览器打开 http://<一体机IP>:<端口> 就能：
  · 看实时状态（CPU / 内存 / 温度 / 网速）
  · 跑一次一键体检，异常项直接标红并给出建议
  · 远程触发一键修复（清理 DNS 缓存 / 临时文件 / 音频服务 / 重置网络）
  · 看安全检测统计

零安装：不需要 App、不需要注册、不连外网，纯局域网内可达。

安全设计（在局域网上开服务必须谨慎，这里做了五道限制）：
  1. **只服务局域网**：请求方必须是私有 / 回环地址，其他来源直接 403；
  2. **访问码**：6 位随机码显示在桌面端，手机首次访问需输入，通过后拿 token；
  3. **失败锁定**：同一 IP 连续 5 次输错就锁 60 秒，挡住暴力猜码；
  4. **动作白名单**：只暴露 repair.py 里已定义好的修复项，不提供任意命令执行；
  5. **token 只在内存**：程序重启即失效，需要重新输码。
"""
from __future__ import annotations

import ipaddress
import json
import os
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DEFAULT_PORT = 38610
MAX_FAILS = 5
LOCK_SECONDS = 60
HEALTH_CACHE_SECONDS = 30

_server: ThreadingHTTPServer | None = None
_port = 0
_code = ""
_tokens: set[str] = set()
_fails: dict[str, list[float]] = {}
_health_cache: dict[str, Any] = {"at": 0.0, "data": None}
_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════
# 访问控制
# ══════════════════════════════════════════════════════════════

def _new_code() -> str:
    return f"{secrets.randbelow(1000000):06d}"


def _is_local(host: str) -> bool:
    """只允许局域网 / 本机来源（公网来源一律拒绝）。"""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local)


def _allow_attempt(host: str) -> bool:
    """该 IP 当前是否还允许尝试输码（失败锁定）。"""
    with _lock:
        stamps = _fails.get(host)
        if not stamps:
            return True
        now = time.time()
        recent = [s for s in stamps if now - s < LOCK_SECONDS]
        _fails[host] = recent
        return len(recent) < MAX_FAILS


def _note_fail(host: str) -> None:
    with _lock:
        _fails.setdefault(host, []).append(time.time())


def _clear_fails(host: str) -> None:
    with _lock:
        _fails.pop(host, None)


def _authorized(token: str) -> bool:
    if not token:
        return False
    with _lock:
        return token in _tokens


# ══════════════════════════════════════════════════════════════
# 数据
# ══════════════════════════════════════════════════════════════

def _snapshot() -> dict[str, Any]:
    """手机端首页数据：实时指标 + 修复项 + 安全统计（全部本机实测）。"""
    from .hardware_detail import collect
    from .monitor import device_info, local_ip, monitor
    from .repair import list_repairs
    from .security import stats as security_stats

    metrics = monitor.metrics()
    hardware = monitor.hardware()
    try:
        quick = collect(quick=True)
    except Exception:
        quick = {}

    return {
        "now": round(time.time() * 1000),
        "device": {
            **device_info(),
            "ip": local_ip() or "",
            "bootTime": hardware.get("bootTime") or 0,
            "cpuName": (hardware.get("cpu") or {}).get("name") if isinstance(hardware.get("cpu"), dict) else "",
        },
        "cpu": {
            **(metrics.get("cpu") or {}),
            "temp": quick.get("cpuTemp"),
            "tempSource": quick.get("tempSource") or "",
        },
        "memory": metrics.get("memory") or {},
        "network": metrics.get("network") or {},
        "disk": metrics.get("disk") or {},
        "gpus": quick.get("gpus") or [],
        "repairs": list_repairs(),
        "security": security_stats(),
    }


def _health() -> dict[str, Any]:
    """一键体检（30 秒缓存，避免手机反复点把机器压满）。"""
    now = time.time()
    with _lock:
        cached = _health_cache["data"]
        if cached is not None and now - float(_health_cache["at"]) < HEALTH_CACHE_SECONDS:
            return {**cached, "cached": True}

    from .health import run_checks

    result = run_checks()
    with _lock:
        _health_cache["at"] = now
        _health_cache["data"] = result
    return {**result, "cached": False}


# ══════════════════════════════════════════════════════════════
# 页面（移动端单页，自包含，不引任何外部资源）
# ══════════════════════════════════════════════════════════════

_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0f1115">
<title>OpenClass-Box 控制台</title>
<style>
:root{--bg:#0f1115;--card:#171a21;--line:#262b35;--fg:#e8eaed;--dim:#98a0ad;
--ok:#3fb950;--warn:#d29922;--bad:#f85149;--accent:#4c8dff}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;padding:14px 13px 40px;background:var(--bg);color:var(--fg);
font:15px/1.6 -apple-system,"Segoe UI",system-ui,"Microsoft YaHei",sans-serif}
h1{font-size:17px;margin:0;font-weight:650}
.sub{color:var(--dim);font-size:12.5px;margin-top:3px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin-top:12px}
.row{display:flex;align-items:center;gap:14px}
.rings{display:flex;justify-content:space-around;text-align:center;margin-top:4px}
.ring{position:relative;width:92px;height:92px;border-radius:50%;display:grid;place-items:center;
background:conic-gradient(from -90deg,var(--c,var(--accent)) calc(var(--v,0)*1%),rgba(255,255,255,.08) 0)}
.ring::after{content:"";position:absolute;inset:9px;border-radius:50%;background:var(--card)}
.ring b{position:relative;z-index:1;font:650 19px/1 ui-monospace,SFMono-Regular,Menlo,monospace}
.ring small{position:relative;z-index:1;display:block;font-size:10.5px;color:var(--dim);margin-top:3px;font-weight:400}
.legend{font-size:12.5px;color:var(--dim);text-align:center;margin-top:8px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}
.kv{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.kv i{display:block;font-style:normal;color:var(--dim);font-size:12px}
.kv b{display:block;font:650 17px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;margin-top:2px}
button{width:100%;border:0;border-radius:10px;padding:13px;font-size:15px;font-weight:600;
background:var(--accent);color:#fff;margin-top:10px}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--fg)}
button:disabled{opacity:.55}
.item{display:flex;gap:10px;padding:9px 0;border-bottom:1px solid var(--line);font-size:13.5px}
.item:last-child{border-bottom:0}
.dot{width:8px;height:8px;border-radius:50%;background:var(--ok);flex:0 0 auto;margin-top:7px}
.dot.bad{background:var(--bad)}.dot.warn{background:var(--warn)}
.item div{flex:1;min-width:0}
.item em{display:block;font-style:normal;color:var(--dim);font-size:12.5px;margin-top:2px}
.hint{color:var(--dim);font-size:12.5px;margin-top:10px}
#login{position:fixed;inset:0;background:var(--bg);display:none;flex-direction:column;
justify-content:center;padding:26px;z-index:9}
#login.on{display:flex}
#login input{font:650 26px/1 ui-monospace,monospace;letter-spacing:.5em;text-align:center;
padding:16px;border-radius:12px;border:1px solid var(--line);background:var(--card);color:var(--fg);width:100%}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11.5px;
background:rgba(63,185,80,.15);color:var(--ok)}
</style>
</head>
<body>
<h1 id="host">OpenClass-Box</h1>
<div class="sub" id="addr">正在连接…</div>

<div class="card" id="live" style="display:none">
  <div class="rings">
    <div><div class="ring" id="r-cpu" style="--v:0"><b id="v-cpu">–</b><small>CPU</small></div></div>
    <div><div class="ring" id="r-mem" style="--v:0"><b id="v-mem">–</b><small>内存</small></div></div>
    <div><div class="ring" id="r-temp" style="--v:0"><b id="v-temp">–</b><small>温度</small></div></div>
  </div>
  <div class="legend" id="legend">实时数据每 5 秒刷新</div>
  <div class="grid">
    <div class="kv"><i>下载</i><b id="v-dl">–</b></div>
    <div class="kv"><i>上传</i><b id="v-ul">–</b></div>
  </div>
</div>

<div class="card">
  <b style="font-size:14px">一键体检</b>
  <button id="btn-health">开始体检</button>
  <div id="health"></div>
</div>

<div class="card">
  <b style="font-size:14px">远程修复</b>
  <div class="hint">修复动作需要在这台机器上确认权限（部分会弹系统框）</div>
  <div id="repairs"></div>
</div>

<div class="card">
  <b style="font-size:14px">安全检测</b>
  <div id="security" class="hint">加载中…</div>
</div>

<div class="hint" style="text-align:center">仅在同一局域网内可访问 · OpenClass-Box</div>

<div id="login">
  <div style="text-align:center">
    <div style="font-size:19px;font-weight:650">输入访问码</div>
    <div class="sub" style="margin:6px 0 18px">访问码显示在电脑端的「设置 → 手机控制台」</div>
    <input id="code" inputmode="numeric" maxlength="6" placeholder="······">
    <button id="btn-auth">进入</button>
    <div class="hint" id="auth-msg"></div>
  </div>
</div>

<script>
var T = localStorage.getItem('oc_token') || '';
function $(id){return document.getElementById(id)}
function fmtRate(b){ if(!b&&b!==0) return '–'; var u=['B/s','KB/s','MB/s','GB/s'],i=0,v=b;
  while(v>=1024&&i<u.length-1){v/=1024;i++} return (v>=10||i===0?v.toFixed(0):v.toFixed(1))+' '+u[i] }
function fmtUp(s){ if(!s) return '–'; var d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);
  return d? d+' 天 '+h+' 小时' : (h? h+' 小时 '+m+' 分' : m+' 分钟') }
function setRing(id,val,label){
  var el=$(id); if(!el) return;
  if(val===null||val===undefined||isNaN(val)){ el.style.setProperty('--v',0); $(label).textContent='–'; return }
  var v=Math.max(0,Math.min(100,Math.round(val)));
  el.style.setProperty('--v',v);
  el.style.setProperty('--c', v>=85?'var(--bad)':(v>=65?'var(--warn)':'var(--accent)'));
  $(label).textContent=v+'%';
}
function showLogin(){ $('login').classList.add('on') }
async function auth(){
  var code=$('code').value.trim();
  if(code.length!==6){ $('auth-msg').textContent='请输入 6 位访问码'; return }
  $('btn-auth').disabled=true; $('auth-msg').textContent='校验中…';
  try{
    var r=await fetch('/api/auth',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:code})});
    var d=await r.json();
    if(d.ok){ T=d.token; localStorage.setItem('oc_token',T); $('login').classList.remove('on'); $('auth-msg').textContent=''; load() }
    else { $('auth-msg').textContent=d.message||'访问码不正确'; $('btn-auth').disabled=false }
  }catch(e){ $('auth-msg').textContent='连接失败：'+e; $('btn-auth').disabled=false }
}
async function load(){
  try{
    var r=await fetch('/api/status?token='+encodeURIComponent(T));
    if(r.status===401){ T=''; localStorage.removeItem('oc_token'); showLogin(); return }
    var d=await r.json();
    $('live').style.display='block';
    $('host').textContent=d.device.hostname||d.device.model||'OpenClass-Box';
    var bits=[d.device.ip?('IP '+d.device.ip):'', d.device.cpuName||'', '已运行 '+fmtUp(Math.floor(Date.now()/1000-d.device.bootTime))];
    $('addr').innerHTML='<span class="pill">在线</span> '+bits.filter(Boolean).join(' · ');
    setRing('r-cpu',d.cpu.percent,'v-cpu');
    setRing('r-mem',d.memory.percent,'v-mem');
    if(d.cpu.temp!==null&&d.cpu.temp!==undefined) setRing('r-temp',Math.min(100,d.cpu.temp/95*100),'v-temp'), $('legend').textContent='温度 '+d.cpu.temp+'°C（'+(d.cpu.tempSource||'实测')+'）· 每 5 秒刷新';
    else { setRing('r-temp',null,'v-temp'); $('legend').textContent='本机未提供温度传感器 · 每 5 秒刷新' }
    $('v-dl').textContent=fmtRate(d.network.download);
    $('v-ul').textContent=fmtRate(d.network.upload);
    $('security').textContent='累计检测 '+d.security.total+' 条 · 今日 '+d.security.today+
      ' 条 · 风险 '+d.security.riskTotal+' 条 · 白名单 '+d.security.whitelistCount+' 个';
    if(!$('repairs').dataset.done){
      var html='';
      (d.repairs||[]).forEach(function(it){
        html+='<button class="ghost" data-key="'+it.key+'">'+it.name+(it.admin?'（需管理员）':'')+'</button>';
      });
      $('repairs').innerHTML=html;
      $('repairs').dataset.done='1';
      $('repairs').querySelectorAll('button').forEach(function(b){
        b.onclick=function(){ repair(b.dataset.key, b.textContent, b) };
      });
    }
  }catch(e){ $('addr').textContent='连接已断开，正在重试…' }
}
async function repair(key,name,btn){
  if(!confirm('确定在电脑上执行「'+name+'」吗？')) return;
  btn.disabled=true; var old=btn.textContent; btn.textContent='执行中…';
  try{
    var r=await fetch('/api/repair',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({token:T,key:key})});
    var d=await r.json();
    alert(d.message||(d.ok?'执行完成':'执行失败'));
  }catch(e){ alert('执行失败：'+e) }
  btn.disabled=false; btn.textContent=old;
}
async function health(){
  var btn=$('btn-health'); btn.disabled=true; btn.textContent='体检中…';
  try{
    var r=await fetch('/api/health?token='+encodeURIComponent(T));
    if(r.status===401){ T=''; localStorage.removeItem('oc_token'); showLogin(); return }
    var d=await r.json();
    var html='';
    (d.items||[]).forEach(function(it){
      html+='<div class="item"><span class="dot'+(it.ok?'':' bad')+'"></span><div>'+
        '<b>'+it.name+'</b><em>'+it.detail+(it.suggest?' —— '+it.suggest:'')+'</em></div></div>';
    });
    html+='<div class="hint">'+(d.healthy? '全部通过（'+d.total+' 项）' : '通过 '+d.okCount+'/'+d.total+' 项'+(d.cached?' · 30 秒内缓存':''))+'</div>';
    $('health').innerHTML=html;
  }catch(e){ $('health').innerHTML='<div class="hint">体检失败：'+e+'</div>' }
  btn.disabled=false; btn.textContent='重新体检';
}
$('btn-auth').onclick=auth;
$('code').addEventListener('keydown',function(e){ if(e.key==='Enter') auth() });
$('btn-health').onclick=health;
load();
setInterval(load,5000);
</script>
</body>
</html>
"""


# ══════════════════════════════════════════════════════════════
# HTTP
# ══════════════════════════════════════════════════════════════

def _make_handler():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "OpenClassBox-Web/1.0"

        def log_message(self, *_args: Any) -> None:
            pass

        # ── 基础 ────────────────────────────────────────

        def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: Any, status: int = 200) -> None:
            self._send(
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
                status,
            )

        def _body(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                return {}

        def _guard(self) -> bool:
            """只允许局域网来源。"""
            if _is_local(self.client_address[0]):
                return True
            self._json({"ok": False, "message": "仅允许局域网访问"}, 403)
            return False

        # ── GET ─────────────────────────────────────────

        def do_GET(self) -> None:  # noqa: N802
            if not self._guard():
                return
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            token = (query.get("token") or [""])[0]

            if parsed.path in ("/", "/index.html"):
                self._send(_PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return

            if parsed.path == "/api/status":
                if not _authorized(token):
                    self._json({"ok": False, "message": "未授权"}, 401)
                    return
                try:
                    self._json(_snapshot())
                except Exception as exc:
                    self._json({"ok": False, "message": f"采集失败：{exc}"}, 500)
                return

            if parsed.path == "/api/health":
                if not _authorized(token):
                    self._json({"ok": False, "message": "未授权"}, 401)
                    return
                try:
                    self._json(_health())
                except Exception as exc:
                    self._json({"ok": False, "message": f"体检失败：{exc}"}, 500)
                return

            self._json({"ok": False, "message": "未找到"}, 404)

        # ── POST ────────────────────────────────────────

        def do_POST(self) -> None:  # noqa: N802
            if not self._guard():
                return
            parsed = urllib.parse.urlparse(self.path)
            host = self.client_address[0]

            if parsed.path == "/api/auth":
                if not _allow_attempt(host):
                    self._json({"ok": False, "message": "尝试次数过多，请 1 分钟后再试"}, 429)
                    return
                data = self._body()
                if str(data.get("code") or "") == _code:
                    _clear_fails(host)
                    token = secrets.token_urlsafe(24)
                    with _lock:
                        _tokens.add(token)
                    self._json({"ok": True, "token": token, "message": ""})
                else:
                    _note_fail(host)
                    self._json({"ok": False, "message": "访问码不正确"}, 401)
                return

            if parsed.path == "/api/repair":
                data = self._body()
                if not _authorized(str(data.get("token") or "")):
                    self._json({"ok": False, "message": "未授权"}, 401)
                    return
                key = str(data.get("key") or "")
                try:
                    from .repair import run_repair

                    self._json(run_repair(key))
                except Exception as exc:
                    self._json({"ok": False, "message": f"执行失败：{exc}"}, 500)
                return

            self._json({"ok": False, "message": "未找到"}, 404)

    return Handler


# ══════════════════════════════════════════════════════════════
# 生命周期
# ══════════════════════════════════════════════════════════════

def start(port: int = DEFAULT_PORT) -> dict[str, Any]:
    """启动手机控制台服务（监听 0.0.0.0，但只响应局域网来源）。"""
    global _server, _port, _code
    with _lock:
        if _server is not None:
            return {"ok": True, "message": "控制台已在运行", "port": _port, "code": _code}

    if not _code:
        _code = _new_code()

    handler = _make_handler()
    httpd: ThreadingHTTPServer | None = None
    chosen = int(port)
    for candidate in (int(port), 0):
        try:
            httpd = ThreadingHTTPServer(("0.0.0.0", candidate), handler)
            chosen = httpd.server_address[1]
            break
        except OSError:
            httpd = None
    if httpd is None:
        return {"ok": False, "message": f"端口 {port} 无法监听（可能被占用）"}

    httpd.daemon_threads = True
    with _lock:
        _server = httpd
        _port = chosen
    threading.Thread(target=httpd.serve_forever, daemon=True, name="oc-webconsole").start()
    return {"ok": True, "message": "手机控制台已启动", "port": chosen, "code": _code}


def stop() -> dict[str, Any]:
    global _server, _port
    with _lock:
        httpd = _server
        _server = None
        _port = 0
        _tokens.clear()
    if httpd is None:
        return {"ok": True, "message": "控制台未在运行"}
    try:
        httpd.shutdown()
        httpd.server_close()
    except Exception:
        pass
    return {"ok": True, "message": "手机控制台已停止"}


def regenerate() -> str:
    """换一个访问码（旧 token 全部失效）。"""
    global _code
    with _lock:
        _code = _new_code()
        _tokens.clear()
    return _code


def status() -> dict[str, Any]:
    from .monitor import local_ip


    with _lock:
        running = _server is not None
        port = _port
        code = _code
        clients = len(_tokens)
    ip = local_ip() or ""
    return {
        "running": running,
        "port": port,
        "code": code if running else "",
        "ip": ip,
        "url": f"http://{ip}:{port}" if (running and ip) else "",
        "clients": clients,
        "portDefault": DEFAULT_PORT,
    }


# ══════════════════════════════════════════════════════════════
# 防火墙放行
#
# 手机连不上最常见的原因不是服务没开，而是 **Windows 防火墙默认拦截入站连接**：
# 本机能打开 127.0.0.1，局域网里的手机却被挡在门外。这里做两件事：
#   1. 检测是否已有放行规则（不给用户猜）；
#   2. 一键添加规则（需要管理员，弹 UAC 确认）。
# 规则只对「专用 / 域」网络放行，公共网络（咖啡厅 WiFi）依然关闭，避免误暴露。
# ══════════════════════════════════════════════════════════════

_FIREWALL_RULE = "OpenClass-Box 手机控制台"


def _run(args: list[str], timeout: float = 12.0) -> str:
    import subprocess

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return ""
    for encoding in ("gbk", "utf-8", "latin-1"):
        try:
            return proc.stdout.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def firewall_status() -> dict[str, Any]:
    """检查入站放行规则是否存在。"""
    if os.name != "nt":
        return {"supported": False, "allowed": True, "message": "非 Windows 系统无需放行"}
    output = _run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={_FIREWALL_RULE}"])
    allowed = "OpenClass-Box" in output
    return {
        "supported": True,
        "allowed": allowed,
        "rule": _FIREWALL_RULE,
        "message": "已放行" if allowed else "未放行：手机可能连不上（防火墙默认拦截入站）",
    }


def allow_firewall() -> dict[str, Any]:
    """添加防火墙放行规则（管理员权限，弹 UAC）。"""
    if os.name != "nt":
        return {"ok": False, "message": "仅支持 Windows"}
    port = _port or DEFAULT_PORT
    command = (
        f'netsh advfirewall firewall delete rule name="{_FIREWALL_RULE}" >nul 2>nul & '
        f'netsh advfirewall firewall add rule name="{_FIREWALL_RULE}" dir=in action=allow '
        f"protocol=TCP localport={port} profile=private,domain"
    )
    try:
        import ctypes

        result = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            None, "runas", "cmd.exe", f"/c {command}", None, 0
        )
    except Exception as exc:
        return {"ok": False, "message": f"提权失败：{exc}"}
    if result <= 32:
        return {"ok": False, "message": "已取消或提权失败（需要管理员同意）"}
    return {
        "ok": True,
        "message": f"已请求放行 TCP {port}（仅专用/域网络）。请在系统弹窗中确认后刷新状态。",
    }
