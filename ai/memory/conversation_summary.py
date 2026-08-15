"""Persistent rolling summary for Online Chat working context.

Raw Message rows remain authoritative and are never deleted by compaction. The
model receives a projection: an older summary plus the latest raw turns.
"""

import logging
import json
import re
import threading

from django.utils.timezone import now
from openai import OpenAI

from ai.config import llm_api_base, llm_api_key, llm_model, require_llm_config
from ai.memory.context_budget import (
    RECENT_HISTORY_TOKEN_BUDGET,
    SUMMARY_TOKEN_BUDGET,
    WORKING_HISTORY_TOKEN_BUDGET,
    estimate_tokens,
)
from web.models.friend import Friend, Message
from web.models.memory import ConversationCollapse


logger = logging.getLogger(__name__)
MIN_RECENT_TURNS = 10
MAX_SUMMARY_CHARS = 2000
MAX_BATCH_CHARS = 8000

_fold_locks: dict[int, threading.Lock] = {}
_fold_locks_guard = threading.Lock()


def _friend_fold_lock(friend_id: int) -> threading.Lock:
    """Per-Friend mutex for the rolling fold.

    Two concurrent chat requests on the same Friend can read the same
    checkpoint, fold the same range, and then the slower one overwrites the
    other's summary and rolls the checkpoint back.  The deployment runs a
    single uvicorn process, so an in-process lock fully serializes folds for
    one Friend; a multi-worker deployment would need a database-level lock.
    """
    with _fold_locks_guard:
        lock = _fold_locks.get(friend_id)
        if lock is None:
            lock = _fold_locks[friend_id] = threading.Lock()
        return lock


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
) -> tuple[str, str, list[str]]:
    if not api_key and not api_base:
        require_llm_config()
    client = OpenAI(
        api_key=api_key or llm_api_key(),
        base_url=api_base or llm_api_base(),
        timeout=40,
    )
    dialogue = "\n\n".join(_message_text(message) for message in messages)
    start_id, end_id = messages[0].id, messages[-1].id
    prompt = f"""把伴侣聊天的旧上下文更新成一份可继续对话的结构化工作摘要。

已有摘要：
{previous_summary or '（暂无）'}

新增旧对话：
{dialogue}

只输出 JSON，不要 Markdown：
{{
  "working_summary": "更新后的完整工作摘要",
  "collapse_summary": "仅概括这批 message_id {start_id}-{end_id} 的阶段胶囊",
  "topics": ["这批对话的检索关键词，最多 6 个"]
}}

working_summary 最多 {MAX_SUMMARY_CHARS} 个中文字符（约 {SUMMARY_TOKEN_BUDGET} token），使用以下固定区块；没有内容写“无”：
【覆盖范围】message_id {start_id}-{end_id}（与已有摘要的范围合并）
【进行中的话题与指代】
【未完成约定或待办】
【当前状态与情绪】
【已变化的历史状态】
【必须保留的具体事实】

规则：
1. 保留时间顺序，绝不能把“以前”写成“现在”。
2. 不推测、不编造；不要重复寒暄，也不要写成完整聊天记录。
3. 已有摘要仍有效的信息必须保留；状态改变时同时写明旧状态和新状态。
4. 这是较早对话的工作摘要，不要假装是用户本轮刚说的话。"""
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
    try:
        payload = json.loads(content)
        summary = str(payload.get("working_summary", "")).strip()
        collapse = str(payload.get("collapse_summary", "")).strip()
        topics = payload.get("topics", [])
        if not isinstance(topics, list):
            topics = []
        topics = [str(topic).strip()[:80] for topic in topics if str(topic).strip()][:6]
        if summary:
            return summary[:MAX_SUMMARY_CHARS], collapse[:1200], topics
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    # A provider may occasionally ignore the JSON shape. Keep compaction
    # forward-compatible: preserve the old rolling-summary behaviour and use
    # the same text as the range capsule rather than losing raw-history safety.
    fallback = content[:MAX_SUMMARY_CHARS]
    return fallback, fallback[:1200], []


def _save_collapse(friend: Friend, messages: list[Message], summary: str, topics: list[str]) -> None:
    if not messages or not summary:
        return
    ConversationCollapse.objects.update_or_create(
        friend=friend,
        start_message_id=messages[0].id,
        end_message_id=messages[-1].id,
        defaults={
            "summary": summary[:2000],
            "topics": topics,
            "token_count": estimate_tokens(summary),
        },
    )


