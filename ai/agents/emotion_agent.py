# ai/agents/emotion_agent.py
import json
from openai import OpenAI

from ai.config import (
    llm_api_base,
    llm_api_key,
    llm_model,
    require_llm_config,
    sub_llm_timeout,
)
from ai.tracing import record_trace


def emotion_agent_node(state: dict, api_key: str = "", api_base: str = "") -> dict:
    """Emotion Agent — 分析用户情绪，建议回复语调。
    输出: emotion_analysis: {"emotion": str, "intensity": int, "suggested_tone": str, "should_comfort": bool}
    """
    if not api_key and not api_base:
        require_llm_config()
    client = OpenAI(
        api_key=api_key or llm_api_key(),
        base_url=api_base or llm_api_base(),
        # 情绪分析炸了本来就只降级成 neutral（见下面的 except），没有理由让它
        # 用 SDK 默认的 600s read 超时把整轮拖住。
        timeout=sub_llm_timeout(),
        max_retries=1,
    )

    messages = state.get("messages", [])
    recent = []
    for msg in messages[-6:]:
        if hasattr(msg, "content"):
            recent.append(msg.content)

    user_msg = recent[-1] if recent else ""
    emoji_context = state.get("emotion_context") or []
    emoji_text = "\n".join(str(item) for item in emoji_context[:8])

    prompt = f"""分析用户情绪。最近对话：{chr(10).join(recent[-4:])}
前端提供的 emoji 语义提示（仅作为辅助，不是用户原话）：{emoji_text or '无'}
输出纯JSON：
{{"emotion": "sad|happy|angry|anxious|neutral|tired|excited", "intensity": 0-10,
  "suggested_tone": "gentle|cheerful|calm|encouraging|playful", "should_comfort": true/false}}"""

    trace_inputs = {
        "model": llm_model(),
        "recent_messages": recent[-4:],
        "emotion_context": emoji_context,
        "user_msg": user_msg,
        "messages": [{"role": "user", "content": prompt}],
    }
    record_trace(
        "emotion_agent.prompt",
        trace_inputs,
        metadata=state.get("trace_metadata", {}),
    )
    try:
        resp = client.chat.completions.create(
            model=llm_model(), messages=[{"role": "user", "content": prompt}],
            temperature=0.0, max_tokens=500,
        )
        content = resp.choices[0].message.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
            if "```" in content:
                content = content.rsplit("```", 1)[0]
            content = content.strip()
        analysis = json.loads(content)
    except Exception:
        # Match the degradation contract of the other LLM calls in the graph
        # (supervisor classification, query rewriter, compressor): a failed
        # emotion analysis must not take down the whole turn.
        content = ""
        analysis = {"emotion": "neutral", "intensity": 3, "suggested_tone": "gentle", "should_comfort": False}
        record_trace(
            "emotion_agent.fallback",
            trace_inputs,
            {"analysis": analysis, "error": "emotion_analysis_failed"},
            run_type="llm",
            metadata=state.get("trace_metadata", {}),
        )

    record_trace(
        "emotion_agent.output",
        trace_inputs,
        {"raw_content": content, "analysis": analysis},
        run_type="llm",
        metadata=state.get("trace_metadata", {}),
    )
    return {"emotion_analysis": analysis}
