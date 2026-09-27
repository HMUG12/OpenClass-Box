"""
网址安全检测 —— 两段式评分：**本地秒级判定** + **按需联网深查**。

为什么改两段式（原来的问题）：
  旧实现每次检测都要抓页面（6 秒超时）再查 RDAP（6 秒超时），剪贴板里
  每粘一个网址就可能卡十几秒 —— 又慢又让人分不清到底有没有风险。
  现在：
    · **本地规则**（域名仿冒 / IP / 端口 / 可疑后缀 / 混淆字符 / 敏感词）
      纯计算、毫秒级，立刻给出结论与依据；
    · **深度规则**（页面 ICP 备案、跨域下载、RDAP 域名年龄）只在需要时
      联网，并且严格限时，失败会**明确说明"未完成该项校验"**，
      而不是默默当作没问题。

判定阈值（依据都写在 reasons 里，用户能看到"为什么"）：
  · 本地分 ≥ 45 或深度分叠加后 ≥ 45 → 可疑（warn）
  · 总分 ≥ 80                          → 高危（danger）

结果带 30 分钟缓存：同一个网址反复检测不再重复联网。
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from .updater import _system_proxy

_TIMEOUT = 3.5            # 联网查询超时（秒）—— 宁可查不到，也不拖住用户
_CACHE_TTL = 1800.0       # 检测结果缓存
_CACHE_MAX = 500
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) OpenClass-Box"}

WARN_SCORE = 45
DANGER_SCORE = 80

# 高频品牌词（用于仿冒检测）
BRANDS = [
    "google", "microsoft", "apple", "amazon", "facebook", "paypal",
    "alipay", "taobao", "tmall", "jd", "qq", "wechat", "weixin",
    "baidu", "bilibili", "zhihu", "sina", "sohu", "163", "126",
    "icbc", "ccb", "abchina", "bankcomm", "alibaba", "huawei",
    "xiaomi", "lenovo", "dell", "adobe", "oracle", "nvidia",
]

# 常见形近/替换手法
LOOKALIKE = {"0": "o", "1": "l", "3": "e", "5": "s", "8": "b", "$": "s", "vv": "w", "rn": "m"}

# 高风险顶级域（价格低、注册门槛低，钓鱼站点大量集中在这里）
RISKY_TLDS = {
    "top", "xyz", "tk", "ml", "ga", "cf", "gq", "work", "click", "link",
    "loan", "buzz", "cyber", "rest", "makeup", "mom", "lol", "icu", "fit",
}

# 钓鱼常用路径关键词（配合非知名域名时加分）
PHISH_WORDS = (
    "login", "signin", "verify", "account", "password", "update", "secure",
    "pay", "wallet", "bank", "验证", "登录", "支付", "中奖", "领取",
)

# 公认安全域名（直接放行，不联网 —— 白名单短路，最常见的提速来源）
TRUSTED = {
    "baidu.com", "qq.com", "weixin.qq.com", "taobao.com", "tmall.com",
    "jd.com", "bilibili.com", "zhihu.com", "sina.com.cn", "sohu.com",
    "163.com", "gov.cn", "edu.cn", "microsoft.com", "apple.com",
    "google.com", "github.com", "aliyun.com", "qq.com.cn", "360.cn",
}

_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _norm_domain(domain: str) -> str:
    d = unicodedata.normalize("NFKC", domain.lower().split(":")[0])
    if d.startswith("www."):
        d = d[4:]
    for k, v in LOOKALIKE.items():
        d = d.replace(k, v)
    return d


def registrable_domain(domain: str) -> str:
    """取可注册域名（去掉子域，兼顾 com.cn 这类双后缀）。"""
    parts = _norm_domain(domain).split(".")
    if len(parts) <= 2:
        return ".".join(parts)
    double = {"com.cn", "net.cn", "org.cn", "gov.cn", "co.jp", "com.hk", "co.uk", "com.tw"}
    if ".".join(parts[-2:]) in double and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def spoof_score(domain: str) -> tuple[int, list[str]]:
    """域名仿冒检测。"""
    name = registrable_domain(domain).split(".")[0]
    score, reasons = 0, []
    for brand in BRANDS:
        if name == brand:
            return 0, []
        if brand in name:
            score = max(score, 60 if len(name) > len(brand) + 2 else 45)
            reasons.append(f"域名包含品牌词「{brand}」但非官方域名")
            break
        if abs(len(name) - len(brand)) <= 2 and _levenshtein(name, brand) <= 2:
            score = max(score, 60)
            reasons.append(f"域名与品牌「{brand}」高度相似（疑似仿冒）")
            break
    if name.count("-") >= 2 or len(re.findall(r"\d", name)) >= 3:
        score = max(score, 25)
        reasons.append("域名含异常连字符或数字组合")
    return score, reasons


def is_trusted(url: str) -> bool:
    """是否属于公认安全域名或其子域（白名单短路，直接放行且不联网）。"""
    try:
        host = (urlparse(url if "://" in url else f"http://{url}").hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    try:
        from .security import is_whitelisted

        if is_whitelisted(url):
            return True
    except Exception:
        pass
    host = host[4:] if host.startswith("www.") else host
    return any(host == item or host.endswith("." + item) for item in TRUSTED)


# ══════════════════════════════════════════════════════════════
# 第一段：本地规则（毫秒级，不联网）
# ══════════════════════════════════════════════════════════════

def local_score(url: str) -> dict[str, Any]:
    """纯本地评分：能在不联网的情况下说清「哪里可疑」。"""
    target = url if "://" in url else f"http://{url}"
    try:
        parsed = urlparse(target)
    except ValueError:
        return {"url": url, "host": "", "score": 0, "reasons": ["网址无法解析"], "isIp": False}

    host = (parsed.hostname or "").lower()
    score = 0
    reasons: list[str] = []
    is_ip = bool(re.fullmatch(r"[\d.]+", host))

    # 1) 域名仿冒
    if not is_ip and host:
        sub, sub_reasons = spoof_score(host)
        score += sub
        reasons += sub_reasons

    # 2) IP 直连 / 非常规端口
    if is_ip:
        score += 30
        reasons.append("使用 IP 地址访问而非域名")
    if parsed.port not in (None, 80, 443):
        score += 15
        reasons.append(f"使用非常规端口 {parsed.port}")

    # 3) 中文/同形字域名（punycode）—— 钓鱼常用
    if "xn--" in host:
        score += 35
        reasons.append("域名含 punycode 编码（可能是同形字钓鱼域名）")

    # 4) 高风险后缀
    suffix = host.rsplit(".", 1)[-1] if "." in host else ""
    if suffix in RISKY_TLDS:
        score += 25
        reasons.append(f"域名后缀 .{suffix} 属于低门槛高风险后缀")

    # 5) URL 里的欺骗手法：用户名@主机
    if "@" in (parsed.netloc or ""):
        score += 20
        reasons.append("网址中含 @ 符号（可能用真实域名伪装）")

    # 6) 敏感路径词 + 非知名域名
    path_text = f"{parsed.path or ''}{parsed.query or ''}".lower()
    if path_text and not is_ip:
        if any(word in path_text for word in PHISH_WORDS) and not is_trusted(target):
            score += 20
            reasons.append("链接含登录/支付/验证类敏感词，且域名不在已知名单中")

    # 7) 超长 URL（常见于追踪跳转与钓鱼）
    if len(target) > 180:
        score += 10
        reasons.append("网址异常长（可能经过多层跳转）")

    return {
        "url": url,
        "host": host,
        "score": max(0, score),
        "reasons": reasons,
        "isIp": is_ip,
        "mode": "local",
    }


# ══════════════════════════════════════════════════════════════
# 第二段：深度规则（联网，严格限时）
# ══════════════════════════════════════════════════════════════

def _open(url: str, timeout: float = _TIMEOUT):
    proxies = _system_proxy()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler(proxies) if proxies else None
    )
    return opener.open(urllib.request.Request(url, headers=_HEADERS), timeout=timeout)


def _fetch_page(url: str) -> str:
    try:
        with _open(url) as resp:
            return resp.read(200_000).decode("utf-8", "ignore")
    except (OSError, urllib.error.URLError, ValueError):
        return ""


def _has_icp(html: str) -> bool:
    return bool(re.search(r"(ICP\s*备|京ICP|粤ICP|沪ICP|浙ICP|苏ICP|鲁ICP|蜀ICP|ICP证)", html, re.I))


def _cross_origin_downloads(html: str, host: str) -> int:
    hits = 0
    for m in re.finditer(
        r'href\s*=\s*["\']([^"\']+\.(?:zip|rar|7z|exe|msi|apk))["\']', html, re.I
    ):
        link = m.group(1)
        if link.startswith("http") and host not in link:
            hits += 1
    return hits


def domain_age_days(domain: str) -> int | None:
    """通过 RDAP 查询域名注册天数；失败返回 None（调用方须说明未校验）。"""
    try:
        with _open(f"https://rdap.org/domain/{domain}") as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for event in data.get("events", []):
            if event.get("eventAction") == "registration":
                date = str(event.get("eventDate") or "")[:10]
                if date:
                    stamp = time.mktime(time.strptime(date, "%Y-%m-%d"))
                    return int((time.time() - stamp) / 86400)
    except (OSError, urllib.error.URLError, ValueError):
        return None
    return None


def deep_score(url: str, base: dict[str, Any]) -> dict[str, Any]:
    """在本地结果上叠加联网规则（页面 + 域名年龄）。"""
    score = int(base.get("score") or 0)
    reasons = list(base.get("reasons") or [])
    host = str(base.get("host") or "")
    target = url if "://" in url else f"http://{url}"
    checked: list[str] = []

    html = _fetch_page(target)
    if html:
        checked.append("页面内容")
        if not _has_icp(html):
            score += 30
            reasons.append("页面未发现 ICP 备案号")
        downloads = _cross_origin_downloads(html, host)
        if downloads:
            score += min(40, 20 + downloads * 5)
            reasons.append(f"存在 {downloads} 个跨域压缩包/可执行下载链接")
    else:
        # 抓不到页面不能当作「没问题」：本地分已经偏高时加重判定
        if score >= WARN_SCORE:
            score += 20
            reasons.append("页面无法访问，未能完成页面校验（按域名风险等级保守判定）")
        else:
            reasons.append("页面无法访问（本次未做页面校验）")

    if host and not base.get("isIp"):
        age = domain_age_days(registrable_domain(host))
        if age is not None:
            checked.append("域名年龄")
            if age < 30:
                score += 60
                reasons.append(f"域名注册仅 {age} 天（新域名高风险）")
            elif age > 730:
                score -= 20
                reasons.append(f"域名已注册 {age // 365} 年（可信度加分）")
        else:
            reasons.append("未能查询到域名注册时间（可能离线，本次未做该项校验）")

    score = max(0, score)
    return {
        "url": url,
        "host": host,
        "score": score,
        "level": _level(score),
        "reasons": reasons,
        "mode": "deep",
        "checked": checked,
    }


def _level(score: int) -> str:
    if score >= DANGER_SCORE:
        return "danger"
    if score >= WARN_SCORE:
        return "warn"
    return "safe"


# ══════════════════════════════════════════════════════════════
# 对外入口
# ══════════════════════════════════════════════════════════════

def check_url(url: str, deep: bool = False, use_cache: bool = True) -> dict[str, Any]:
    """检测一个网址。

    deep=False（默认）：只跑本地规则，毫秒级返回 —— 适合剪贴板/浏览器
    监控这种高频调用；deep=True：额外联网校验页面与域名年龄，适合用户
    主动点「检查」时使用。
    """
    key = f"{'d' if deep else 'l'}:{url}"
    now = time.time()
    if use_cache:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_TTL:
            return {**hit[1], "cached": True}

    if is_trusted(url):
        result = {
            "url": url,
            "host": "",
            "score": 0,
            "level": "safe",
            "reasons": ["属于已知安全域名（白名单）"],
            "mode": "trusted",
        }
    else:
        base = local_score(url)
        if deep:
            result = deep_score(url, base)
        else:
            score = int(base.get("score") or 0)
            result = {
                "url": url,
                "host": base.get("host", ""),
                "score": score,
                "level": _level(score),
                "reasons": base.get("reasons") or ["本地规则未发现风险特征（未做联网校验）"],
                "mode": "local",
            }

    result["cached"] = False
    if use_cache:
        _cache[key] = (now, result)
        if len(_cache) > _CACHE_MAX:
            for old_key in sorted(_cache, key=lambda k: _cache[k][0])[: len(_cache) - _CACHE_MAX]:
                _cache.pop(old_key, None)
    return result


def clear_cache() -> int:
    size = len(_cache)
    _cache.clear()
    return size
