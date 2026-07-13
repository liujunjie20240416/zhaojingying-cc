"""Memory Agent — 检索 Semantic Memory + Conversation History + ImportAnalysis。

三轮检索：
  1. 时间匹配: 用户提到时间段 → 匹配 TimeChunk → 锁定 msg_index 范围
  2. 统一原文检索: Imported Chat + Online Chat，经各自 Adapter 搜索后合并
  3. 话题路由: 用户提到话题 → 查 TopicTag → 补充相关消息
"""
import json
import re

from ai.rag.reranker import Reranker
from ai.rag.query_rewriter import QueryRewriter
from ai.rag.compressor import ContextCompressor
from ai.memory.intent import detect_memory_intent
from ai.memory.history_search import ConversationHistorySearch
from ai.memory.import_access import can_access_imported_context
from ai.memory.semantic import search_semantic
from ai.tracing import record_trace
from web.models.chat_message import ChatMessage
from web.models.friend import Friend, Message
from web.models.import_analysis import ImportAnalysis, TimeChunk, TopicTag


def _search_time_chunks(character_id: int, user_msg: str) -> dict | None:
    """第 1 轮：时间匹配 — 在 TimeChunk 中搜索用户提到的时间段。

    不依赖 LLM 猜的阶段名（如"暧昧期"），而是：
    1. 关键词匹配 chunk 的 summary（内容描述）→ 找到对应时间范围
    2. 时间方向词（"以前"/"最近"）→ 映射到早/晚期 chunk
    """
    # 时间方向词
    early_words = ["刚认识", "以前", "那会儿", "那时候", "当初", "最开始", "刚加", "第一次", "之前", "上次"]
    recent_words = ["最近", "前几天", "后来", "现在"]

    chunks = list(TimeChunk.objects.filter(character_id=character_id).order_by("start_msg_index"))
    if not chunks:
        return None

    import jieba
    keywords = []
    for word in jieba.cut(user_msg):
        word = word.strip()
        if len(word) >= 2:
            keywords.append(word)

    # 同时搜用户消息里的其他内容词（"火锅"、"吵架"等，不只是时间词）
    content_keywords = [kw for kw in keywords if kw not in early_words + recent_words]

    best_chunk = None
    best_score = 0
    for chunk in chunks:
        score = 0
        for kw in content_keywords:
            if kw in chunk.summary:
                score += 3  # 内容匹配分高
        if score > best_score:
            best_score = score
            best_chunk = chunk

    # 内容关键词匹配到了 → 直接返回
    if best_chunk and best_score >= 3:
        return _chunk_to_result(best_chunk)

    # 没匹配到内容，但用户说了时间方向词 → 取最早/最近的 chunk
    if any(w in user_msg for w in early_words):
        return _chunk_to_result(chunks[0])
    if any(w in user_msg for w in recent_words):
        return _chunk_to_result(chunks[-1])

    # 没有任何时间信号 → 不做时间过滤
    return None


def _chunk_to_result(chunk) -> dict:
    return {
        "label": chunk.label,
        "start_msg_index": chunk.start_msg_index,
        "end_msg_index": chunk.end_msg_index,
        "summary": chunk.summary,
    }


def _search_topic_tags(character_id: int, user_msg: str) -> list[str]:
    """第 3 轮：话题路由 — 匹配用户消息中的话题，返回相关消息 index 列表。

    先匹配标签名，再返回该标签关联的 msg_indices。
    """
    all_tags = list(TopicTag.objects.filter(character_id=character_id))
    if not all_tags:
        return []

    matched_indices: set[int] = set()

    for tag_obj in all_tags:
        # 如果话题标签中的关键词出现在用户消息中
        tag_parts = tag_obj.tag.split("/")
        for part in tag_parts:
            if part and part in user_msg:
                try:
                    indices = json.loads(tag_obj.msg_indices)
                    if isinstance(indices, list):
                        for idx in indices:
                            if isinstance(idx, int):
                                matched_indices.add(idx)
                except (json.JSONDecodeError, TypeError):
                    pass
                break  # 匹配到就跳出，不再重复加分

    return list(matched_indices)


def _load_chat_messages_by_indices(character_id: int, msg_indices: list[int], limit: int = 30) -> list[dict]:
    """根据 msg_index 列表加载 ChatMessage"""
    if not msg_indices:
        return []
    msgs = list(
        ChatMessage.objects.filter(
            character_id=character_id, msg_index__in=msg_indices
        ).order_by("msg_index")[:limit]
    )
    return [
        {"sender": m.sender, "content": m.content, "timestamp": m.timestamp, "msg_index": m.msg_index}
        for m in msgs
    ]


