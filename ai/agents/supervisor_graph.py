# ai/agents/supervisor_graph.py
"""Supervisor Graph — Multi-Agent 主编排图。

编排流程:
    START → Supervisor → 分类（has_emotion / memory_kind）
        ├── 两者都命中 → emotion 与 memory 并行 ─┐
        ├── 只命中情绪 → emotion ───────────────┼──▶ Conversation → END
        ├── 只命中记忆 → memory ────────────────┤
        └── 都没命中 ───────────────────────────┘

图是无环的：并行扇出把「先情绪后补记忆」的串行补丁换成了同一步内的两支，
所以不需要 memory_done / emotion_done 之类的防环标志。
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
    # Supervisor 的两个独立判断，取代了原来的单标签 intent / delegate_to。
    has_emotion: NotRequired[bool]
    memory_kind: NotRequired[str]
    classification_source: NotRequired[str]
    memory_context: str
    memory_sections: NotRequired[list[dict]]
    memory_intent: NotRequired[dict]
    retrieval_plan: NotRequired[dict]
    reply_provenance: NotRequired[dict]
    emotion_analysis: dict | None
    character_profile: str
    style_profile: str
    base_system_prompt: str
    time_context: str
    conversation_summary: str
    character_name: str
    chat_sender_name: str
    semantic_facts: list[str]
    core_memory_context: NotRequired[str]
    last_provider_input_tokens: NotRequired[int]
    friend_id: int
    character_id: int | None
    trace_metadata: NotRequired[dict]
    emotion_context: NotRequired[list]
    vision_attachments: NotRequired[list]


def route_from_supervisor(state: dict) -> list[str]:
    """返回节点名**列表**即触发并行扇出（LangGraph 同超步执行）。

    返回空列表会走不通，所以两个标签都没命中时显式落到 conversation。
    """
    targets = []
    if state.get("has_emotion"):
        targets.append("emotion")
    if state.get("memory_kind", "none") != "none":
        targets.append("memory")
    return targets or ["conversation"]


def create_supervisor_app():
    """创建完整的 Multi-Agent 对话图。

    不接受任何参数：四个节点全部从 state 读取所需的值，而 state 由调用方
    （api/chat.py）在 invoke 时提供。这里曾有一组 friend_id / character_name
    之类的参数，但它们从未被读过——签名承诺了一份不生效的配置。
    """
    graph = StateGraph(MultiAgentState)

    graph.add_node("supervisor", supervisor_node)
    graph.add_node("memory", memory_agent_node)
    graph.add_node("emotion", emotion_agent_node)
    graph.add_node("conversation", conversation_agent_node)

    graph.add_edge(START, "supervisor")

    graph.add_conditional_edges("supervisor", route_from_supervisor, {
        "memory": "memory",
        "emotion": "emotion",
        "conversation": "conversation",
    })

    # 固定边：conversation 在 emotion / memory 都完成后执行（同超步扇入）。
    graph.add_edge("memory", "conversation")
    graph.add_edge("emotion", "conversation")
    graph.add_edge("conversation", END)

    return graph.compile()
