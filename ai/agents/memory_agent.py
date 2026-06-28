"""Memory Agent — 检索 Semantic Memory + Chat History + ImportAnalysis，组织上下文注入给 Conversation Agent。

三轮检索：
  1. 时间匹配: 用户提到时间段 → 匹配 TimeChunk → 锁定 msg_index 范围
  2. 范围内混合检索: FTS5 + LanceDB 在时间范围内搜索
  3. 话题路由: 用户提到话题 → 查 TopicTag → 补充相关消息
"""
import json
import re

from ai.rag.retriever import HybridRetriever
from ai.rag.reranker import Reranker
from ai.memory.intent import detect_memory_intent
from ai.memory.semantic import search_semantic
from web.models.chat_message import ChatMessage
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


def _build_context_snippets(
    character_id: int,
    matched_ids: set[int],
    chat_sender_name: str,
    char_name: str,
    context_window: int = 5,
    time_scope: tuple | None = None,
) -> list[str]:
    """根据匹配的 ChatMessage rowid 拉取上下文窗口，拼接成对话片段。

    如果提供了 time_scope (start, end)，只在范围内检索。
    """
    if not matched_ids or not chat_sender_name:
        return []

    msg_indices: set[int] = set()
    base_qs = ChatMessage.objects.filter(character_id=character_id)
    if time_scope:
        base_qs = base_qs.filter(msg_index__gte=time_scope[0], msg_index__lte=time_scope[1])

    for cm in base_qs.filter(id__in=matched_ids).values("msg_index"):
        msg_indices.add(cm["msg_index"])

    all_indices: set[int] = set()
    for mi in msg_indices:
        for offset in range(-context_window, context_window + 1):
            all_indices.add(mi + offset)

    qs = ChatMessage.objects.filter(character_id=character_id)
    if time_scope:
        qs = qs.filter(msg_index__gte=time_scope[0], msg_index__lte=time_scope[1])
    context_msgs = list(qs.filter(msg_index__in=all_indices).order_by("msg_index")[:200])
    context_msgs.sort(key=lambda m: m.msg_index)

    snippets: list[list] = []
    current_snippet: list = []
    last_idx: int | None = None
    for m in context_msgs:
        if last_idx is not None and m.msg_index - last_idx > context_window * 2:
            if current_snippet:
                snippets.append(current_snippet)
            current_snippet = [m]
        else:
            current_snippet.append(m)
        last_idx = m.msg_index
    if current_snippet:
        snippets.append(current_snippet)

    result: list[str] = []
    for snippet in snippets[:3]:
        lines = []
        for m in snippet:
            if m.sender == chat_sender_name:
                lines.append(f"[{m.timestamp}] {char_name}：{m.content}")
            else:
                lines.append(f"[{m.timestamp}] 对方：{m.content}")
        result.append("\n".join(lines))
    return result


def memory_agent_node(state: dict, api_key: str = "", api_base: str = "") -> dict:
    """Memory Agent — 三轮检索：时间匹配 → 混合检索 → 话题路由"""
    user_msg = ""
    for msg in reversed(state.get("messages", [])):
        if hasattr(msg, "content"):
            user_msg = msg.content
            break

    if not user_msg:
        return {"memory_context": "", "semantic_facts": state.get("semantic_facts", [])}

    retriever = HybridRetriever(api_key, api_base)
    reranker = Reranker(api_key, api_base)
    character_id = state.get("character_id")
    friend_id = state.get("friend_id", 0)
    memory_intent = detect_memory_intent(user_msg)

    # 1. Search Semantic Memory
    semantic_results = _rank_semantic_results(
        search_semantic(friend_id, user_msg, top_k=20),
        memory_intent,
    )[:10]
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
    time_chunk = _search_time_chunks(character_id, user_msg) if character_id else None
    time_scope = None
    time_context = ""
    if time_chunk:
        time_scope = (time_chunk["start_msg_index"], time_chunk["end_msg_index"])
        time_context = f"【时间段】{time_chunk['label']}: {time_chunk['summary']}\n"

    # ── 第 2 轮：混合检索（有 time_scope 则缩小范围） ──
    wechat_context = ""
    if character_id:
        # FTS5 + LanceDB 混合检索
        candidates = retriever.hybrid_search(user_msg, character_id, top_k=30, use_hyde=False)

        # 如果有 time_scope，过滤不在范围内的结果
        if time_scope:
            candidates = [
                c for c in candidates
                if not c.get("rowid") or _rowid_in_scope(c["rowid"], character_id, time_scope)
            ]

        candidates = reranker.rerank(user_msg, candidates, top_k=10)

        matched_ids: set[int] = set()
        for c in candidates:
            rowid = c.get("rowid")
            if rowid:
                matched_ids.add(int(rowid))

        if matched_ids:
            char_name = state.get("character_name", "")
            chat_sender_name = state.get("chat_sender_name", "")
            snippets = _build_context_snippets(
                character_id, matched_ids, chat_sender_name, char_name,
                time_scope=time_scope,
            )
            wechat_context = "\n---\n".join(snippets[:5]) if snippets else ""

        if not wechat_context and candidates:
            wechat_context = "\n".join(c["content"][:400] for c in candidates[:3])

    # ── 第 3 轮：话题路由 ──
    topic_indices = _search_topic_tags(character_id, user_msg) if character_id else []
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
    if wechat_context:
        parts.append("【相关聊天记录】\n" + wechat_context)
    if topic_messages:
        parts.append(topic_messages)

    context = "\n\n".join(parts)

    # 注入关系演变概览（宏观）
    if character_id:
        analysis = ImportAnalysis.objects.filter(character_id=character_id, status="done").first()
        if analysis and analysis.relationship_overview:
            overview_parts = [analysis.relationship_overview]
            timeline_context = _timeline_context_for_intent(analysis, memory_intent)
            if timeline_context:
                overview_parts.append(timeline_context)
            context = f"【关系演变概览】\n{chr(10).join(overview_parts)}\n\n" + context

    return {
        "memory_context": context,
        "semantic_facts": semantic_facts,
    }


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


def _rowid_in_scope(rowid: int, character_id: int, time_scope: tuple) -> bool:
    """检查 ChatMessage rowid 对应的 msg_index 是否在 time_scope 范围内"""
    try:
        cm = ChatMessage.objects.filter(character_id=character_id, id=rowid).values("msg_index").first()
        if cm:
            return time_scope[0] <= cm["msg_index"] <= time_scope[1]
    except Exception:
        pass
    return True  # 查不到时就不过滤，宁可多返回
