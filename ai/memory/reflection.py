# ai/memory/reflection.py
"""Reflection - 从原始对话直接提炼 Semantic Memory。

触发条件: 距上次 reflection >= 6 小时，且有新对话。
不再经过 EpisodicMemory 中间层，LLM 直接看完整的用户-AI对话。
"""
import json
import os
from django.utils.timezone import now
from openai import OpenAI

from web.models.friend import Friend, Message
from web.models.memory import SemanticMemory
from ai.memory.semantic import add_fact, resolve_conflict, sync_friend_memory_cache

_REFLECTION_INTERVAL_HOURS = 6


def _get_client(api_key: str = "", api_base: str = ""):
    return OpenAI(api_key=api_key or os.getenv("API_KEY"), base_url=api_base or os.getenv("API_BASE"))


def _build_existing_facts_text(friend_id: int) -> str:
    """构建已有事实文本，按分类分组展示全部活跃事实"""
    from collections import defaultdict
    grouped: dict[str, list[str]] = defaultdict(list)
    for sm in SemanticMemory.objects.filter(friend_id=friend_id, is_active=True).order_by("-confidence"):
        locked = "，用户已确认，请勿替换" if sm.is_locked else ""
        grouped[sm.category].append(f"  - {sm.fact} (置信度:{sm.confidence:.1f}{locked})")

    labels = {"identity": "身份", "preference": "偏好", "experience": "经历", "relationship": "互动规律"}
    parts = []
    for cat in ("identity", "preference", "experience", "relationship"):
        if grouped[cat]:
            parts.append(f"【{labels.get(cat, cat)}】\n" + "\n".join(grouped[cat]))
    return "\n".join(parts) if parts else "（暂无已有事实）"


def _deactivate_facts(friend_id: int, fact_texts: list[str]):
    """批量将指定事实标记为 inactive"""
    SemanticMemory.objects.filter(
        friend_id=friend_id, is_active=True, fact__in=fact_texts
    ).update(is_active=False)


def reflect_memories(friend: Friend, force: bool = False, api_key: str = "", api_base: str = "") -> list[dict]:
    """从原始对话提炼 Semantic Memory。

    触发条件:
      - force=True: 强制执行
      - 自动: 距上次 reflection >= 6 小时 且 有新对话
    """
    if not force:
        hours_since = (now() - friend.last_reflection_time).total_seconds() / 3600
        if hours_since < _REFLECTION_INTERVAL_HOURS:
            return []
        recent_count = Message.objects.filter(
            friend=friend, create_time__gt=friend.last_reflection_time
        ).count()
        if recent_count == 0:
            return []

    # 读取这段时间的全部对话（最多100轮，防止上下文过大）
    messages = list(Message.objects.filter(
        friend=friend,
        create_time__gt=friend.last_reflection_time,
    ).order_by("create_time")[:100])

    if not messages:
        return []

    client = _get_client(api_key, api_base)

    # 拼成对话文本
    dialogue_lines = []
    for i, m in enumerate(messages):
        dialogue_lines.append(f"{i+1}. 用户: {m.user_message}")
        dialogue_lines.append(f"   AI: {m.output}")
    dialogue_text = "\n".join(dialogue_lines)

    # 全部已有事实（按分类分组）
    existing_text = _build_existing_facts_text(friend.id)

    prompt = f"""分析以下角色扮演对话，提炼关于真实用户（对话中的人类一方）的关键信息。

重要：这是角色扮演对话。
- "用户" = 真实人类用户，我们需要记住的是这个人的信息
- "AI" = AI扮演的角色，AI角色的信息不需要记录
- 只提取关于真实用户的事实！

事实分类（必须四选一，不允许其他分类）：
- identity: 用户的客观身份信息（姓名/年龄/生日/职业/地点/家庭成员等），这些不会随时间改变
- preference: 用户的偏好习惯（喜欢/讨厌/习惯/饮食/爱好/审美等），可能随时间改变
- experience: 用户经历过的具体事件（过去发生的事/共同回忆/重大事件），不可改变
- relationship: 用户跟AI角色互动中被验证过的规律（如"用户受委屈时最想被肯定"、"用户说你烦其实是开心"、"用户吃软不吃硬"），换了另一个角色可能不成立

============ 已有全部事实 ============
{existing_text}

============ 新对话内容 ============
{dialogue_text[:6000]}

============ 任务 ============
对新发现或需更新的事实，输出纯JSON（每项包含以下字段）：

[
  {{
    "fact": "关于用户的一句事实",
    "category": "identity|preference|experience|relationship",
    "confidence": 0.8,
    "conflicts_with": null,
    "replaces": []
  }}
]

字段说明：
- fact: 新事实（必须明确指明是"用户"，不要跟AI角色混淆）
- confidence: 0.0-1.0，根据证据充分程度判断
- conflicts_with: 与已有事实中某条精确冲突，填该事实的原文。null表示无精确冲突
- replaces: 【重要】如果新事实使得某些同领域的旧事实过时了（比如口味变化、关系规律变化），把需要淘汰的旧事实原文列在这里。必须是同类型（preference替换preference，relationship替换relationship），identity和experience不要替换

规则：
1. 只输出新发现，不要重复已有事实（相似度>80%就算重复）
2. preference和relationship类型的旧事实可以被新事实整体替换（用replaces字段列出）
3. identity和experience一般不替换，只追加
4. 标有“用户已确认，请勿替换”的事实绝不能填入 conflicts_with 或 replaces
5. relationship必须是从真实对话中验证过的具体互动规律，不能是推测"""

    resp = client.chat.completions.create(
        model="deepseek-v4-pro", messages=[{"role": "user", "content": prompt}],
        temperature=0.2, max_tokens=4000,
    )
    content = resp.choices[0].message.content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1]
        if "```" in content:
            content = content.rsplit("```", 1)[0]
        content = content.strip()

    try:
        extracted = json.loads(content)
        if not isinstance(extracted, list):
            return []
    except json.JSONDecodeError:
        return []

    new_facts = []
    all_replaces: list[str] = []
    locked_facts = set(SemanticMemory.objects.filter(
        friend=friend, is_active=True, is_locked=True
    ).values_list("fact", flat=True))
    for item in extracted:
        fact_text = str(item.get("fact", "")).strip()
        if not fact_text:
            continue
        category = str(item.get("category", "preference"))
        if category not in ("identity", "preference", "experience", "relationship"):
            category = "preference"

        # 收集需要替换的旧事实
        replaces = item.get("replaces")
        if isinstance(replaces, list):
            all_replaces.extend([str(r).strip() for r in replaces if r and str(r).strip() not in locked_facts])

        # 处理精确冲突
        conflicts = str(item.get("conflicts_with", "")).strip() if item.get("conflicts_with") else ""
        if conflicts and conflicts not in locked_facts:
            sm = resolve_conflict(friend.id, conflicts, fact_text)
            if conflicts not in all_replaces:
                all_replaces.append(conflicts)
        else:
            sm = add_fact(friend=friend, fact=fact_text, category=category,
                          confidence=float(item.get("confidence", 0.5)))
        new_facts.append({"fact": sm.fact, "category": sm.category, "confidence": sm.confidence})

    # 批量淘汰被替换的旧事实
    if all_replaces:
        _deactivate_facts(friend.id, all_replaces)

    sync_friend_memory_cache(friend)
    friend.last_reflection_time = now()
    friend.save(update_fields=["last_reflection_time"])
    return new_facts
