# ai/agents/memory_agent.py
"""Memory Agent — 检索 Semantic Memory + Chat History，组织上下文注入给 Conversation Agent。

不再检索 Episodic Memory（Episodic 退化为 Reflection 的原料缓冲区）。
"""
from ai.rag.retriever import HybridRetriever
from ai.rag.reranker import Reranker
from ai.memory.semantic import search_semantic
from web.models.chat_message import ChatMessage


def _build_context_snippets(character_id: int, matched_ids: set[int], chat_sender_name: str, char_name: str, context_window: int = 5) -> list[str]:
    """根据匹配的 ChatMessage rowid 拉取上下文窗口，拼接成对话片段"""
    if not matched_ids or not chat_sender_name:
        return []

    msg_indices: set[int] = set()
    for cm in ChatMessage.objects.filter(character_id=character_id, id__in=matched_ids).values("msg_index"):
        msg_indices.add(cm["msg_index"])

    all_indices: set[int] = set()
    for mi in msg_indices:
        for offset in range(-context_window, context_window + 1):
            all_indices.add(mi + offset)

    context_msgs = list(
        ChatMessage.objects.filter(character_id=character_id, msg_index__in=all_indices).order_by("msg_index")[:200]
    )
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
    """Memory Agent — 检索 Semantic Memory + Chat History，组织上下文"""
    user_msg = ""
    for msg in reversed(state.get("messages", [])):
        if hasattr(msg, "content"):
            user_msg = msg.content
            break

    if not user_msg:
        return {"memory_context": "", "semantic_facts": state.get("semantic_facts", [])}

    retriever = HybridRetriever(api_key, api_base)
    reranker = Reranker(api_key, api_base)

    # 1. Search Semantic Memory — 只搜跟当前消息语义相关的
    friend_id = state.get("friend_id", 0)
    semantic_results = search_semantic(friend_id, user_msg, top_k=10)
    semantic_facts = [r["fact"] for r in semantic_results]

    # 2. Hybrid search (FTS5 + LanceDB) on ChatMessage
    character_id = state.get("character_id")
    wechat_context = ""
    if character_id:
        candidates = retriever.hybrid_search(user_msg, character_id, top_k=30, use_hyde=False)
        candidates = reranker.rerank(user_msg, candidates, top_k=10)

        matched_ids: set[int] = set()
        for c in candidates:
            rowid = c.get("rowid")
            if rowid:
                matched_ids.add(int(rowid))

        if matched_ids:
            char_name = state.get("character_name", "")
            chat_sender_name = state.get("chat_sender_name", "")
            snippets = _build_context_snippets(character_id, matched_ids, chat_sender_name, char_name)
            wechat_context = "\n---\n".join(snippets[:5]) if snippets else ""

        if not wechat_context:
            wechat_context = "\n".join(c["content"][:400] for c in candidates[:3])

    # 3. Build context (no Episodic — covered by Semantic + Chat History)
    parts: list[str] = []
    if semantic_facts:
        parts.append("【关于用户的已知事实】\n" + "\n".join(f"- {f}" for f in semantic_facts))

    if wechat_context:
        parts.append("【相关聊天记录（含上下文）】\n" + wechat_context)

    context = "\n\n".join(parts)

    return {"memory_context": context, "semantic_facts": semantic_facts}
