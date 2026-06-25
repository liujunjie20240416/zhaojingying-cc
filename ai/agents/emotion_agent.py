# ai/agents/emotion_agent.py
import json
import os
from openai import OpenAI


def emotion_agent_node(state: dict, api_key: str = "", api_base: str = "") -> dict:
    """Emotion Agent — 分析用户情绪，建议回复语调。
    输出: emotion_analysis: {"emotion": str, "intensity": int, "suggested_tone": str, "should_comfort": bool}
    """
    client = OpenAI(api_key=api_key or os.getenv("API_KEY"), base_url=api_base or os.getenv("API_BASE"))

    messages = state.get("messages", [])
    recent = []
    for msg in messages[-6:]:
        if hasattr(msg, "content"):
            recent.append(msg.content)

    user_msg = recent[-1] if recent else ""

    prompt = f"""分析用户情绪。最近对话：{chr(10).join(recent[-4:])}
输出纯JSON：
{{"emotion": "sad|happy|angry|anxious|neutral|tired|excited", "intensity": 0-10,
  "suggested_tone": "gentle|cheerful|calm|encouraging|playful", "should_comfort": true/false}}"""

    resp = client.chat.completions.create(
        model="deepseek-v4-pro", messages=[{"role": "user", "content": prompt}],
        temperature=0.0, max_tokens=500,
    )
    content = resp.choices[0].message.content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1]
        if "```" in content:
            content = content.rsplit("```", 1)[0]
        content = content.strip()

    try:
        analysis = json.loads(content)
    except json.JSONDecodeError:
        analysis = {"emotion": "neutral", "intensity": 3, "suggested_tone": "gentle", "should_comfort": False}

    return {"emotion_analysis": analysis}
