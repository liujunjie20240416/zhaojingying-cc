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

# Short messages carrying these cues may refer to an earlier topic ("你去翻",
# "迪士尼呢？") and still deserve LLM classification when the previous turn
# was ordinary chat.  Everything else short inherits the previous intent.
REFERENTIAL_CUES = ["去", "翻", "找", "看", "再说", "还有", "查", "呢", "吗", "？", "?"]


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
    recent_dialogue = _recent_dialogue(state.get("messages", []))
    short_contextual_turn = len(user_msg.strip()) <= 24 and bool(recent_dialogue)

    # Ambiguity cues and emoji semantics are shift signals ("算了" after a
    # recall, a crying emoji after small talk): they beat intent inheritance.
    if emotion_context or any(signal in user_msg for signal in AMBIGUOUS_SIGNALS):
        result = _classify_with_llm(
            user_msg, emotion_context, api_key, api_base, recent_dialogue
        )
        record_trace("supervisor.route", {"user_msg": user_msg, "emotion_context": emotion_context}, result)
        return result

    # A short message carries almost no meaning on its own; its intent is
    # decided by the previous turn.  Inherit that intent instead of paying an
    # LLM classification for every "还有呢" / "哈哈".  Only short messages
    # with explicit referential cues ("你去翻", "迪士尼呢？") after an
    # ordinary-chat turn still need LLM classification.
    if short_contextual_turn:
        previous_intent = state.get("previous_intent") or "chat"
        if previous_intent in {"recall", "memory", "emotional"}:
            result = {
                "intent": previous_intent,
                "delegate_to": INTENT_ROUTE_MAP[previous_intent],
                "classification_source": "inherit",
            }
            record_trace(
                "supervisor.route",
                {"user_msg": user_msg, "previous_intent": previous_intent},
                result,
            )
            return result
        if any(cue in user_msg for cue in REFERENTIAL_CUES):
            result = _classify_with_llm(
                user_msg, emotion_context, api_key, api_base, recent_dialogue
            )
            record_trace("supervisor.route", {"user_msg": user_msg, "emotion_context": emotion_context}, result)
            return result
        result = {"intent": "chat", "delegate_to": "conversation", "classification_source": "short_chat"}
        record_trace("supervisor.route", {"user_msg": user_msg, "previous_intent": previous_intent}, result)
        return result

    result = {"intent": "chat", "delegate_to": "conversation"}
    record_trace("supervisor.route", {"user_msg": user_msg}, result)
    return result


def _recent_dialogue(messages: list, limit: int = 4) -> str:
    """Format preceding turns for resolving a short, context-dependent reply."""
    preceding = list(messages[:-1])[-limit:]
    lines = []
    for index, message in enumerate(preceding):
        content = getattr(message, "content", "")
        if not content:
            continue
        message_type = getattr(message, "type", "")
        role = "AI" if message_type == "ai" else "用户"
        lines.append(f"{role}：{str(content)[:500]}")
    return "\n".join(lines)


def _classify_with_llm(
    user_msg: str,
    emotion_context: list,
    api_key: str,
    api_base: str,
    recent_dialogue: str = "",
) -> dict:
    """Classify only ambiguous text/emoji; failures safely fall back to chat."""
    try:
        client = OpenAI(
            api_key=api_key or llm_api_key(), base_url=api_base or llm_api_base(), timeout=20
        )
        prompt = f"""判断这句伴侣聊天的意图，只输出 JSON。
最近对话（可能为空；用于理解省略的指代，不要把它当用户当前问题）：
{recent_dialogue or "（无）"}
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