def search_conversation_collapses(
    friend_id: int,
    query: str,
    *,
    time_mode: str = "any",
    limit: int = 3,
) -> list[ConversationCollapse]:
    """Locate historical periods without injecting every old summary.

    This is deliberately read-time only: neither raw messages nor old facts are
    deleted when a collapse is created.  Keyword hits select the period; a
    historical/early request can fall back to the earliest capsules, while a
    recent request uses the latest ones.
    """
    queryset = ConversationCollapse.objects.filter(friend_id=friend_id)
    terms = [term for term in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{3,}", query or "")]
    matches = []
    for collapse in queryset.order_by("start_message_id"):
        haystack = " ".join([collapse.summary, *[str(topic) for topic in collapse.topics]])
        score = sum(1 for term in terms[:8] if term in haystack)
        if score:
            matches.append((score, collapse))
    if matches:
        return [item for _, item in sorted(matches, key=lambda pair: (-pair[0], -pair[1].end_message_id))[:limit]]
    if time_mode in {"historical", "early", "specific_time"}:
        return list(queryset.order_by("start_message_id")[:limit])
    if time_mode in {"recent", "current"}:
        return list(queryset.order_by("-end_message_id")[:limit])
    return []


def prepare_conversation_context(
    friend: Friend,
    api_key: str = "",
    api_base: str = "",
) -> tuple[str, list[Message]]:
    """Return rolling summary and raw recent turns for the next model request.

    The fold is serialized per Friend: without it, two concurrent requests
    would both read the same checkpoint, fold the same range, and the slower
    one would overwrite the winner's summary and roll the checkpoint back.
    The checkpoint is therefore re-read under the lock, so the loser waits
    and then finds nothing left to fold.
    """
    queryset = Message.objects.filter(friend=friend)
    if friend.summary_through_message_id:
        queryset = queryset.filter(id__gt=friend.summary_through_message_id)
    unsummarized = list(queryset.order_by("id"))

    unsummarized_tokens = sum(estimate_tokens(_message_text(message)) for message in unsummarized)
    if unsummarized_tokens <= WORKING_HISTORY_TOKEN_BUDGET:
        return friend.conversation_summary or "", unsummarized

    with _friend_fold_lock(friend.id):
        # A concurrent fold may have advanced the checkpoint while we waited;
        # re-read it and fold only what is still unsummarized.
        friend.refresh_from_db()
        queryset = Message.objects.filter(friend=friend)
        if friend.summary_through_message_id:
            queryset = queryset.filter(id__gt=friend.summary_through_message_id)
        unsummarized = list(queryset.order_by("id"))
        unsummarized_tokens = sum(estimate_tokens(_message_text(message)) for message in unsummarized)
        if unsummarized_tokens <= WORKING_HISTORY_TOKEN_BUDGET:
            return friend.conversation_summary or "", unsummarized

        recent: list[Message] = []
        recent_tokens = 0
        for message in reversed(unsummarized):
            message_tokens = estimate_tokens(_message_text(message))
            if (
                len(recent) >= MIN_RECENT_TURNS
                and recent_tokens + message_tokens > RECENT_HISTORY_TOKEN_BUDGET
            ):
                break
            recent.append(message)
            recent_tokens += message_tokens
        recent.reverse()
        to_compact = unsummarized[:len(unsummarized) - len(recent)]
        summary = friend.conversation_summary or ""
        try:
            for batch in _partition_batches(to_compact):
                summarized = _summarize_batch(summary, batch, api_key, api_base)
                # Keep old tests/extensions that return the pre-collapse string
                # shape working while new providers return the structured tuple.
                if isinstance(summarized, tuple):
                    summary, collapse_summary, collapse_topics = summarized
                else:
                    summary = str(summarized or "")
                    collapse_summary, collapse_topics = summary, []
                if not summary:
                    raise RuntimeError("conversation summary model returned empty text")
                _save_collapse(friend, batch, collapse_summary, collapse_topics)
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
            # Keep the promised recent-turn floor, but never reference the removed
            # turn-trigger constant.  A failed compaction must still return a
            # bounded, token-aware raw projection.
            fallback: list[Message] = []
            fallback_tokens = 0
            for message in reversed(list(remaining.order_by("-id")[:MIN_RECENT_TURNS * 4])):
                message_tokens = estimate_tokens(_message_text(message))
                if len(fallback) >= MIN_RECENT_TURNS and fallback_tokens + message_tokens > RECENT_HISTORY_TOKEN_BUDGET:
                    break
                fallback.append(message)
                fallback_tokens += message_tokens
            fallback.reverse()
            return friend.conversation_summary or "", fallback

        return summary, recent
