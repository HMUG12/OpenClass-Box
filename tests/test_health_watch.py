"""维护清单的判定规则 —— 只验证纯函数，不碰真实硬件。

立场校验：结论必须**可解释**（每条都带依据数值），且不允许出现"寿命预测"式表述。
"""
from __future__ import annotations

from backend.core import health_watch as hw


def test_healthy_disk_stays_ok():
    result = hw.evaluate_disk(
        {
            "name": "Samsung SSD 980",
            "mediaType": "SSD",
            "health": "Healthy",
            "temperature": 38,
            "powerOnHours": 1200,
            "readErrors": 0,
            "writeErrors": 0,
            "wear": 4,
        }
    )
    assert result["level"] == "ok"
    assert result["reasons"] == []
    assert result["typeLabel"] == "固态硬盘"


def test_read_errors_trigger_replacement_advice():
    """读写错误是"已经在丢数据"的信号，应给出更换建议。"""
    result = hw.evaluate_disk(
        {
            "name": "WD Blue",
            "mediaType": "HDD",
            "health": "Healthy",
            "temperature": 40,
            "readErrors": 25,
            "writeErrors": 0,
        }
    )
    assert result["level"] == "replace"
    assert any("读写错误" in reason for reason in result["reasons"])


def test_high_temperature_warns_with_number():
    result = hw.evaluate_disk(
        {"name": "Seagate", "mediaType": "HDD", "health": "Healthy", "temperature": 58}
    )
    assert result["level"] == "warn"
    assert any("58" in reason for reason in result["reasons"])


def test_old_drive_is_watched_not_damned():
    """通电时间长只算"关注"，不直接判死刑（老盘还能用）。"""
    result = hw.evaluate_disk(
        {
            "name": "Old HDD",
            "mediaType": "HDD",
            "health": "Healthy",
            "temperature": 42,
            "powerOnHours": 30000,
        }
    )
    assert result["level"] == "watch"
    assert any("30000" in reason for reason in result["reasons"])


def test_unhealthy_status_escalates():
    result = hw.evaluate_disk({"name": "X", "mediaType": "SSD", "health": "Unhealthy"})
    assert result["level"] == "replace"


def test_crash_signal_levels():
    assert hw.evaluate_crash({"count": 0, "windowDays": 14})["level"] == "ok"
    assert hw.evaluate_crash({"count": 1, "windowDays": 14})["level"] == "watch"
    assert hw.evaluate_crash({"count": 4, "windowDays": 14})["level"] == "warn"


def test_cpu_temperature_missing_is_not_guessed():
    """读不到温度就明确说读不到，绝不编一个数。"""
    result = hw.evaluate_cpu_temp(None)
    assert result["level"] == "ok"
    assert result["temperature"] is None
    assert result["reasons"] == []


def test_cpu_temperature_thresholds():
    assert hw.evaluate_cpu_temp(60)["level"] == "ok"
    assert hw.evaluate_cpu_temp(78)["level"] == "watch"
    assert hw.evaluate_cpu_temp(90)["level"] == "warn"


def test_scan_output_declares_no_lifetime_prediction():
    """对外表述里必须写明不做寿命预测（避免以后被"加回去"）。"""
    assert "不做寿命预测" in hw.scan.__doc__ or "不做寿命预测" in (hw.__doc__ or "")
