# ai/agents/supervisor_graph.py
"""Supervisor Graph — Multi-Agent 主编排图。

编排流程:
    START → Supervisor → 路由
        ├── "emotional" → Emotion → Memory → Conversation → END
        └── default     → Memory → Emotion? → Conversation → END
"""
from typing import TypedDict, Annotated, Sequence, NotRequired
from langchain_core.messages import BaseMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph import add_messages

from ai.agents.supervisor import supervisor_node
from ai.agents.memory_agent import memory_agent_node
from ai.agents.emotion_agent import emotion_agent_node
from ai.agents.conversation_agent import conversation_agent_node


class MultiAgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    intent: str
    delegate_to: str
    memory_context: str
    emotion_analysis: dict | None
    character_profile: str
    style_profile: str
    base_system_prompt: str
    time_context: str
    conversation_summary: str
    character_name: str
    chat_sender_name: str
    semantic_facts: list[str]
    friend_id: int
    character_id: int | None
    trace_metadata: NotRequired[dict]
    emotion_context: NotRequired[list]
    vision_attachments: NotRequired[list]
    memory_done: NotRequired[bool]
    emotion_done: NotRequired[bool]


STRONG_EMOTION_SIGNALS = [
    "难过", "伤心", "哭", "崩溃", "绝望", "害怕", "焦虑",
    "开心死", "激动", "太棒", "兴奋", "生气", "愤怒", "烦",
    "累死", "压力", "撑不住",
]


def _last_user_msg(state: dict) -> str:
    for msg in reversed(state.get("messages", [])):
        if hasattr(msg, "content") and not hasattr(msg, "tool_calls"):
            return getattr(msg, "content", "")
    return ""


def wrap_supervisor(state: dict) -> dict:
    return supervisor_node(state)


def wrap_memory(state: dict) -> dict:
    return {**memory_agent_node(state), "memory_done": True}


def wrap_emotion(state: dict) -> dict:
    return {**emotion_agent_node(state), "emotion_done": True}


def wrap_conversation(state: dict) -> dict:
    return conversation_agent_node(state)


def route_from_supervisor(state: dict) -> str:
    intent = state.get("intent", "chat")
    # 强烈情绪优先；普通闲聊/时间直接回复；记忆类才检索。
    if intent == "emotional":
        return "emotion"
    if intent in {"recall", "memory"}:
        return "memory"
    return "conversation"


def route_after_memory(state: dict) -> str:
    if state.get("emotion_done"):
        return "conversation"
    user_msg = _last_user_msg(state)
    for signal in STRONG_EMOTION_SIGNALS:
        if signal in user_msg:
            return "emotion"
    return "conversation"


def route_after_emotion(state: dict) -> str:
    if state.get("memory_done"):
        return "conversation"
    intent = state.get("intent", "")
    user_msg = _last_user_msg(state)
    recall_signals = ["记得", "以前", "那次", "第一次", "上次", "什么时候"]
    if intent == "recall" or any(s in user_msg for s in recall_signals):
        return "memory"
    return "conversation"


def create_supervisor_app(
    friend_id: int = 0,
    character_id: int | None = None,
    character_name: str = "",
    character_profile: str = "",
    api_key: str = "",
    api_base: str = "",
):
    """创建完整的 Multi-Agent 对话图"""
    graph = StateGraph(MultiAgentState)

    graph.add_node("supervisor", wrap_supervisor)
    graph.add_node("memory", wrap_memory)
    graph.add_node("emotion", wrap_emotion)
    graph.add_node("conversation", wrap_conversation)

    graph.add_edge(START, "supervisor")

    graph.add_conditional_edges("supervisor", route_from_supervisor, {
        "memory": "memory",
        "emotion": "emotion",
        "conversation": "conversation",
    })

    graph.add_conditional_edges("memory", route_after_memory, {
        "emotion": "emotion",
        "conversation": "conversation",
    })

    graph.add_conditional_edges("emotion", route_after_emotion, {
        "memory": "memory",
        "conversation": "conversation",
    })

    graph.add_edge("conversation", END)

    return graph.compile()
