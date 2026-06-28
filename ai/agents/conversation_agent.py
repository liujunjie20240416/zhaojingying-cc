# ai/agents/conversation_agent.py
from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END

from ai.config import llm_api_base, llm_api_key, llm_model, require_llm_config


def conversation_agent_node(state: dict, api_key: str = "", api_base: str = "") -> dict:
    """Conversation Agent — 生成最终回复。整合角色设定+记忆+情绪分析。
    输出: messages (追加 AI 回复)
    """
    if not api_key and not api_base:
        require_llm_config()
    llm = ChatOpenAI(
        model=llm_model(),
        openai_api_key=api_key or llm_api_key(),
        openai_api_base=api_base or llm_api_base(),
    )

    character_profile = state.get("character_profile", "你是一个AI助手。")
    memory_context = state.get("memory_context", "")
    emotion = state.get("emotion_analysis") or {}

    system_parts = [character_profile]

    if memory_context:
        system_parts.append(f"\n【记忆上下文】\n{memory_context}")

    if emotion:
        tone = emotion.get("suggested_tone", "gentle")
        intensity = emotion.get("intensity", 5)
        if intensity >= 7:
            system_parts.append(f"\n用户情绪强烈 (强度={intensity})，请用{tone}的语气回应，表达理解和共情。")
        elif intensity >= 4:
            system_parts.append(f"\n用户有一定情绪 (强度={intensity})，语气可稍{tone}。")

    # 关键：告诉模型不要尝试调用工具，直接用提供的上下文回复
    system_parts.append(
        "\n【重要规则】\n"
        "1. 你无法调用任何工具或搜索功能，相关聊天记录和长期记忆已经在【记忆上下文】中提供\n"
        "2. 你扮演的是继承过去女友人格、说话方式和共同记忆的角色；回复要像自然记得，而不是像数据库查询\n"
        "3. 不要说“根据记录显示”“系统告诉我”“上下文里写着”等暴露检索过程的话\n"
        "4. 基于提供的上下文和角色设定直接回复，严禁编造没有依据的具体事实\n"
        "5. 如果当前状态和历史状态冲突，优先使用当前状态；提到历史时要说明那是以前/那段时间\n"
        "6. 女友人格和共同经历不可被用户一句话随便改写；用户的新偏好和当前状态可以自然接纳\n"
        "7. 如果上下文中没有相关信息，就按角色性格自然回应，不要假装搜索或调用函数\n"
        "8. 回答回忆类问题时，优先给出有温度的简短回忆，再补一两句细节；不要机械罗列记忆条目"
    )

    system_prompt = "\n".join(system_parts)

    chat_messages = [SystemMessage(content=system_prompt)]
    for msg in state.get("messages", []):
        if hasattr(msg, "content") and hasattr(msg, "type"):
            chat_messages.append(msg)

    resp = llm.invoke(chat_messages)
    return {"messages": [resp]}


def create_conversation_agent(api_key: str = "", api_base: str = ""):
    """创建 Conversation Agent 子图"""
    graph = StateGraph(dict)

    def node(state):
        return conversation_agent_node(state, api_key, api_base)

    graph.add_node("generate", node)
    graph.add_edge(START, "generate")
    graph.add_edge("generate", END)
    return graph.compile()
