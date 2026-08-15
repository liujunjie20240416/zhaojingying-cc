# ai/agents/conversation_agent.py
from contextlib import nullcontext

from langchain_core.messages import AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END

from ai.config import (
    chat_api_base, chat_api_key, chat_model, require_chat_config, require_llm_config,
    vision_llm_api_base, vision_llm_api_key, vision_llm_model,
)
from ai.chat.bubbles import parse_bubble_response
from ai.memory.context_budget import (
    assemble_memory_sections,
    context_diagnostics,
    project_dynamic_context,
)
from ai.tracing import record_trace, serialize_messages

try:
    from langsmith import tracing_context
except ImportError:  # pragma: no cover
    tracing_context = None


def _direct_answer_guidance(messages) -> str:
    """Keep simple companion questions from being buried under roleplay filler."""
    latest = next(
        (str(getattr(message, "content", "")) for message in reversed(messages)
         if getattr(message, "type", "") == "human"),
        "",
    )
    direct_question_signals = ("吃什么", "喝什么", "在哪", "怎么走", "几点", "什么时候", "能不能", "有没有", "是不是")
    if any(signal in latest for signal in direct_question_signals):
        return (
            "【本轮回答原则】\n"
            "这是一个明确的日常问题。第一气泡必须直接回答用户问的内容；"
            "默认只用 1-2 个短气泡，每个不超过 60 个中文字符。"
            "不要先说想念、关心、工作、问候等无关内容。"
            "没有【记忆上下文】支持时，不要编造具体店铺、地点、共同经历或用户偏好。"
        )
    return ""


