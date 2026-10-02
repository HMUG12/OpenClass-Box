"""URL 守卫：局域网地址不该被当成可疑外链。

背景：访问自己机器上的服务（手机控制台 38610、临时传输 38620、机房协同 38900）
必然是「私有 IP + 自定义端口」，而这两条规则各扣 30 / 15 分 —— 正好 45 分触发
"安全提醒"。也就是说**正常操作天天弹提醒**，弹多了用户就学会无视，
那才是真正的风险。

同时要保证：**公网 IP 仍然照常计分**（这条规则本来是针对外链的）。
"""
from __future__ import annotations

from backend.core.url_guard import check_url


def test_console_url_is_not_suspicious():
    """自家局域网地址 + 自有端口：0 分。"""
    result = check_url("http://192.168.0.105:38610")
    assert result["score"] == 0, result
    assert result["level"] == "safe"


def test_loopback_is_not_suspicious():
    assert check_url("http://127.0.0.1:38610")["score"] == 0


def test_all_private_ranges_are_exempt():
    for url in (
        "http://192.168.1.10:38900",
        "http://10.0.0.5:38900",
        "http://172.16.3.4:38900",
        "http://172.31.255.254:38900",
    ):
        assert check_url(url)["score"] == 0, url


def test_public_ip_still_penalized():
    """公网 IP + 非常规端口仍然算风险 —— 规则是针对外链的，不能一起放过。"""
    result = check_url("http://8.8.8.8:8080")
    assert result["score"] >= 30, result
    assert any("IP 地址" in reason for reason in result["reasons"])


def test_private_ip_only_exempts_the_ip_rule():
    """只免掉「IP 直连 / 非常规端口」，其他规则照常生效。

    用高风险后缀验证：域名规则不该因为是内网就被跳过。
    """
    result = check_url("http://internal.evil.top/login")
    assert result["score"] > 0, result


def test_normal_external_url_unaffected():
    result = check_url("https://github.com/HMUG12/OpenClass-Box")
    assert result["score"] == 0
    assert result["level"] == "safe"
