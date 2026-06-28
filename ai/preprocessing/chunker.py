"""Step 1: Chunking — 按"聊天日"切分，一天 = 一个 Chunk。

聊天日起点从数据中自动计算：统计历史消息的发送时间分布，
找出最晚聊天的小时，+1 作为第二天起点。

例如历史最晚消息在 02:34，那 03:00 就是一天的分界：
  昨天 23:30 → 属于"昨天"
  今天 00:30 → 属于"昨天"（在 03:00 前）
  今天 07:30 → 属于"今天"

如果某天消息超过上限，再按日内会话空隙二次切分。
"""

import datetime
from collections import Counter
from zoneinfo import ZoneInfo

from web.models.chat_message import ChatMessage

TZ = ZoneInfo("Asia/Shanghai")
MAX_PER_CHUNK = 500   # 单 Chunk 上限（微信消息短，一天的对话通常放得下）
SESSION_GAP_HOURS = 4  # 日内二次切分的会话间隙


def chunk_messages(character_id: int, api_key: str = "", api_base: str = "") -> list[dict]:
    """切分消息：每天一个 Chunk。

    api_key/api_base 保留参数兼容，实际不用 LLM。
    """
    msgs = list(
        ChatMessage.objects.filter(character_id=character_id)
        .order_by("msg_index")
        .values("sender", "content", "timestamp", "msg_index")
    )

    if not msgs:
        return []

    # 从数据中计算一天的起点
    day_start_hour = _detect_day_start(msgs)

    chunks: list[list[dict]] = [[msgs[0]]]
    prev_dt = _parse_datetime(msgs[0].get("timestamp", ""))

    for msg in msgs[1:]:
        cur_dt = _parse_datetime(msg.get("timestamp", ""))
        should_split = False

        if prev_dt and cur_dt:
            if _crosses_day(prev_dt, cur_dt, day_start_hour):
                # 跨聊天日 → 新的一天
                should_split = True
            elif not _crosses_day(prev_dt, cur_dt, day_start_hour):
                # 同一天内但间隙太大 → 不同的会话段落（只在消息过多时触发）
                gap_h = (cur_dt - prev_dt).total_seconds() / 3600
                if len(chunks[-1]) >= MAX_PER_CHUNK and gap_h > SESSION_GAP_HOURS:
                    should_split = True

        if should_split:
            chunks.append([msg])
        else:
            chunks[-1].append(msg)

        if cur_dt:
            prev_dt = cur_dt

    return [_build_chunk(i, c) for i, c in enumerate(chunks)]


def _detect_day_start(msgs: list[dict]) -> int:
    """从消息时间分布推算聊天日起点。

    统计凌晨 0-6 点每小时的消息数，找到最少消息的那个小时作为自然睡眠断点。
    例如凌晨 2 点消息最少 → 说明一般聊到 2 点就睡了 → 起点设为 3 点。

    如果没有凌晨消息（全部在 6 点后），默认 5 点。
    """
    hours = []
    for m in msgs:
        dt = _parse_datetime(m.get("timestamp", ""))
        if dt:
            naive = dt.astimezone(TZ).replace(tzinfo=None) if dt.tzinfo else dt
            hours.append(naive.hour)

    if not hours:
        return 5

    # 只看凌晨 0-6 点
    night_hours = [h for h in hours if 0 <= h < 6]

    if not night_hours:
        return 5  # 没人凌晨聊天，默认 5 点

    # 统计每小时消息数，最少消息的小时 +1 作为边界
    counter = Counter(night_hours)
    # 补充不存在的小时（消息数为 0 = 最少的）
    for h in range(6):
        if h not in counter:
            counter[h] = 0

    quietest = min(counter, key=counter.get)
    return quietest + 1  # 最安静的小时之后就是新的一天


def _chat_day_id(dt: datetime.datetime, day_start: int) -> int:
    """返回聊天日编号。day_start 点之前属于前一天。"""
    naive = dt.astimezone(TZ).replace(tzinfo=None) if dt.tzinfo else dt
    if naive.hour < day_start:
        naive = naive - datetime.timedelta(days=1)
    return naive.toordinal()


def _crosses_day(prev: datetime.datetime, cur: datetime.datetime, day_start: int) -> bool:
    """两条消息是否跨聊天日"""
    return _chat_day_id(prev, day_start) != _chat_day_id(cur, day_start)


def _build_chunk(index: int, msgs: list[dict]) -> dict:
    msg_indices = [m["msg_index"] for m in msgs]
    timestamps = [m.get("timestamp", "") for m in msgs if m.get("timestamp")]
    return {
        "index": index,
        "start_msg_index": min(msg_indices),
        "end_msg_index": max(msg_indices),
        "time_start": min(timestamps)[:10] if timestamps else "",
        "time_end": max(timestamps)[:10] if timestamps else "",
        "messages": [
            {
                "sender": m["sender"],
                "content": m["content"],
                "timestamp": m.get("timestamp", ""),
                "msg_index": m["msg_index"],
            }
            for m in msgs
        ],
    }


def _parse_datetime(timestamp: str) -> datetime.datetime | None:
    if not timestamp:
        return None
    try:
        dt = datetime.datetime.strptime(timestamp[:19], "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=TZ)
    except (ValueError, IndexError):
        try:
            dt = datetime.datetime.strptime(timestamp[:10], "%Y-%m-%d")
            return dt.replace(tzinfo=TZ)
        except ValueError:
            return None
