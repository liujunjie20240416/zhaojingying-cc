# ai/agents/conversation_agent.py
from contextlib import nullcontext

from langchain_core.messages import AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END

from ai.config import (
    llm_api_base, llm_api_key, llm_model, require_llm_config,
    vision_llm_api_base, vision_llm_api_key, vision_llm_model,
)
from ai.chat.bubbles import parse_bubble_response
from ai.tracing import record_trace, serialize_messages

try:
    from langsmith import tracing_context
except ImportError:  # pragma: no cover
    tracing_context = None


def conversation_agent_node(state: dict, api_key: str = "", api_base: str = "") -> dict:
    """Conversation Agent — 生成最终回复。整合角色设定+记忆+情绪分析。
    输出: messages (追加 AI 回复)
    """
    if not api_key and not api_base:
        require_llm_config()
    vision_attachments = state.get("vision_attachments") or []
    llm = ChatOpenAI(
        model=vision_llm_model() if vision_attachments else llm_model(),
        openai_api_key=(vision_llm_api_key() if vision_attachments else (api_key or llm_api_key())),
        openai_api_base=(vision_llm_api_base() if vision_attachments else (api_base or llm_api_base())),
    )

    character_profile = state.get("character_profile", "你是一个AI助手。")
    style_profile = state.get("style_profile", "")
    base_system_prompt = state.get("base_system_prompt", "")
    time_context = state.get("time_context", "")
    conversation_summary = state.get("conversation_summary", "")
    memory_context = state.get("memory_context", "")
    emotion = state.get("emotion_analysis") or {}

    system_parts = [base_system_prompt]
    if character_profile:
        system_parts.append(f"【角色核心设定】\n{character_profile}")
    if style_profile:
        system_parts.append(f"【说话风格】\n{style_profile}")
    if time_context:
        system_parts.append(time_context)
    if conversation_summary:
        system_parts.append(
            "【较早在线对话摘要】\n"
            + conversation_summary
            + "\n该摘要只用于延续对话；如果与最近原文冲突，以最近原文为准。"
        )

    if memory_context:
        system_parts.append(f"\n【记忆上下文】\n{memory_context}")

    if emotion:
        tone = emotion.get("suggested_tone", "gentle")
        intensity = emotion.get("intensity", 5)
        if intensity >= 7:
            system_parts.append(f"\n用户情绪强烈 (强度={intensity})，请用{tone}的语气回应，表达理解和共情。")
        elif intensity >= 4:
            system_parts.append(f"\n用户有一定情绪 (强度={intensity})，语气可稍{tone}。")

    memory_rule = (
        "2. 你扮演的是继承过去女友人格、说话方式和共同记忆的角色；回复要像自然记得，而不是像数据库查询"
        if memory_context
        else "2. 当前没有可用的导入聊天记录或长期记忆；只能依据角色设定和当前对话自然回复，不要编造共同回忆"
    )

    # 关键：告诉模型不要尝试调用工具，直接用提供的上下文回复
    system_parts.append(
        "\n【重要规则】\n"
        "1. 你无法调用任何工具或搜索功能，相关聊天记录和长期记忆已经在【记忆上下文】中提供\n"
        f"{memory_rule}\n"
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

    record_trace(
        "conversation_agent.final_prompt",
        {
            "character_profile": character_profile,
            "style_profile": style_profile,
            "conversation_summary": conversation_summary,
            "memory_context": memory_context,
            "emotion_analysis": emotion,
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
    return {"messages": [normalized_resp]}


def create_conversation_agent(api_key: str = "", api_base: str = ""):
    """创建 Conversation Agent 子图"""
    graph = StateGraph(dict)

    def node(state):
        return conversation_agent_node(state, api_key, api_base)

    graph.add_node("generate", node)
    graph.add_edge(START, "generate")
    graph.add_edge("generate", END)
    return graph.compile()
