# ai/agents/conversation_agent.py
import os
from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END


def conversation_agent_node(state: dict, api_key: str = "", api_base: str = "") -> dict:
    """Conversation Agent — 生成最终回复。整合角色设定+记忆+情绪分析。
    输出: messages (追加 AI 回复)
    """
    llm = ChatOpenAI(
        model="deepseek-v4-pro",
        openai_api_key=api_key or os.getenv("API_KEY"),
        openai_api_base=api_base or os.getenv("API_BASE"),
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
        "1. 你无法调用任何工具或搜索功能，相关的聊天记录已经在【记忆上下文】中提供\n"
        "2. 基于提供的上下文和角色设定直接回复，严禁编造没有在上下文中出现的事实\n"
        "3. 如果上下文中没有相关信息，就按角色性格自然地回应，不要假装搜索或调用函数"
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