def memory_agent_node(state: dict, api_key: str = "", api_base: str = "") -> dict:
    """Memory Agent — 三轮检索：时间匹配 → 混合检索 → 话题路由"""
    user_msg = ""
    for msg in reversed(state.get("messages", [])):
        if hasattr(msg, "content"):
            user_msg = msg.content
            break

    if not user_msg:
        return {"memory_context": "", "semantic_facts": state.get("semantic_facts", [])}

    history_search = ConversationHistorySearch(api_key, api_base)
    reranker = Reranker(api_key, api_base)
    character_id = state.get("character_id")
    friend_id = state.get("friend_id", 0)
    friend = Friend.objects.select_related("character").filter(id=friend_id).first()
    imported_context_allowed = bool(friend and can_access_imported_context(friend))
    memory_intent = detect_memory_intent(user_msg)
    should_search_raw = (
        state.get("intent") == "recall" or memory_intent.get("needs_raw_chat", False)
    )
    queries = [user_msg]
    if state.get("intent") == "recall" and (
        len(user_msg) <= 30 or any(word in user_msg for word in ("那次", "当时", "那件事", "她"))
    ):
        try:
            queries = QueryRewriter(api_key, api_base).rewrite(user_msg)[:3]
        except Exception:
            queries = [user_msg]

    # 1. Search Semantic Memory
    semantic_candidates: dict[int, dict] = {}
    for query in queries:
        for item in search_semantic(
            friend_id,
            query,
            top_k=12,
            include_imported=imported_context_allowed,
        ):
            semantic_candidates.setdefault(item["id"], item)
    semantic_results = _rank_semantic_results(
        list(semantic_candidates.values()),
        memory_intent,
    )[:8]
    target_subject = memory_intent.get("target_subject", "mixed")
    category_hint = memory_intent.get("category_hint", "any")
    semantic_reliable = any(
        item.get("memory_state", "current") == "current"
        and (target_subject == "mixed" or item.get("subject") == target_subject)
        and (category_hint == "any" or item.get("category") == category_hint)
        for item in semantic_results[:5]
    )
    if state.get("intent") == "memory" and not semantic_reliable:
        should_search_raw = True
    semantic_facts = [r["fact"] for r in semantic_results]
    user_facts = [r["fact"] for r in semantic_results if r.get("subject", "user") == "user"]
    girlfriend_facts = [r["fact"] for r in semantic_results if r.get("subject") == "girlfriend"]
    relationship_experiences = [
        r["fact"] for r in semantic_results
        if r.get("subject") == "relationship" and r.get("category") == "experience"
    ]
    relationship_patterns = [
        r["fact"] for r in semantic_results
        if r.get("subject") == "relationship" and r.get("category") != "experience"
    ]

    # ── 第 1 轮：时间匹配 ──
    time_chunk = (
        _search_time_chunks(character_id, user_msg)
        if character_id and should_search_raw and imported_context_allowed
        else None
    )
    time_scope = None
    time_context = ""
    if time_chunk:
        time_scope = (time_chunk["start_msg_index"], time_chunk["end_msg_index"])
        time_context = f"【时间段】{time_chunk['label']}: {time_chunk['summary']}\n"

    # ── 第 2 轮：混合检索（有 time_scope 则缩小范围） ──
    history_context = ""
    history_hits: list[dict] = []
    has_imported = bool(
        imported_context_allowed
        and character_id
        and ChatMessage.objects.filter(character_id=character_id).exists()
    )
    has_online = bool(friend_id and Message.objects.filter(friend_id=friend_id).exists())
    if should_search_raw and (has_imported or has_online):
        candidates = history_search.search(
            queries,
            friend_id=friend_id,
            character_id=character_id if imported_context_allowed else None,
            imported_time_scope=time_scope,
            top_k=30,
        )
        history_hits = reranker.rerank(user_msg, candidates, top_k=8)
        history_parts = []
        for hit in history_hits[:5]:
            label = "导入聊天" if hit.get("source_type") == "import_chat" else "后续AI聊天"
            history_parts.append(f"【{label}】\n{hit.get('content', '')[:1200]}")
        history_context = "\n---\n".join(history_parts)
        if len(history_context) > 3000 and not any(
            signal in user_msg for signal in ("原话", "怎么说", "说了什么", "逐字")
        ):
            try:
                history_context = ContextCompressor(api_key, api_base).compress(
                    history_context, max_length=1000
                )
            except Exception:
                history_context = history_context[:3000]

    # ── 第 3 轮：话题路由 ──
    topic_indices = (
        _search_topic_tags(character_id, user_msg)
        if character_id and should_search_raw and imported_context_allowed
        else []
    )
    topic_messages = ""
    if topic_indices:
        topic_msgs = _load_chat_messages_by_indices(character_id, topic_indices, limit=20)
        if topic_msgs:
            char_name = state.get("character_name", "")
            chat_sender_name = state.get("chat_sender_name", "")
            lines = []
            for m in topic_msgs:
                role = char_name if m["sender"] == chat_sender_name else "对方"
                lines.append(f"[{m['timestamp']}] {role}：{m['content'][:200]}")
            topic_messages = "【话题相关消息】\n" + "\n".join(lines[:15])

    # Build context
    parts: list[str] = []
    if time_context:
        parts.append(time_context)
    if user_facts:
        parts.append("【用户记忆】\n" + "\n".join(f"- {f}" for f in user_facts))
    if girlfriend_facts:
        parts.append("【女友自我记忆】\n" + "\n".join(f"- {f}" for f in girlfriend_facts))
    if relationship_experiences:
        parts.append("【共同经历】\n" + "\n".join(f"- {f}" for f in relationship_experiences))
    if relationship_patterns:
        parts.append("【关系互动规律】\n" + "\n".join(f"- {f}" for f in relationship_patterns))
    if history_context:
        parts.append("【相关聊天原文】\n" + history_context)
    if topic_messages:
        parts.append(topic_messages)

    context = "\n\n".join(parts)

    # 注入关系演变概览（宏观）
    needs_relationship_overview = (
        memory_intent.get("target_subject") == "relationship"
        or memory_intent.get("category_hint") == "relationship"
    )
    if character_id and needs_relationship_overview and imported_context_allowed:
        analysis = ImportAnalysis.objects.filter(character_id=character_id, status="done").first()
        if analysis and analysis.relationship_overview:
            overview_parts = [analysis.relationship_overview]
            timeline_context = _timeline_context_for_intent(analysis, memory_intent)
            if timeline_context:
                overview_parts.append(timeline_context)
            context = f"【关系演变概览】\n{chr(10).join(overview_parts)}\n\n" + context

    result = {
        "memory_context": context,
        "semantic_facts": semantic_facts,
    }
    record_trace(
        "memory_agent.retrieval",
        {
            "user_msg": user_msg,
            "friend_id": friend_id,
            "character_id": character_id,
            "memory_intent": memory_intent,
            "should_search_raw": should_search_raw,
            "semantic_reliable": semantic_reliable,
            "imported_context_allowed": imported_context_allowed,
            "queries": queries,
            "time_chunk": time_chunk,
            "time_scope": time_scope,
            "semantic_results": semantic_results,
            "history_hits": history_hits,
            "history_context": history_context,
            "topic_indices": topic_indices,
            "topic_messages": topic_messages,
        },
        result,
        metadata=state.get("trace_metadata", {}),
    )
    return result


