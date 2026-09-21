"""Memory Agent — 检索 Semantic Memory + Conversation History + ImportAnalysis。

三轮检索：
  1. 时间匹配: 用户提到时间段 → 匹配 TimeChunk → 锁定 msg_index 范围
  2. 统一原文检索: Imported Chat + Online Chat，经各自 Adapter 搜索后合并
  3. 话题路由: 用户提到话题 → 查 TopicTag → 补充相关消息
"""
import json
import re

from django.db import close_old_connections

from ai.rag.reranker import Reranker
from ai.rag.query_rewriter import QueryRewriter
from ai.rag.compressor import ContextCompressor
from ai.memory.intent import detect_memory_intent
from ai.memory.history_search import ConversationHistorySearch
from ai.memory.import_access import can_access_imported_context
from ai.memory.conversation_summary import search_conversation_collapses
from ai.memory.semantic import expand_state_trajectories, search_semantic
from ai.time.time_anchor import annotate_relative_time_fact, find_unanchored_relative_time
from ai.tracing import record_trace
from storage.models.chat_message import ChatMessage
from storage.models.friend import Friend, Message
from storage.models.import_analysis import ImportAnalysis, TimeChunk, TopicTag


def _search_time_chunks(character_id: int, user_msg: str, plan: dict | None = None) -> dict | None:
    """第 1 轮：时间匹配 — 在 TimeChunk 中搜索用户提到的时间段。

    不依赖 LLM 猜的阶段名（如"暧昧期"），而是：
    1. 关键词匹配 chunk 的 summary（内容描述）→ 找到对应时间范围
    2. 时间方向词（"以前"/"最近"）→ 映射到早/晚期 chunk
    """
    # 时间方向词
    early_words = ["刚认识", "什么时候认识", "何时认识", "相识", "以前", "那会儿", "那时候", "当初", "最开始", "刚加", "第一次", "之前", "上次"]
    recent_words = ["最近", "前几天", "后来", "现在"]

    chunks = list(TimeChunk.objects.filter(character_id=character_id).order_by("start_msg_index"))
    if not chunks:
        return None
    anchor = str((plan or {}).get("temporal_anchor", "unknown"))
    if anchor in {"origin", "early"}:
        return _chunk_to_result(chunks[0])
    if anchor == "recent":
        return _chunk_to_result(chunks[-1])

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

    # Origin questions must use the first relationship period. Generic words
    # such as “什么/咱们” otherwise make an unrelated later chunk win.
    if any(w in user_msg for w in early_words):
        return _chunk_to_result(chunks[0])

    # 内容关键词匹配到了 → 直接返回
    if best_chunk and best_score >= 3:
        return _chunk_to_result(best_chunk)

    # 没匹配到内容，但用户说了时间方向词 → 取最近的 chunk
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


def _imported_anchor_evidence(
    character_id: int,
    time_scope: tuple[int, int] | None,
    plan: dict,
) -> dict | None:
    """Materialise a time-planned Imported Chat window as evidence."""
    if not character_id or not time_scope:
        return None
    queryset = ChatMessage.objects.filter(
        character_id=character_id,
        msg_index__gte=time_scope[0],
        msg_index__lte=time_scope[1],
    ).order_by("msg_index")
    if plan.get("temporal_anchor") == "recent":
        messages = list(queryset.order_by("-msg_index")[:8])
        messages.reverse()
    else:
        messages = list(queryset[:8])
    if not messages:
        return None
    return {
        "source_type": "import_chat",
        "message_refs": [message.msg_index for message in messages],
        "timestamp": messages[0].timestamp,
        "content": "\n".join(
            f"[{message.timestamp}] {message.sender}：{message.content}"
            for message in messages
        ),
        "score": 1.15,
    }