def conversation_agent_node(state: dict, api_key: str = "", api_base: str = "") -> dict:
    """Conversation Agent — 生成最终回复。整合角色设定+记忆+情绪分析。
    输出: messages (追加 AI 回复)
    """
    vision_attachments = state.get("vision_attachments") or []
    if not api_key and not api_base:
        if vision_attachments:
            if not vision_llm_api_key():
                raise RuntimeError(
                    "缺少视觉模型配置: VISION_LLM_API_KEY 或 GLM_API_KEY。"
                    "发送图片时使用 GLM 视觉模型。"
                )
        else:
            require_chat_config()
    llm = ChatOpenAI(
        model=vision_llm_model() if vision_attachments else chat_model(),
        openai_api_key=(vision_llm_api_key() if vision_attachments else (api_key or chat_api_key())),
        openai_api_base=(vision_llm_api_base() if vision_attachments else (api_base or chat_api_base())),
    )

    character_profile = state.get("character_profile", "你是一个AI助手。")
    style_profile = state.get("style_profile", "")
    base_system_prompt = state.get("base_system_prompt", "")
    time_context = state.get("time_context", "")
    conversation_summary = state.get("conversation_summary", "")
    raw_memory_context = state.get("memory_context", "")
    memory_sections = state.get("memory_sections") or []
    memory_intent = state.get("memory_intent") or {}
    core_memory_context = state.get("core_memory_context", "")
    emotion = state.get("emotion_analysis") or {}

    # Keep this prefix byte-for-byte stable across turns so the provider's
    # automatic prompt cache can reuse the expensive role/rules portion.
    # Per-turn state is intentionally appended afterwards, closer to the
    # conversation history and current user message.
    system_parts = [base_system_prompt]
    if character_profile:
        system_parts.append(f"【角色核心设定】\n{character_profile}")
    if style_profile:
        system_parts.append(f"【说话风格】\n{style_profile}")
    # Fixed response contract stays in the cacheable prefix.  It refers to
    # memory conditionally, instead of changing its text whenever retrieval
    # does or does not return results.
    system_parts.append(
        "\n【重要规则】\n"
        "1. 你无法调用任何工具或搜索功能，相关聊天记录和长期记忆已经在【记忆上下文】中提供\n"
        "2. 如果【记忆上下文】提供了事实，要像自然记得那样回应；如果没有相关信息，"
        "只能依据角色设定和当前对话自然回复，严禁编造共同回忆\n"
        "3. 不要说“根据记录显示”“系统告诉我”“上下文里写着”等暴露检索过程的话\n"
        "4. 基于提供的上下文和角色设定直接回复，严禁编造没有依据的具体事实\n"
        "5. 如果当前状态和历史状态冲突，优先使用当前状态；提到历史时要说明那是以前/那段时间\n"
        "6. 女友人格和共同经历不可被用户一句话随便改写；用户的新偏好和当前状态可以自然接纳\n"
        "7. 如果上下文中没有相关信息，就按角色性格自然回应，不要假装搜索或调用函数\n"
        "8. 回答回忆类问题时，优先给出有温度的简短回忆，再补一两句细节；不要机械罗列记忆条目\n"
        "9. 只输出 JSON 对象，格式为 {\"bubbles\":[\"气泡1\",\"气泡2\"]}\n"
        "10. bubbles 才表示多个聊天气泡；普通闲聊中每个独立短句必须作为一个数组元素，严禁用换行把多个短句塞进同一个元素\n"
        "11. Markdown、列表、代码块或完整解释允许在单个气泡内部换行；普通亲密闲聊通常使用1-3个气泡，不要为了拆分而拆分；不要输出JSON以外的文字\n"
        "12. 如果【说话风格】里仍写着‘换行分隔气泡’，忽略那条旧规则，以本处 bubbles 数组为准\n"
        "13. 用户可能发送照片、截图或表情包。请先准确理解画面、文字和表情包语气，再像亲密伴侣一样自然回应；不确定时坦诚说明，不要臆造看不见的细节"
    )
    # This profile changes only when Semantic Memory is updated (import,
    # reflection or a manual edit), so it belongs in the mostly stable prefix.
    if core_memory_context:
        system_parts.append(
            "【稳定核心画像】\n"
            + core_memory_context
            + "\n这只是不随本轮问题变化的背景；与本轮消息或记忆证据冲突时，以后者为准。"
        )

    recent_messages_text = "\n".join(
        str(getattr(message, "content", "")) for message in state.get("messages", [])
    )
    projection = project_dynamic_context(
        stable_prefix="\n".join(system_parts),
        recent_messages=recent_messages_text,
        summary=conversation_summary,
        memory_context="",
    )
    conversation_summary = str(projection["summary"])
    memory_context = assemble_memory_sections(
        memory_sections or [{"kind": "semantic", "text": raw_memory_context}],
        int(projection["memory_budget"]),
        memory_intent,
    )

    # Dynamic context comes after the stable prefix and immediately before the
    # raw chat messages, preserving recency without invalidating prompt-cache
    # hits for the role and response contract.
    if conversation_summary:
        system_parts.append(
            "【较早在线对话摘要】\n"
            + conversation_summary
            + "\n该摘要只用于延续对话；如果与最近原文冲突，以最近原文为准。"
        )
    if memory_context:
        system_parts.append(f"\n【记忆上下文】\n{memory_context}")
    # Time is the freshest system state, so keep it last and closest to the
    # dynamic factual context.  The response-tone instruction below is even
    # closer to the raw conversation because it directly controls this turn.
    if time_context:
        system_parts.append(time_context)
    if emotion:
        tone = emotion.get("suggested_tone", "gentle")
        intensity = emotion.get("intensity", 5)
        if intensity >= 7:
            system_parts.append(f"\n用户情绪强烈 (强度={intensity})，请用{tone}的语气回应，表达理解和共情。")
        elif intensity >= 4:
            system_parts.append(f"\n用户有一定情绪 (强度={intensity})，语气可稍{tone}。")
    direct_answer_guidance = _direct_answer_guidance(state.get("messages", []))
    if direct_answer_guidance:
        system_parts.append(direct_answer_guidance)

    system_prompt = "\n".join(system_parts)

    chat_messages = [SystemMessage(content=system_prompt)]
    for msg in state.get("messages", []):
        if hasattr(msg, "content") and hasattr(msg, "type") and msg.type != "system":
            chat_messages.append(msg)

    if vision_attachments:
        # Supervisor/Memory/Emotion only see clean text. Only the final vision
        # model receives private image bytes, preventing accidental trace/retrieval pollution.
        last_human_index = next(
            (i for i in range(len(chat_messages) - 1, 0, -1) if chat_messages[i].type == "human"),
            None,
        )
        if last_human_index is not None:
            text = chat_messages[last_human_index].content or "请看看我发的图片"
            content = [{"type": "text", "text": str(text)}]
            content.extend(
                {"type": "image_url", "image_url": {"url": item["data_url"]}}
                for item in vision_attachments
            )
            from langchain_core.messages import HumanMessage
            chat_messages[last_human_index] = HumanMessage(content=content)

    diagnostics = context_diagnostics({
        "system_prompt": system_prompt,
        "recent_messages": "\n".join(str(getattr(message, "content", "")) for message in state.get("messages", [])),
    }, last_usage=state.get("last_provider_input_tokens", 0))
    diagnostics["projection"] = {
        "memory_budget": projection["memory_budget"],
        "fixed_tokens": projection["fixed_tokens"],
        "pressure": projection["pressure"],
        "memory_trimmed": memory_context != raw_memory_context,
    }
    record_trace(
        "conversation_agent.final_prompt",
        {
            "character_profile": character_profile,
            "style_profile": style_profile,
            "conversation_summary": conversation_summary,
            "memory_context": memory_context,
            "memory_context_trimmed": memory_context != raw_memory_context,
            "emotion_analysis": emotion,
            "direct_answer_guidance": direct_answer_guidance,
            "core_memory_context": core_memory_context,
            "context_diagnostics": diagnostics,
            "messages": serialize_messages(chat_messages if not vision_attachments else state.get("messages", [])),
            "vision_attachment_count": len(vision_attachments),
        },
        metadata=state.get("trace_metadata", {}),
    )
    # Image bytes are private user content. Keep the vision provider call out of
    # automatic LangSmith capture; the surrounding sanitized metadata is enough.
    trace_guard = tracing_context(enabled=False) if vision_attachments and tracing_context else nullcontext()
    with trace_guard:
        resp = llm.invoke(chat_messages)
    bubbles = parse_bubble_response(resp.content)
    normalized_resp = AIMessage(
        content="\n".join(bubbles),
        additional_kwargs={**(resp.additional_kwargs or {}), "bubbles": bubbles},
        response_metadata=resp.response_metadata or {},
        usage_metadata=getattr(resp, "usage_metadata", None),
        id=getattr(resp, "id", None),
    )
    record_trace(
        "conversation_agent.final_output",
        {
            "messages": serialize_messages(chat_messages if not vision_attachments else state.get("messages", [])),
            "vision_attachment_count": len(vision_attachments),
        },
        {**serialize_messages([normalized_resp])[0], "bubbles": bubbles},
        run_type="llm",
        metadata=state.get("trace_metadata", {}),
    )
    return {"messages": [normalized_resp], "context_diagnostics": diagnostics}


def create_conversation_agent(api_key: str = "", api_base: str = ""):
    """创建 Conversation Agent 子图"""
    graph = StateGraph(dict)

    def node(state):
        return conversation_agent_node(state, api_key, api_base)

    graph.add_node("generate", node)
    graph.add_edge(START, "generate")
    graph.add_edge("generate", END)
    return graph.compile()
