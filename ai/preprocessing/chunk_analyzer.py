"""Step 2: Map — 对单个 Chunk 调用 LLM，同时分析用户和 AI 角色。

每个 Chunk 独立分析，输出 JSON。支持传入相邻 Chunk 摘要作为上下文，
带一次重试。
"""

import json
import os
import time

from openai import OpenAI

MAX_RETRIES = 1

CATEGORY_OPTIONS = ["identity", "preference", "experience", "relationship"]


def _get_client(api_key: str = "", api_base: str = ""):
    return OpenAI(
        api_key=api_key or os.getenv("API_KEY"),
        base_url=api_base or os.getenv("API_BASE"),
    )


def analyze_chunk(
    chunk: dict,
    target_name: str,
    prev_summary: str = "",
    next_summary: str = "",
    api_key: str = "",
    api_base: str = "",
) -> dict:
    """LLM 分析一个 Chunk，带一次重试。

    输入: chunk + target_name + 可选相邻 Chunk 摘要
    输出: {chunk_summary, topics, key_events, user_fragments, character_fragments}
    """
    for attempt in range(MAX_RETRIES + 1):
        try:
            return _do_analyze(chunk, target_name, prev_summary, next_summary, api_key, api_base)
        except Exception as e:
            if attempt < MAX_RETRIES:
                time.sleep(1)
                continue
            return _empty_result(chunk, str(e))


def _do_analyze(
    chunk: dict,
    target_name: str,
    prev_summary: str,
    next_summary: str,
    api_key: str,
    api_base: str,
) -> dict:
    client = _get_client(api_key, api_base)

    lines = []
    for m in chunk["messages"]:
        role = "AI角色" if m["sender"] == target_name else "用户"
        lines.append(f"[{m['timestamp']}] {role}: {m['content'][:200]}")
    dialogue = "\n".join(lines[-150:])

    time_range = f"{chunk['time_start']} ~ {chunk['time_end']}"

    context_note = ""
    if prev_summary:
        context_note += f"前一天发生了什么：{prev_summary}\n"
    if next_summary:
        context_note += f"后一天发生了什么：{next_summary}\n"
    if context_note:
        context_note = f"\n【相邻时间段上下文】\n{context_note}"

    prompt = f"""分析以下微信聊天记录片段（{time_range}，共 {len(chunk['messages'])} 条消息）。

重要：这是一段角色扮演对话，需要同时分析双方。
- 发送人「用户」= 真实人类，提取 ta 的身份、偏好、经历、与角色的互动规律
- 发送人「{target_name}」= AI 扮演的角色，提取 ta 在对话中展现的性格、说话风格、关键信息
- 注意区分：不要把 AI 角色说的话当成用户的信息，也不要把用户说的话当成 AI 角色的
{context_note}
对话内容：
{dialogue[:8000]}

请输出纯 JSON（不要 markdown 代码块）：
{{
    "chunk_summary": "这一天的一句话总结（≤50字）",
    "topics": ["大分类/细分类"],
    "key_events": ["客观关键事件"],
    "user_fragments": [
        {{"fact": "关于用户的事实，每条独立完整、含主语'用户'", "category": "identity"}},
        {{"fact": "用户的偏好", "category": "preference"}}
    ],
    "character_fragments": [
        {{"fact": "关于{target_name}的事实，每条独立完整、含角色名", "category": "identity"}},
        {{"fact": "角色的说话风格", "category": "identity"}}
    ]
}}

规则：
- chunk_summary 结合相邻上下文理解当天的事（≤50字）
- topics 控制在 2-5 个，用"大分类/细分类"格式（如"美食/火锅"、"工作/跳槽"）
- key_events 只记客观事件（如"用户提到换了工作"、"吵架"）
- user_fragments 每条 fact 必须独立完整，包含主语"用户"（如"用户生日是2月4号"、"用户喜欢吃火锅"）
- character_fragments 每条 fact 必须独立完整，包含主语"{target_name}"（如"{target_name}说话温柔喜欢用颜文字"、"{target_name}自称是程序员"）
- 每个 fragment 的 category 从以下选一：identity（身份/性格）、preference（偏好/喜好）、experience（经历/事件）、relationship（与对方的互动规律）
- 不要编造不存在的信息"""

    resp = client.chat.completions.create(
        model="deepseek-v4-pro",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=1200,
    )
    content = resp.choices[0].message.content.strip()
    return _parse_json(content, chunk)


def _parse_json(content: str, chunk: dict) -> dict:
    if content.startswith("```"):
        content = content.split("\n", 1)[1]
        if "```" in content:
            content = content.rsplit("```", 1)[0]
        content = content.strip()

    try:
        result = json.loads(content)
        return {
            "chunk_summary": str(result.get("chunk_summary", ""))[:200],
            "topics": _ensure_list(result.get("topics"))[:10],
            "key_events": _ensure_list(result.get("key_events"))[:10],
            "user_fragments": _ensure_fragments(result.get("user_fragments"))[:30],
            "character_fragments": _ensure_fragments(result.get("character_fragments"))[:30],
            "chunk_index": chunk["index"],
            "error": False,
        }
    except (json.JSONDecodeError, ValueError):
        return _empty_result(chunk, "JSON parse failed")


def _ensure_list(val) -> list:
    if isinstance(val, list):
        return [str(v)[:200] for v in val]
    return []


def _ensure_fragments(val) -> list[dict]:
    """确保 fragments 是合法的 {fact, category} 列表"""
    if not isinstance(val, list):
        return []
    result = []
    for item in val:
        if not isinstance(item, dict):
            continue
        fact = str(item.get("fact", "")).strip()
        category = str(item.get("category", "identity")).strip()
        if not fact:
            continue
        if category not in CATEGORY_OPTIONS:
            category = "identity"
        result.append({"fact": fact[:300], "category": category})
    return result


def _empty_result(chunk: dict, error: str = "") -> dict:
    return {
        "chunk_summary": "",
        "topics": [],
        "key_events": [],
        "user_fragments": [],
        "character_fragments": [],
        "chunk_index": chunk["index"],
        "error": bool(error),
        "error_msg": error,
    }
