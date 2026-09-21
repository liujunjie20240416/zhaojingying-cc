# ai/agents/supervisor.py
"""Supervisor — 一次 LLM 调用把用户消息判成多标签。

输出 has_emotion 与 memory_kind 两个**互相独立**的判断，路由图据此决定
是并行进 emotion / memory，还是直接进 conversation。

为什么不看关键词：意图本来就不是单选。「上次吵架我好难过」同时需要情绪回应
和历史检索，任何单标签分类器都会丢掉一半。
"""

import json
import unicodedata

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

# 「这条消息有没有可回应的内容」用 Unicode 类别判定：只由标点（P*）和
# 空白（Z*）组成就算没有。不能用「不含汉字/字母/数字」的字符白名单——
# 那样裸的 💔 会被当成无信息量消息吞掉，详见 _is_contentless 的注释。
_CONTENTLESS_CATEGORIES = frozenset("PZ")

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

    按 LangChain 的 `type` 判定，而不是「有没有 tool_calls 属性」：AIMessage
    与 ToolMessage 都不该被当成用户输入，而 `hasattr(msg, "tool_calls")` 只是
    一个恰好成立的巧合——工具调用落地后 ToolMessage 进入状态，这条会读错。
    """
    for msg in reversed(state.get("messages", []) or []):
        if isinstance(msg, dict):
            if msg.get("role") == "user":
                return msg.get("content", "") or ""
        elif getattr(msg, "type", "") == "human":
            return getattr(msg, "content", "") or ""
    return ""


def _is_contentless(user_msg: str) -> bool:
    """消息是否只有标点或空白——没有可回应的内容。

    用 Unicode 类别而不是字符白名单。字符白名单（`[一-鿿A-Za-z0-9]`）会漏掉
    表外 emoji：『💔』不含任何「内容字符」，于是被判成无信息量、走快速通道、
    has_emotion=False，用户得不到情绪回应。而且这个失败是静默的——
    classification_source 记的是 fast_path，看起来是最健康的那个值。
    """
    text = user_msg.strip()
    if text in LIGHTWEIGHT_ACKS:
        return True
    if len(text) > 8:
        return False
    return all(unicodedata.category(ch)[0] in _CONTENTLESS_CATEGORIES for ch in text)


def supervisor_node(state: dict, api_key: str = "", api_base: str = "") -> dict:
    """把这一轮判成 has_emotion + memory_kind，两个标签互相独立。"""
    user_msg = _last_user_msg(state)
    # 其余 agent 的 record_trace 都带 trace_metadata（friend_id / character_id /
    # entrypoint），supervisor 不带就会变成游离的、筛不出来的 span。
    trace_metadata = state.get("trace_metadata", {})
    emotion_context = state.get("emotion_context") or []

    if not user_msg:
        result = _no_op_decision("empty")
        record_trace("supervisor.route", {"user_msg": user_msg}, result, metadata=trace_metadata)
        return result

    # 快速通道只处理「确定没有信息量」的消息。emoji 语义是快速通道看不见的
    # 情绪信号，所以带 emotion_context 的消息一律过分类器。
    if not emotion_context and _is_contentless(user_msg):
        result = _no_op_decision("fast_path")
        record_trace("supervisor.route", {"user_msg": user_msg}, result, metadata=trace_metadata)
        return result

    recent_dialogue = _recent_dialogue(state.get("messages", []))
    result = _classify_with_llm(user_msg, emotion_context, api_key, api_base, recent_dialogue)
    trace_inputs = {
        "model": llm_model(),
        "user_msg": user_msg,
        "emotion_context": emotion_context,
        "recent_dialogue": recent_dialogue,
        "messages": [{"role": "user", "content": _build_classifier_prompt(
            user_msg, emotion_context, recent_dialogue)}],
    }
    # 降级那一轮最该被复盘——「降级率突然涨了」是唯一一个能自己冒出来的信号，
    # 但只看 classification_source 不知道涨在哪：超时？JSON 烂？鉴权挂了？
    if result.get("_error"):
        trace_inputs["error"] = result["_error"]
    record_trace(
        "supervisor.route",
        trace_inputs,
        result,
        run_type="llm",
        metadata=trace_metadata,
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


def _build_classifier_prompt(user_msg: str, emotion_context: list, recent_dialogue: str) -> str:
    """单独成函数：supervisor_node 要把它原样记进 trace，供事后逐条复盘。"""
    return f"""判断这条伴侣聊天消息需要哪些处理，只输出 JSON。
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
        response = client.chat.completions.create(
            model=llm_model(),
            messages=[{"role": "user", "content": _build_classifier_prompt(
                user_msg, emotion_context, recent_dialogue)}],
            temperature=0,
            max_tokens=120,
            response_format={"type": "json_object"},
        )
        content = str(response.choices[0].message.content or "").strip()
        # 空回复必须当成失败，不能变成一次看起来健康的「不需要记忆」。
        # 注意真正拦住它的是下一行的 json.loads：空串会抛 JSONDecodeError，同样落进
        # 下面这个 except，结果一样是降级。这一行负责的是「降级原因说得清楚」——
        # 没有它，trace 里的 _error 是一句 JSON 解析错误，看不出是模型压根没回内容。
        # （.strip() 在这里，所以纯空白的回复到这一行时也已经和空串无异。）
        if not content:
            raise ValueError("classifier returned empty content")
        parsed = json.loads(content)
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError("classifier returned no fields")

        memory_kind = parsed.get("memory_kind", "none")
        if memory_kind not in _VALID_MEMORY_KINDS:
            memory_kind = "none"

        # 不能直接 bool()：模型把布尔写成字符串时 bool("false") 是 True。
        # 认不出来的值一律当 False——这个方向偏保守（少一次情绪回应），
        # 反过来把未知值当 True 会让每句话都带情绪，代价更大。
        has_emotion = parsed.get("has_emotion", False)
        if not isinstance(has_emotion, bool):
            has_emotion = str(has_emotion).strip().lower() in {"true", "yes", "1"}

        try:
            confidence = float(parsed.get("confidence", 0) or 0)
        except (TypeError, ValueError):
            # 分类本身是成功的，只有 confidence 脏——不能因此把整个判断丢掉。
            confidence = 0.0

        return {
            "has_emotion": has_emotion,
            "memory_kind": memory_kind,
            "confidence": confidence,
            "classification_source": "llm",
        }
    except Exception as exc:
        # 分类失败不能让用户失去一次记忆：宁可按最宽的检索模式跑一次。
        # has_emotion 取 False——情绪分析本身也是一次 LLM 调用，分类器都连不上，
        # 它大概率也连不上，重试只是叠加延迟。
        return {
            "has_emotion": False,
            "memory_kind": "recall",
            "confidence": 0.0,
            "classification_source": "fallback",
            # 私有键，只给 supervisor_node 记 trace 用。LangGraph 会静默丢弃
            # 不在 MultiAgentState schema 里的键（实现阶段已验证），所以它不会
            # 泄漏进图状态。失败原因必须带出来：降级本身只是「没判」，
            # 知道了原因才知道该去修什么。
            "_error": f"{type(exc).__name__}: {exc}",
        }