def _rank_semantic_results(results: list[dict], intent: dict) -> list[dict]:
    target_subject = intent.get("target_subject", "mixed")
    category_hint = intent.get("category_hint", "any")
    time_mode = intent.get("time_mode", "any")

    def score(item: dict) -> float:
        value = float(item.get("score", 0))
        if target_subject != "mixed" and item.get("subject") == target_subject:
            value += 1.0
        if category_hint != "any" and item.get("category") == category_hint:
            value += 0.7
        state = item.get("memory_state", "current")
        if time_mode in {"historical", "early", "specific_time"}:
            if state == "historical":
                value += 0.9
            elif state == "current":
                value += 0.2
        elif time_mode in {"current", "recent", "any"}:
            if state == "current":
                value += 0.9
            elif state == "historical":
                value -= 0.2
        return value

    return sorted(results, key=score, reverse=True)


def _timeline_context_for_intent(analysis: ImportAnalysis, intent: dict) -> str:
    if not analysis.timeline_json:
        return ""
    if intent.get("target_subject") != "relationship" and intent.get("time_mode") not in {
        "historical", "early", "recent", "specific_time",
    }:
        return ""
    try:
        timeline = json.loads(analysis.timeline_json)
    except (json.JSONDecodeError, TypeError):
        return ""
    stages = timeline.get("stages") if isinstance(timeline, dict) else []
    if not isinstance(stages, list) or not stages:
        return ""

    selected = stages
    time_mode = intent.get("time_mode")
    if time_mode == "early":
        selected = stages[:2]
    elif time_mode in {"recent", "current"}:
        selected = stages[-2:]
    else:
        selected = stages[:4]

    lines = ["【关系阶段】"]
    for stage in selected[:4]:
        if not isinstance(stage, dict):
            continue
        label = stage.get("label", "阶段")
        time_range = stage.get("time_range", "")
        summary = stage.get("summary", "")
        state = stage.get("relationship_state", "")
        line = f"- {label}"
        if time_range:
            line += f"（{time_range}）"
        if summary:
            line += f": {summary}"
        if state:
            line += f"；状态：{state}"
        lines.append(line[:500])
    return "\n".join(lines)
