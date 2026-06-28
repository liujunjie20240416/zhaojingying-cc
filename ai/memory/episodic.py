# ai/memory/episodic.py
"""Episodic Memory — 只写不读的缓冲区。

每轮对话提取摘要写入 EpisodicMemory 表，供 Reflection 提炼 Semantic Memory 使用。
检索需求由 ChatMessage 混合检索 (ai/rag/retriever.py) 和 Semantic Memory (ai/memory/semantic.py) 覆盖。
"""
import json

from openai import OpenAI

from ai.config import llm_api_base, llm_api_key, llm_model, require_llm_config
from web.models.memory import EpisodicMemory


def _get_client(api_key: str = "", api_base: str = ""):
    if not api_key and not api_base:
        require_llm_config()
    return OpenAI(
        api_key=api_key or llm_api_key(),
        base_url=api_base or llm_api_base(),
    )


def extract_episodic_info(user_msg: str, ai_response: str, api_key: str = "", api_base: str = "") -> dict:
    """LLM 提取对话的 summary, keywords, importance"""
    client = _get_client(api_key, api_base)
    prompt = f"""分析以下角色扮演对话，提取关键信息。输出纯JSON（不要markdown代码块）：

用户消息：{user_msg[:200]}
AI回复：{ai_response[:200]}

注意：这是角色扮演。用户是真实人类，AI是扮演的角色。摘要应区分双方信息。

JSON格式：{{"summary": "一句话摘要(≤50字)", "keywords": "3-5个关键词空格分隔", "importance": 0.0-1.0}}

summary规则：
- 涉及用户的信息用"用户XXX"（如"用户生日是2月4号"）
- 涉及AI角色的信息用"AI XXX"（如"AI透露自己生日是农历十月十七"）
- 不要混淆用户和AI

importance评分标准：
- 0.8-1.0: 涉及用户个人信息、偏好、承诺、重要事件
- 0.4-0.7: 有信息量的日常聊天
- 0.1-0.3: 问候、客套、无实质内容"""

    resp = client.chat.completions.create(
        model=llm_model(),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=1000,
    )
    content = resp.choices[0].message.content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1]
        if "```" in content:
            content = content.rsplit("```", 1)[0]
        content = content.strip()
    try:
        result = json.loads(content)
        return {
            "summary": str(result.get("summary", ""))[:200],
            "keywords": str(result.get("keywords", ""))[:200],
            "importance": max(0.0, min(1.0, float(result.get("importance", 0.5)))),
        }
    except (json.JSONDecodeError, ValueError):
        return {"summary": user_msg[:50], "keywords": "", "importance": 0.5}


def write_episodic(friend, user_msg: str, ai_response: str, api_key: str = "", api_base: str = ""):
    """写入一条 Episodic Memory。

    低 importance (< 0.3) 的对话（问候/客套）跳过不写，减少噪音。
    Episodic 仅作为 Reflection 的原料缓冲区，不提供检索接口。
    """
    try:
        info = extract_episodic_info(user_msg, ai_response, api_key, api_base)
        if info["importance"] < 0.3:
            return None
        ep = EpisodicMemory.objects.create(
            friend=friend,
            summary=info["summary"],
            keywords=info["keywords"],
            importance=info["importance"],
            raw_messages=json.dumps(
                [{"role": "user", "content": user_msg}, {"role": "ai", "content": ai_response}],
                ensure_ascii=False,
            ),
            msg_count=1,
        )
        return ep
    except Exception:
        return None
