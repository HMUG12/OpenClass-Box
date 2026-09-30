"""
统一网络访问 —— 代理、超时、失败原因只在这里处理一次。

教室一体机常见三种网络环境：
  1. 直连外网；
  2. **必须走代理**（校园网出口代理 / 用户自建代理）；
  3. 完全离线，只有校园内网。

三种情况下界面必须能区分「本机没网」「目标站点不可达」「代理没配」，
否则一律显示「联网失败」，老师根本不知道该找谁。

因此：
  · 代理来源统一取 `urllib.request.getproxies()` —— 在 Windows 上它会读
    注册表里的 Internet 设置（也就是系统代理），环境变量也一并覆盖；
  · 失败原因翻译成人话（DNS / 超时 / TLS / 拒绝连接）；
  · 需要联网的地方统一走 http_get / http_get_json。
"""
from __future__ import annotations

import json
import socket
import ssl
import urllib.error
import urllib.request
from typing import Any

DEFAULT_TIMEOUT = 6.0
USER_AGENT = "OpenClass-Box"


def proxies() -> dict[str, str]:
    """系统代理（环境变量优先，Windows 上再读注册表 IE 设置）。"""
    try:
        found = urllib.request.getproxies()
    except Exception:
        found = {}
    return {k: str(v) for k, v in (found or {}).items() if v}


def proxy_note() -> str:
    """给界面用的一句话代理说明（没配代理则空串）。"""
    found = proxies()
    if not found:
        return ""
    address = found.get("https") or found.get("http") or ""
    return f"当前走系统代理 {address}" if address else ""


def classify_error(exc: BaseException) -> str:
    """把网络异常翻译成可读原因（供各处复用）。"""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 403:
            return "目标返回 403（可能被网络策略拦截）"
        if exc.code == 404:
            return "目标不存在（HTTP 404）"
        if exc.code >= 500:
            return f"目标服务异常（HTTP {exc.code}）"
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, socket.timeout):
            return "连接超时（网络慢或被拦截）"
        if isinstance(reason, ssl.SSLError):
            return "TLS 证书校验失败（部分校园网会替换证书）"
        text = str(reason)
        lowered = text.lower()
        if "getaddrinfo" in text or "name or service not known" in lowered:
            return "域名解析失败（DNS 异常）"
        if "refused" in lowered:
            return "目标拒绝连接"
        if "timed out" in lowered:
            return "连接超时"
        return f"连接失败：{text[:120]}"
    if isinstance(exc, socket.timeout):
        return "连接超时"
    return f"请求失败：{type(exc).__name__}"


def opener(use_proxy: bool = True) -> urllib.request.OpenerDirector:
    """构造 opener：按需带系统代理。"""
    found = proxies() if use_proxy else {}
    return urllib.request.build_opener(urllib.request.ProxyHandler(found))


def http_get(
    url: str,
    timeout: float = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    use_proxy: bool = True,
) -> tuple[bool, bytes, str]:
    """GET 请求。返回 (成功, 内容, 失败原因)。任何异常都不外抛。"""
    request = urllib.request.Request(url, method="GET")
    request.add_header("User-Agent", USER_AGENT)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with opener(use_proxy).open(request, timeout=timeout) as response:
            return True, response.read(), ""
    except Exception as exc:  # noqa: BLE001 —— 统一转成可读原因
        return False, b"", classify_error(exc)


def http_get_json(
    url: str,
    timeout: float = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    use_proxy: bool = True,
) -> tuple[bool, Any, str]:
    """GET 并解析 JSON。返回 (成功, 数据, 失败原因)。"""
    ok, raw, error = http_get(url, timeout, headers, use_proxy)
    if not ok:
        return False, None, error
    try:
        return True, json.loads(raw.decode("utf-8", "replace")), ""
    except ValueError:
        return False, None, "返回内容不是有效 JSON"


def local_ipv4() -> list[str]:
    """本机活动网卡的 IPv4（判断「本机有没有网络」用）。"""
    addresses: list[str] = []
    try:
        import psutil

        for name, stat in psutil.net_if_stats().items():
            if not stat.isup:
                continue
            for addr in psutil.net_if_addrs().get(name, []):
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    addresses.append(addr.address)
    except Exception:
        pass
    if not addresses:
        try:
            host = socket.gethostname()
            addresses = list({info[4][0] for info in socket.getaddrinfo(host, None)})
        except OSError:
            addresses = []
    return addresses


def dns_ok(host: str = "www.baidu.com") -> bool:
    try:
        socket.getaddrinfo(host, 443, socket.AF_INET)
        return True
    except OSError:
        return False
