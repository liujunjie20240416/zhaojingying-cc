import datetime
from collections import Counter
from zoneinfo import ZoneInfo

from django.utils import timezone


TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_DAY_START_HOUR = 5


def to_local(dt: datetime.datetime) -> datetime.datetime:
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, TZ)
    return dt.astimezone(TZ)


def detect_day_start_hour_from_datetimes(datetimes: list[datetime.datetime]) -> int:
    night_hours = [to_local(dt).hour for dt in datetimes if dt and 0 <= to_local(dt).hour < 6]
    if not night_hours:
        return DEFAULT_DAY_START_HOUR
    counter = Counter(night_hours)
    for hour in range(6):
        counter.setdefault(hour, 0)
    return min(counter, key=counter.get) + 1


def get_chat_day(dt: datetime.datetime, day_start_hour: int) -> datetime.date:
    local = to_local(dt)
    if local.hour < day_start_hour:
        local -= datetime.timedelta(days=1)
    return local.date()


def get_chat_day_range(
    chat_day: datetime.date, day_start_hour: int
) -> tuple[datetime.datetime, datetime.datetime]:
    start = timezone.make_aware(
        datetime.datetime.combine(chat_day, datetime.time(hour=day_start_hour)), TZ
    )
    return start, start + datetime.timedelta(days=1)