def _select_source_diverse_hits(
    ranked_hits: list[dict],
    plan: dict,
    anchor_evidence: dict | None = None,
    limit: int = 8,
) -> list[dict]:
    """Retain required source coverage after relevance reranking."""
    ordered: list[dict] = []
    seen: set[tuple] = set()

    def add(hit: dict):
        key = (hit.get("source_type"), tuple(hit.get("message_refs") or []))
        if key not in seen and len(ordered) < limit:
            seen.add(key)
            ordered.append(hit)

    import_hits = [hit for hit in ranked_hits if hit.get("source_type") == "import_chat"]
    online_hits = [hit for hit in ranked_hits if hit.get("source_type") == "online_chat"]
    policy = plan.get("source_policy", "any")
    if anchor_evidence and policy in {"import_preferred", "balanced"}:
        add(anchor_evidence)
    if policy == "import_preferred":
        for hit in import_hits[:2]: add(hit)
        for hit in online_hits[:2]: add(hit)
    elif policy == "balanced":
        for hit in import_hits[:2]: add(hit)
        for hit in online_hits[:2]: add(hit)
    elif policy == "online_preferred":
        for hit in online_hits[:3]: add(hit)
        for hit in import_hits[:1]: add(hit)
    for hit in ranked_hits:
        add(hit)
    return ordered


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


def _recent_dialogue_for_retrieval(messages: list, limit: int = 4) -> str:
    """Give the retrieval planner enough antecedent context for short follow-ups."""
    lines = []
    for message in list(messages[:-1])[-limit:]:
        content = getattr(message, "content", "")
        if not content:
            continue
        role = "AI" if getattr(message, "type", "") == "ai" else "用户"
        lines.append(f"{role}：{str(content)[:500]}")
    return "\n".join(lines)


def memory_agent_node(state: dict, api_key: str = "", api_base: str = "") -> dict:
    """Memory Agent — 三轮检索：时间匹配 → 混合检索 → 话题路由。

    套一层 try/finally 关数据库连接。这层是防御性的，不是在补一个正在漏的连接
    ——原因写在下面 _run_memory_agent 的 docstring 里，别照着想象改。
    """
    try:
        return _run_memory_agent(state, api_key, api_base)
    finally:
        close_old_connections()


