"""Persistent rolling summary for Online Chat working context.

Raw Message rows remain authoritative and are never deleted by compaction. The
model receives a projection: an older summary plus the latest raw turns.
"""

import logging

from django.utils.timezone import now
from openai import OpenAI

from ai.config import llm_api_base, llm_api_key, llm_model, require_llm_config
from web.models.friend import Friend, Message


logger = logging.getLogger(__name__)
RECENT_TURNS = 10
COMPACT_TRIGGER_TURNS = 15
MAX_SUMMARY_CHARS = 2000
MAX_BATCH_CHARS = 8000


def _message_text(message: Message) -> str:
    return (
        f"[message_id={message.id}] 用户：{message.user_message}\n"
        f"[message_id={message.id}] AI：{(message.output or '')[:1500]}"
    )


def _partition_batches(messages: list[Message]) -> list[list[Message]]:
    batches: list[list[Message]] = []
    current: list[Message] = []
    current_chars = 0
    for message in messages:
        size = len(_message_text(message))
        if current and current_chars + size > MAX_BATCH_CHARS:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(message)
        current_chars += size
    if current:
        batches.append(current)
    return batches


def _summarize_batch(
    previous_summary: str,
    messages: list[Message],
    api_key: str = "",
    api_base: str = "",
) -> str:
    if not api_key and not api_base:
        require_llm_config()
    client = OpenAI(
        api_key=api_key or llm_api_key(),
        base_url=api_base or llm_api_base(),
        timeout=40,
    )
    dialogue = "\n\n".join(_message_text(message) for message in messages)
    prompt = f"""把伴侣聊天的旧上下文更新成一份可继续对话的滚动摘要。

已有摘要：
{previous_summary or '（暂无）'}

新增旧对话：
{dialogue}

只输出更新后的摘要正文，最多 {MAX_SUMMARY_CHARS} 个中文字符。要求：
1. 保留正在讨论的主题、未完成问题、临时约定、代词指向和近期情绪变化。
2. 保留重要时间顺序与状态变化，不把“以前”写成“现在”。
3. 不推测、不编造，不写成逐条数据库事实，也不要重复寒暄。
4. 已有摘要中的仍有效信息必须保留；已被新增对话更新的状态要明确说明变化。
5. 这是较早对话的摘要，不要假装是用户本轮刚说的话。"""
    response = client.chat.completions.create(
        model=llm_model(),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=1200,
    )
    content = (response.choices[0].message.content or "").strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
        if "```" in content:
            content = content.rsplit("```", 1)[0]
        content = content.strip()
    return content[:MAX_SUMMARY_CHARS]


def prepare_conversation_context(
    friend: Friend,
    api_key: str = "",
    api_base: str = "",
) -> tuple[str, list[Message]]:
    """Return rolling summary and raw recent turns for the next model request."""
    queryset = Message.objects.filter(friend=friend)
    if friend.summary_through_message_id:
        queryset = queryset.filter(id__gt=friend.summary_through_message_id)
    unsummarized = list(queryset.order_by("id"))

    # Keep a five-turn buffer beyond the normal raw window, avoiding one
    # compaction call on every turn without hiding unsummarized messages.
    if len(unsummarized) <= COMPACT_TRIGGER_TURNS:
        return friend.conversation_summary or "", unsummarized

    to_compact = unsummarized[:-RECENT_TURNS]
    recent = unsummarized[-RECENT_TURNS:]
    summary = friend.conversation_summary or ""
    try:
        for batch in _partition_batches(to_compact):
            summary = _summarize_batch(summary, batch, api_key, api_base)
            if not summary:
                raise RuntimeError("conversation summary model returned empty text")
            friend.conversation_summary = summary
            friend.summary_through_message_id = batch[-1].id
            friend.summary_updated_at = now()
            friend.save(update_fields=[
                "conversation_summary",
                "summary_through_message_id",
                "summary_updated_at",
            ])
    except Exception:
        logger.exception("Failed to compact Online Chat for Friend %s", friend.id)
        remaining = Message.objects.filter(friend=friend)
        if friend.summary_through_message_id:
            remaining = remaining.filter(id__gt=friend.summary_through_message_id)
        return (
            friend.conversation_summary or "",
            list(remaining.order_by("id"))[-COMPACT_TRIGGER_TURNS:],
        )

    return summary, recent
