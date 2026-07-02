from datetime import datetime, timezone


def test_get_current_time_formats_weekday_and_weekend():
    from ai.tools.time_tools import get_current_time

    result = get_current_time(
        now_dt=datetime(2026, 7, 2, 1, 30, tzinfo=timezone.utc)
    )

    assert result["date"] == "2026-07-02"
    assert result["time"] == "09:30"
    assert result["weekday"] == "星期四"
    assert result["day_type"] == "工作日"
    assert result["is_weekend"] is False


def test_get_current_time_detects_solar_holiday():
    from ai.tools.time_tools import get_current_time

    result = get_current_time(
        now_dt=datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)
    )

    assert result["date"] == "2026-10-01"
    assert result["holiday"] == "国庆节"


def test_format_current_time_context_mentions_current_time_rules():
    from ai.tools.time_tools import format_current_time_context

    text = format_current_time_context(
        {
            "date": "2026-07-02",
            "time": "09:30",
            "weekday": "星期四",
            "day_type": "工作日",
            "timezone": "Asia/Shanghai",
            "holiday": None,
        }
    )

    assert "【当前时间】" in text
    assert "2026-07-02 09:30" in text
    assert "星期四" in text
    assert "今天、昨天、明天" in text