def _run_memory_agent(state: dict, api_key: str = "", api_base: str = "") -> dict:
    """三轮检索：时间匹配 → 混合检索 → 话题路由。

    单独成函数，好让外面那层只管关连接。

    关于那层 finally 到底在防什么——它是**防御性**的，不是修一个正在漏的连接。
    实测：每个请求在 api/chat.py 新起线程里 asyncio.run(...)，LangGraph 用该
    loop 的默认 executor 派发同步节点，而 asyncio.run 退出时会 shutdown 这个
    executor 并 join 线程。所以节点线程随请求消亡，它身上的连接本来也不会长期驻留。

    真正无人关闭的是另一批线程：FastAPI/anyio 的工作线程（同步路由里的 ORM 读、
    SSE 生成器里的写、后台 daemon 线程）。它们跨请求复用，空闲 10s 才回收。
    根因是 Django 的 request_finished 挂在它自己的响应对象 close 上，而本应用是
    FastAPI + django.setup() 只用 ORM，WSGIHandler 只挂在 /admin——所以 /api/*
    既不触发 request_started 也不触发 request_finished。这句「每个请求结束就关掉」
    对链路上**任何**线程都是空话，不只是这条。

    在 SQLite 下代价很低（每线程一个句柄），换 Postgres 之前要处理那批线程。
    """
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
    # 缺字段一律当 "none"：supervisor_graph 的路由函数也是这个默认值，
    # 两边对「state 里没写」必须给出同一个答案。
    memory_kind = state.get("memory_kind", "none")
    friend = Friend.objects.select_related("character").filter(id=friend_id).first()
    imported_context_allowed = bool(friend and can_access_imported_context(friend))
    memory_intent = detect_memory_intent(user_msg)
    should_search_raw = (
        memory_kind == "recall"
        or memory_intent.get("needs_raw_chat", False)
        or memory_intent.get("needs_lightweight_recall", False)
    )
    queries = [user_msg]
    retrieval_plan = {
        "queries": queries,
        "temporal_anchor": memory_intent.get("time_mode", "unknown"),
        "source_policy": "any",
        "evidence_policy": "mixed",
    }
    # 为什么不止判 should_search_raw：下面算 semantic_reliable 用的是规划后的
    # query 查回来的结果，fact 必须先过规划器，那条回退判断才有依据。
    if should_search_raw or memory_kind == "fact":
        try:
            retrieval_plan = QueryRewriter(api_key, api_base).plan(
                user_msg,
                fallback_intent=memory_intent,
                imported_chat_available=imported_context_allowed,
                recent_dialogue=_recent_dialogue_for_retrieval(state.get("messages", [])),
            )
            queries = retrieval_plan["queries"][:3]
        except Exception:
            queries = [user_msg]
    if retrieval_plan.get("temporal_anchor") != "unknown":
        memory_intent["time_mode"] = retrieval_plan["temporal_anchor"]

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
    ranked_semantic_results = _rank_semantic_results(
        list(semantic_candidates.values()),
        memory_intent,
    )
    if memory_intent.get("needs_state_trajectory"):
        ranked_semantic_results = expand_state_trajectories(
            friend_id, ranked_semantic_results, limit=12
        )
    semantic_results = ranked_semantic_results[:12 if memory_intent.get("needs_state_trajectory") else 8]
    # 读取端加固：只含相对时间（"本周/当天"）的旧事实没有绝对日期锚点，
    # 直接注入会被当成当前事实。统一追加"可能已过期"注释，让模型知道该
    # 事实的时间信息不可靠，而不是断言"就这周嘛"。
    for item in semantic_results:
        item["fact"] = annotate_relative_time_fact(item.get("fact", ""))
    target_subject = memory_intent.get("target_subject", "mixed")
    category_hint = memory_intent.get("category_hint", "any")
    semantic_reliable = any(
        item.get("memory_state", "current") == "current"
        and (target_subject == "mixed" or item.get("subject") == target_subject)
        and (category_hint == "any" or item.get("category") == category_hint)
        for item in semantic_results[:5]
    )
    if memory_kind == "fact" and not semantic_reliable:
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

    trajectory_context = _format_state_trajectories(semantic_results)

    collapse_context = ""
    if friend_id and memory_intent.get("time_mode") in {
        "historical", "early", "recent", "specific_time",
    }:
        collapses = search_conversation_collapses(
            friend_id,
            user_msg,
            time_mode=memory_intent.get("time_mode", "any"),
            limit=3,
        )
        if collapses:
            lines = [
                f"- 在线对话 message_id {collapse.start_message_id}-{collapse.end_message_id}: {collapse.summary}"
                for collapse in collapses
            ]
            collapse_context = "【相关历史阶段胶囊】\n" + "\n".join(lines)

    # ── 第 1 轮：时间匹配 ──
    time_chunk = (
        _search_time_chunks(character_id, user_msg, retrieval_plan)
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
    history_evidence_refs: list[dict] = []
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
        anchor_evidence = (
            _imported_anchor_evidence(character_id, time_scope, retrieval_plan)
            if imported_context_allowed
            and retrieval_plan.get("source_policy") in {"import_preferred", "balanced"}
            else None
        )
        history_hits = reranker.rerank(user_msg, candidates, top_k=8)
        history_hits = _select_source_diverse_hits(
            history_hits, retrieval_plan, anchor_evidence, limit=8
        )
        history_evidence_refs = [
            {
                "source_type": hit.get("source_type"),
                "message_refs": hit.get("message_refs", []),
                "timestamp": hit.get("timestamp", ""),
            }
            for hit in history_hits[:5]
        ]
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

    # Build separately budgetable sections.  Conversation Agent selects from
    # these according to the current question before applying its final cap.
    sections: list[dict[str, str]] = []
    if time_context:
        sections.append({"kind": "time_scope", "text": time_context})
    if trajectory_context:
        sections.append({"kind": "trajectory", "text": trajectory_context})
    if collapse_context:
        sections.append({"kind": "collapse", "text": collapse_context})
    semantic_parts: list[str] = []
    if user_facts:
        semantic_parts.append("【用户记忆】\n" + "\n".join(f"- {f}" for f in user_facts))
    if girlfriend_facts:
        semantic_parts.append("【女友自我记忆】\n" + "\n".join(f"- {f}" for f in girlfriend_facts))
    if relationship_experiences:
        semantic_parts.append("【共同经历】\n" + "\n".join(f"- {f}" for f in relationship_experiences))
    if relationship_patterns:
        semantic_parts.append("【关系互动规律】\n" + "\n".join(f"- {f}" for f in relationship_patterns))
    if semantic_parts:
        sections.append({"kind": "semantic", "text": "\n\n".join(semantic_parts)})
    if history_context:
        sections.append({"kind": "raw_evidence", "text": "【相关聊天原文】\n" + history_context})
    if topic_messages:
        sections.append({"kind": "topic", "text": topic_messages})

    # 注入关系演变概览（宏观）
    needs_relationship_overview = (
        memory_intent.get("target_subject") == "relationship"
        or memory_intent.get("category_hint") == "relationship"
    )
    relationship_overview_context = ""
    if character_id and needs_relationship_overview and imported_context_allowed:
        analysis = ImportAnalysis.objects.filter(character_id=character_id, status="done").first()
        if analysis and analysis.relationship_overview:
            overview_parts = [analysis.relationship_overview]
            timeline_context = _timeline_context_for_intent(analysis, memory_intent)
            if timeline_context:
                overview_parts.append(timeline_context)
            relationship_overview_context = f"【关系演变概览】\n{chr(10).join(overview_parts)}"
            sections.append({"kind": "relationship_overview", "text": relationship_overview_context})

    # Fallback for callers that do not yet understand memory_sections.
    context = "\n\n".join(section["text"] for section in sections)

    result = {
        "memory_context": context,
        "memory_sections": sections,
        "memory_intent": memory_intent,
        "retrieval_plan": retrieval_plan,
        "semantic_facts": semantic_facts,
        "reply_provenance": {
            **(state.get("reply_provenance") or {}),
            "retrieved_raw": [
                {
                    "source_type": hit.get("source_type", ""),
                    "message_refs": hit.get("message_refs", [])[:30],
                    "timestamp": hit.get("timestamp", ""),
                    "excerpt": hit.get("content", "")[:3000],
                }
                for hit in history_hits[:5]
            ],
            "memory_intent": memory_intent,
            "retrieval_plan": retrieval_plan,
            "semantic_facts": [
                {
                    "id": item["id"], "fact": item["fact"],
                    "subject": item.get("subject"), "category": item.get("category"),
                }
                for item in semantic_results
            ],
        },
    }
    record_trace(
        "memory_agent.retrieval",
        {
            "user_msg": user_msg,
            "friend_id": friend_id,
            "character_id": character_id,
            "memory_intent": memory_intent,
            "retrieval_plan": retrieval_plan,
            "should_search_raw": should_search_raw,
            "semantic_reliable": semantic_reliable,
            "imported_context_allowed": imported_context_allowed,
            "queries": queries,
            "time_chunk": time_chunk,
            "time_scope": time_scope,
            "semantic_results": semantic_results,
            "trajectory_context": trajectory_context,
            "collapse_context": collapse_context,
            "relationship_overview_context": relationship_overview_context,
            "memory_sections": sections,
            "history_hits": history_hits,
            "history_context": history_context,
            "history_evidence_refs": history_evidence_refs,
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
        # 相对时间事实无法回答"具体哪一天"类问题，且内容会随时间过期，
        # 排序时降权，让带绝对日期的事实优先被选中注入。
        if find_unanchored_relative_time(item.get("fact", "")):
            value -= 0.8
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


def _format_state_trajectories(results: list[dict]) -> str:
    groups: dict[str, list[dict]] = {}
    for item in results:
        key = item.get("trajectory_key", "")
        if key:
            groups.setdefault(key, []).append(item)
    if not groups:
        return ""
    lines = ["【状态演变】"]
    for key, items in groups.items():
        if len(items) < 2:
            continue
        lines.append(f"- {key}：")
        for item in sorted(
            items,
            key=lambda value: (
                value.get("valid_from").isoformat()
                if getattr(value.get("valid_from"), "isoformat", None)
                else "",
                value["id"],
            ),
        ):
            state = "当前" if item.get("memory_state") == "current" else "历史"
            lines.append(f"  - [{state}] {item['fact']}")
    return "\n".join(lines) if len(lines) > 1 else ""


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
