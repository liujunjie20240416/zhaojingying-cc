# ai/memory/reflection.py
"""Reflection - 从原始对话直接提炼 Semantic Memory。

触发条件: 距上次 reflection >= 6 小时，且有新对话。
不再经过 EpisodicMemory 中间层，LLM 直接看完整的用户-AI对话。
"""
import json
from django.db import transaction
from django.utils.timezone import now
from openai import OpenAI

from ai.config import llm_api_base, llm_api_key, llm_model, require_llm_config
from ai.tracing import record_trace
from web.models.friend import Friend, Message
from web.models.memory import SemanticMemory
from ai.memory.chat_day import (
    detect_day_start_hour_from_datetimes,
    get_chat_day,
    get_chat_day_range,
)
from ai.memory.semantic import (
    add_fact, add_memory_evidence, rebuild_semantic_index,
    resolve_conflict, sync_friend_memory_cache,
)

MIN_MESSAGES_PER_CHAT_DAY = 5
MIN_TEXT_LENGTH_PER_CHAT_DAY = 300


class ReflectionProcessingError(RuntimeError):
    """Retryable model/output failure for a durable ReflectionJob."""


def _get_client(api_key: str = "", api_base: str = ""):
    if not api_key and not api_base:
        require_llm_config()
    return OpenAI(api_key=api_key or llm_api_key(), base_url=api_base or llm_api_base())


def _build_existing_facts_text(friend_id: int) -> str:
    """构建已有事实文本，按分类分组展示全部活跃事实"""
    from collections import defaultdict
    grouped: dict[str, list[str]] = defaultdict(list)
    for sm in SemanticMemory.objects.filter(friend_id=friend_id, is_active=True).order_by("-confidence"):
        locked = "，不可替换" if sm.is_locked or not sm.is_mutable else ""
        state = "，历史状态" if sm.memory_state == "historical" else ""
        grouped[f"{sm.subject}:{sm.category}"].append(
            f"  - {sm.fact} (置信度:{sm.confidence:.1f}{locked}{state})"
        )

    labels = {"identity": "身份", "preference": "偏好", "experience": "经历", "relationship": "互动规律"}
    subject_labels = {"user": "用户", "girlfriend": "女友", "relationship": "两人关系"}
    parts = []
    for subject in ("user", "girlfriend", "relationship"):
        for cat in ("identity", "preference", "experience", "relationship"):
            key = f"{subject}:{cat}"
            if grouped[key]:
                parts.append(f"【{subject_labels[subject]} / {labels.get(cat, cat)}】\n" + "\n".join(grouped[key]))
    return "\n".join(parts) if parts else "（暂无已有事实）"


def _archive_facts(friend_id: int, fact_texts: list[str]):
    """批量将被替换的可变事实转为历史状态。"""
    SemanticMemory.objects.filter(
        friend_id=friend_id, is_active=True, fact__in=fact_texts,
        is_mutable=True, memory_state="current",
    ).update(memory_state="historical", valid_to=now())


