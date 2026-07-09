# ai/agents/supervisor.py
"""Supervisor 路由 — 基于关键词匹配，零 API 调用延迟。"""

from ai.tracing import record_trace


INTENT_ROUTE_MAP = {
    "chat": "conversation",
    "recall": "memory",
    "emotional": "emotion",
}

RECALL_SIGNALS = [
    "记得", "以前", "那次", "第一次", "上次", "什么时候",
    "说过", "聊过", "提过", "回忆", "往事",
]

EMOTIONAL_SIGNALS = [
    "难过", "伤心", "哭", "崩溃", "绝望", "害怕", "焦虑",
    "开心死", "激动", "太棒", "兴奋", "生气", "愤怒", "烦",
    "累死", "压力", "撑不住", "好累", "想哭",
]


def supervisor_node(state: dict, api_key: str = "", api_base: str = "") -> dict:
    """Supervisor 路由 — 关键词匹配，零延迟"""

    user_msg = ""
    for msg in reversed(state.get("messages", [])):
        if hasattr(msg, "content") and not hasattr(msg, "tool_calls"):
            user_msg = msg.content
            break
        elif isinstance(msg, dict) and msg.get("role") == "user":
            user_msg = msg["content"]
            break

    if not user_msg:
        result = {"intent": "chat", "delegate_to": "conversation"}
        record_trace("supervisor.route", {"user_msg": user_msg}, result)
        return result

    for signal in EMOTIONAL_SIGNALS:
        if signal in user_msg:
            result = {"intent": "emotional", "delegate_to": "emotion", "matched_signal": signal}
            record_trace("supervisor.route", {"user_msg": user_msg}, result)
            return result

    for signal in RECALL_SIGNALS:
        if signal in user_msg:
            result = {"intent": "recall", "delegate_to": "memory", "matched_signal": signal}
            record_trace("supervisor.route", {"user_msg": user_msg}, result)
            return result

    result = {"intent": "chat", "delegate_to": "memory"}
    record_trace("supervisor.route", {"user_msg": user_msg}, result)
    return result
