# ai/agents/supervisor.py
"""Supervisor routing: deterministic fast paths plus LLM fallback."""

import json

from openai import OpenAI

from ai.config import llm_api_base, llm_api_key, llm_model
from ai.tracing import record_trace


INTENT_ROUTE_MAP = {
    "chat": "conversation",
    "time": "conversation",
    "memory": "memory",
    "recall": "memory",
    "emotional": "emotion",
}

RECALL_SIGNALS = [
    "记得", "以前", "那次", "第一次", "上次", "什么时候",
    "说过", "聊过", "提过", "回忆", "往事",
]

TIME_SIGNALS = ["几点", "什么时候了", "现在时间", "今天几号", "星期几", "周几"]
MEMORY_SIGNALS = [
    "我喜欢什么", "我讨厌什么", "我的生日", "我叫什么", "你喜欢什么",
    "你的生日", "我们是什么关系", "怎么哄", "怎么安慰", "我的习惯",
]

EMOTIONAL_SIGNALS = [
    "难过", "伤心", "哭", "崩溃", "绝望", "害怕", "焦虑",
    "开心死", "激动", "太棒", "兴奋", "生气", "愤怒", "烦",
    "累死", "压力", "撑不住", "好累", "想哭",
]

AMBIGUOUS_SIGNALS = [
    "算了", "没事", "呵呵", "随便", "你忙吧", "当我没说", "一言难尽",
    "那个", "当时", "她", "我们", "是不是", "为什么", "怎么",
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

    for signal in TIME_SIGNALS:
        if signal in user_msg:
            result = {"intent": "time", "delegate_to": "conversation", "matched_signal": signal}
            record_trace("supervisor.route", {"user_msg": user_msg}, result)
            return result

    for signal in RECALL_SIGNALS:
        if signal in user_msg:
            result = {"intent": "recall", "delegate_to": "memory", "matched_signal": signal}
            record_trace("supervisor.route", {"user_msg": user_msg}, result)
            return result

    for signal in MEMORY_SIGNALS:
        if signal in user_msg:
            result = {"intent": "memory", "delegate_to": "memory", "matched_signal": signal}
            record_trace("supervisor.route", {"user_msg": user_msg}, result)
            return result

    emotion_context = state.get("emotion_context") or []
    if emotion_context or any(signal in user_msg for signal in AMBIGUOUS_SIGNALS):
        result = _classify_with_llm(user_msg, emotion_context, api_key, api_base)
        record_trace("supervisor.route", {"user_msg": user_msg, "emotion_context": emotion_context}, result)
        return result

    result = {"intent": "chat", "delegate_to": "conversation"}
    record_trace("supervisor.route", {"user_msg": user_msg}, result)
    return result


def _classify_with_llm(user_msg: str, emotion_context: list, api_key: str, api_base: str) -> dict:
    """Classify only ambiguous text/emoji; failures safely fall back to chat."""
    try:
        client = OpenAI(
            api_key=api_key or llm_api_key(), base_url=api_base or llm_api_base(), timeout=20
        )
        prompt = f"""判断这句伴侣聊天的意图，只输出 JSON。
用户消息：{user_msg}
前端识别到的 emoji 含义：{json.dumps(emotion_context, ensure_ascii=False)}

{{"intent":"chat|time|memory|recall|emotional","confidence":0.0,"emotion_intensity":0}}

recall=询问过去具体事件/原话；memory=询问稳定身份偏好关系；emotional=需要明显情绪回应；普通陪伴对话用chat。"""
        response = client.chat.completions.create(
            model=llm_model(), messages=[{"role": "user", "content": prompt}],
            temperature=0, max_tokens=120, response_format={"type": "json_object"},
        )
        parsed = json.loads((response.choices[0].message.content or "{}").strip())
        intent = parsed.get("intent", "chat")
        if intent not in INTENT_ROUTE_MAP:
            intent = "chat"
        return {
            "intent": intent,
            "delegate_to": INTENT_ROUTE_MAP[intent],
            "classification_source": "llm",
            "classification_confidence": float(parsed.get("confidence", 0)),
            "emotion_intensity_hint": int(parsed.get("emotion_intensity", 0)),
        }
    except Exception:
        return {"intent": "chat", "delegate_to": "conversation", "classification_source": "fallback"}
