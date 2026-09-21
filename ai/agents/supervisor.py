# ai/agents/supervisor.py
"""Supervisor — 一次 LLM 调用把用户消息判成多标签。

输出 has_emotion 与 memory_kind 两个**互相独立**的判断，路由图据此决定
是并行进 emotion / memory，还是直接进 conversation。

为什么不看关键词：意图本来就不是单选。「上次吵架我好难过」同时需要情绪回应
和历史检索，任何单标签分类器都会丢掉一半。
"""

import json
import re

from openai import OpenAI

from ai.config import llm_api_base, llm_api_key, llm_model
from ai.tracing import record_trace


# 无信息量的确认语：既没有可检索的事实，也没有情绪信号。
# 刻意不含「哦」「呵呵」「随便」——这些词在不同语境下含义相反（冷淡 or 撒娇），
# 必须交给分类器判断。误判的代价比省下的那次调用高。
LIGHTWEIGHT_ACKS = frozenset({
    "嗯", "嗯嗯", "哦哦", "好", "好的", "好哒", "好呀", "行", "收到",
    "哈哈", "哈哈哈", "嘿嘿", "早", "晚安", "在吗",
})

# 判断「消息是否只有符号」用：含汉字/字母/数字就算有内容。
_CONTENT_CHAR = re.compile(r"[一-鿿A-Za-z0-9]")

# 分类器从「偶发调用」变成「每条消息都调用」，20s 的最坏情况不再可接受。
CLASSIFIER_TIMEOUT = 5

_VALID_MEMORY_KINDS = {"none", "recall", "fact"}


def _no_op_decision(source: str) -> dict:
    return {
        "has_emotion": False,
        "memory_kind": "none",
        "classification_source": source,
    }


def _last_user_msg(state: dict) -> str:
    """取最后一条用户消息。

    AIMessage 带 tool_calls 属性（即使为空），据此跳过；HumanMessage 没有。
    """
    for msg in reversed(state.get("messages", []) or []):
        if hasattr(msg, "content") and not hasattr(msg, "tool_calls"):
            return getattr(msg, "content", "") or ""
        if isinstance(msg, dict) and msg.get("role") == "user":
            return msg.get("content", "") or ""
    return ""


def _is_lightweight_ack(user_msg: str) -> bool:
    text = user_msg.strip()
    if text in LIGHTWEIGHT_ACKS:
        return True
    return len(text) <= 8 and not _CONTENT_CHAR.search(text)


def supervisor_node(state: dict, api_key: str = "", api_base: str = "") -> dict:
    """把这一轮判成 has_emotion + memory_kind，两个标签互相独立。"""
    user_msg = _last_user_msg(state)

    if not user_msg:
        result = _no_op_decision("empty")
        record_trace("supervisor.route", {"user_msg": user_msg}, result)
        return result

    emotion_context = state.get("emotion_context") or []

    # 快速通道只处理「确定没有信息量」的消息。emoji 语义是快速通道看不见的
    # 情绪信号，所以带 emotion_context 的消息一律过分类器。
    if not emotion_context and _is_lightweight_ack(user_msg):
        result = _no_op_decision("fast_path")
        record_trace("supervisor.route", {"user_msg": user_msg}, result)
        return result

    recent_dialogue = _recent_dialogue(state.get("messages", []))
    result = _classify_with_llm(user_msg, emotion_context, api_key, api_base, recent_dialogue)
    record_trace(
        "supervisor.route",
        {"user_msg": user_msg, "emotion_context": emotion_context},
        result,
    )
    return result


def _recent_dialogue(messages: list, limit: int = 4) -> str:
    """Format preceding turns for resolving a short, context-dependent reply."""
    preceding = list(messages[:-1])[-limit:]
    lines = []
    for message in preceding:
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
    """一次调用同时回答两个问题；失败时降级到「检索一次」。"""
    try:
        client = OpenAI(
            api_key=api_key or llm_api_key(),
            base_url=api_base or llm_api_base(),
            timeout=CLASSIFIER_TIMEOUT,
        )
        prompt = f"""判断这条伴侣聊天消息需要哪些处理，只输出 JSON。
最近对话（可能为空；用于理解省略的指代，不要把它当用户当前问题）：
{recent_dialogue or "（无）"}
用户消息：{user_msg}
前端识别到的 emoji 含义：{json.dumps(emotion_context, ensure_ascii=False)}

{{"has_emotion": false, "memory_kind": "none", "confidence": 0.0}}

has_emotion：是否需要情绪回应——用户在表达情绪、需要安慰或共情，或者 emoji 带有情绪含义。两个判断互相独立，可以同时为真。
memory_kind：
  recall = 询问过去具体发生过的事、说过的原话（"上次""那次""你还记得"）
  fact   = 询问稳定的身份、偏好、习惯或关系（"我喜欢什么""我们是什么关系"）
  none   = 不需要翻记忆
confidence：你对上述判断的把握，0 到 1。"""
        response = client.chat.completions.create(
            model=llm_model(),
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=120,
            response_format={"type": "json_object"},
        )
        parsed = json.loads((response.choices[0].message.content or "{}").strip())
        memory_kind = parsed.get("memory_kind", "none")
        if memory_kind not in _VALID_MEMORY_KINDS:
            memory_kind = "none"
        return {
            "has_emotion": bool(parsed.get("has_emotion", False)),
            "memory_kind": memory_kind,
            "confidence": float(parsed.get("confidence", 0) or 0),
            "classification_source": "llm",
        }
    except Exception:
        # 分类失败不能让用户失去一次记忆：宁可按最宽的检索模式跑一次。
        # has_emotion 取 False——情绪分析本身也是一次 LLM 调用，分类器都连不上，
        # 它大概率也连不上，重试只是叠加延迟。
        return {
            "has_emotion": False,
            "memory_kind": "recall",
            "confidence": 0.0,
            "classification_source": "fallback",
        }
