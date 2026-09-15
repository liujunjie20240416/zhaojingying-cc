from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


WEEKDAY_LABELS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

SOLAR_HOLIDAYS = {
    "01-01": "元旦",
    "02-14": "情人节",
    "03-08": "妇女节",
    "05-01": "劳动节",
    "05-04": "青年节",
    "06-01": "儿童节",
    "10-01": "国庆节",
    "12-24": "平安夜",
    "12-25": "圣诞节",
}


def get_current_time(timezone: str = "Asia/Shanghai", now_dt: datetime | None = None) -> dict:
    """Return local time context for prompts and future agent tools."""
    tz = ZoneInfo(timezone)
    current = now_dt.astimezone(tz) if now_dt else datetime.now(tz)
    weekday = WEEKDAY_LABELS[current.weekday()]
    date_key = current.strftime("%m-%d")
    holiday = SOLAR_HOLIDAYS.get(date_key)

    return {
        "date": current.strftime("%Y-%m-%d"),
        "time": current.strftime("%H:%M"),
        "datetime": current.isoformat(timespec="minutes"),
        "weekday": weekday,
        "timezone": timezone,
        "is_weekend": current.weekday() >= 5,
        "day_type": "周末" if current.weekday() >= 5 else "工作日",
        "holiday": holiday,
    }


def format_current_time_context(time_info: dict | None = None) -> str:
    """Format time information into a compact system-prompt section."""
    info = time_info or get_current_time()
    holiday_text = f"，今天是{info['holiday']}" if info.get("holiday") else ""
    return (
        "【当前时间】\n"
        f"现在是 {info['date']} {info['time']}，{info['weekday']}，"
        f"{info['day_type']}，时区 {info['timezone']}{holiday_text}。\n"
        "如果用户提到今天、昨天、明天、周几、周末、节日或时间安排，请以这里的当前时间为准。\n"
    )