def reflect_memories(
    friend: Friend,
    target_chat_day=None,
    force: bool = False,
    api_key: str = "",
    api_base: str = "",
) -> list[dict]:
    """从原始对话提炼 Semantic Memory。

    自动模式只处理已经结束且尚未处理的聊天日；force=True 绕过门槛。
    """
    processed_chat_day = target_chat_day
    if force and target_chat_day is None:
        messages = list(Message.objects.filter(
            friend=friend, create_time__gt=friend.last_reflection_time,
        ).order_by("create_time")[:100])
    else:
        datetimes = list(Message.objects.filter(friend=friend).order_by(
            "create_time"
        ).values_list("create_time", flat=True))
        if not datetimes:
            return []
        day_start_hour = detect_day_start_hour_from_datetimes(datetimes)
        current_chat_day = get_chat_day(now(), day_start_hour)
        if processed_chat_day is None:
            available_days = sorted({get_chat_day(dt, day_start_hour) for dt in datetimes})
            processed_chat_day = next((day for day in available_days
                if day < current_chat_day and (
                    friend.last_reflected_chat_day is None
                    or day > friend.last_reflected_chat_day
                )), None)
        if processed_chat_day is None or (not force and processed_chat_day >= current_chat_day):
            return []
        start, end = get_chat_day_range(processed_chat_day, day_start_hour)
        messages = list(Message.objects.filter(
            friend=friend, create_time__gte=start, create_time__lt=end,
        ).order_by("create_time")[:100])

        total_text_length = sum(
            len(message.user_message or "") + len(message.output or "")
            for message in messages
        )
        if not force and (
            len(messages) < MIN_MESSAGES_PER_CHAT_DAY
            or total_text_length < MIN_TEXT_LENGTH_PER_CHAT_DAY
        ):
            _mark_reflection_progress(friend, processed_chat_day)
            return []

    if not messages:
        return []

    client = _get_client(api_key, api_base)

    # 拼成对话文本
    dialogue_lines = []
    for i, m in enumerate(messages):
        dialogue_lines.append(f"{i+1}. [message_id={m.id}] 用户: {m.user_message}")
        dialogue_lines.append(f"   [message_id={m.id}] AI: {m.output}")
    dialogue_text = "\n".join(dialogue_lines)

    # 全部已有事实（按分类分组）
    existing_text = _build_existing_facts_text(friend.id)

    prompt = f"""分析以下角色扮演对话，提炼关于真实用户（对话中的人类一方）的关键信息。

重要：这是角色扮演对话。
- "用户" = 真实人类用户
- "AI" = 女友/角色
- 同时允许提取用户、女友和两人关系，但必须正确区分主体

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
    "subject": "user|girlfriend|relationship",
    "category": "identity|preference|experience|relationship",
    "confidence": 0.8,
    "conflicts_with": null,
    "replaces": [],
    "evidence_message_ids": [123]
  }}
]

字段说明：
- fact: 新事实（必须明确指明是"用户"，不要跟AI角色混淆）
- confidence: 0.0-1.0，根据证据充分程度判断
- conflicts_with: 与已有事实中某条精确冲突，填该事实的原文。null表示无精确冲突
- replaces: 【重要】如果新事实使得某些同领域的旧事实成为历史状态了（比如口味变化、关系规律变化），把旧事实原文列在这里。必须是同类型（preference替换preference，relationship替换relationship），identity和experience不要替换
- evidence_message_ids: 只填写直接支持该事实的真实 message_id；禁止虚构编号

规则：
1. 只输出新发现，不要重复已有事实（相似度>80%就算重复）
2. preference和relationship类型的旧事实可以变成历史状态（用replaces字段列出），不要说成彻底忘记
3. identity和experience一般不替换，只追加
4. 标有“不可替换”的事实绝不能填入 conflicts_with 或 replaces
5. relationship必须是从真实对话中验证过的具体互动规律，不能是推测
6. 用户说"现在/最近/目前"时，优先形成当前状态事实；如果它改变了旧偏好，把旧事实放入 replaces
7. 用户说"以前/那时候/曾经"时，优先形成历史事实，不要覆盖当前状态
8. 用户说"又/恢复/重新可以"时，表示状态回归或再次变化，旧状态应进入 replaces
9. 生日、出生地、过去发生过的经历不可替换；职业、城市、学校、作息、口味可以在明确表达时更新
10. 不要把少量测试消息、重复问候、无意义字符，提炼为长期偏好或互动规律；除非这种模式在多轮、多天中反复出现
11. 两人共同约定、共同事件和互动模式必须使用 subject=relationship；AI角色自身信息使用 subject=girlfriend"""

    trace_inputs = {
        "model": llm_model(),
        "friend_id": friend.id,
        "dialogue_text": dialogue_text[:6000],
        "existing_facts": existing_text,
        "messages": [{"role": "user", "content": prompt}],
    }
    record_trace(
        "memory.reflection.prompt",
        trace_inputs,
        metadata={"friend_id": friend.id, "message_count": len(messages)},
    )
    resp = client.chat.completions.create(
        model=llm_model(), messages=[{"role": "user", "content": prompt}],
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
            raise ReflectionProcessingError("Reflection output is not a JSON list")
    except json.JSONDecodeError as exc:
        raise ReflectionProcessingError("Reflection output is invalid JSON") from exc

    new_facts = []
    all_replaces: list[str] = []
    locked_facts = set(SemanticMemory.objects.filter(
        friend=friend, is_active=True
    ).filter(
        is_locked=True
    ).values_list("fact", flat=True))
    immutable_facts = set(SemanticMemory.objects.filter(
        friend=friend, is_active=True, is_mutable=False
    ).values_list("fact", flat=True))
    locked_facts |= immutable_facts
    messages_by_id = {message.id: message for message in messages}
    for item in extracted:
        fact_text = str(item.get("fact", "")).strip()
        if not fact_text:
            continue
        category = str(item.get("category", "preference"))
        if category not in ("identity", "preference", "experience", "relationship"):
            category = "preference"
        subject = str(item.get("subject", "user"))
        if subject not in ("user", "girlfriend", "relationship"):
            subject = "user"

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
                          confidence=float(item.get("confidence", 0.5)),
                          subject=subject, source="ai",
                          evidence=(
                              f"来自 {processed_chat_day} 的在线聊天 Reflection"
                              if processed_chat_day else "来自在线聊天 Reflection"
                          ),
                          index=False)
        evidence_ids = []
        raw_evidence_ids = item.get("evidence_message_ids", [])
        if isinstance(raw_evidence_ids, list):
            for value in raw_evidence_ids:
                try:
                    message_id = int(value)
                except (TypeError, ValueError):
                    continue
                if message_id in messages_by_id:
                    evidence_ids.append(message_id)
        evidence_ids = sorted(set(evidence_ids))
        evidence_excerpt = "\n".join(
            f"用户: {messages_by_id[message_id].user_message[:200]}\n"
            f"AI: {messages_by_id[message_id].output[:200]}"
            for message_id in evidence_ids[:6]
        )
        add_memory_evidence(
            sm,
            source_type="online_chat",
            message_refs=evidence_ids,
            excerpt=evidence_excerpt,
            chat_day=processed_chat_day,
        )
        new_facts.append({"fact": sm.fact, "category": sm.category, "confidence": sm.confidence})

    # 批量将被替换的旧事实转为历史状态
    if all_replaces:
        _archive_facts(friend.id, all_replaces)

    sync_friend_memory_cache(friend)
    rebuild_semantic_index(friend.id)
    _mark_reflection_progress(friend, processed_chat_day)
    record_trace(
        "memory.reflection.output",
        trace_inputs,
        {"raw_content": content, "new_facts": new_facts, "archived_facts": all_replaces},
        run_type="llm",
        metadata={"friend_id": friend.id, "message_count": len(messages)},
    )
    return new_facts


def _mark_reflection_progress(friend: Friend, chat_day=None):
    # Concurrent jobs must never move the progress marker backwards.
    with transaction.atomic():
        current = Friend.objects.select_for_update().get(id=friend.id)
        current.last_reflection_time = now()
        update_fields = ["last_reflection_time"]
        if chat_day is not None and (
            current.last_reflected_chat_day is None
            or chat_day > current.last_reflected_chat_day
        ):
            current.last_reflected_chat_day = chat_day
            update_fields.append("last_reflected_chat_day")
        current.save(update_fields=update_fields)
        friend.last_reflection_time = current.last_reflection_time
        friend.last_reflected_chat_day = current.last_reflected_chat_day
