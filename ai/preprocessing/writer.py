"""Step 3: Writer — 直接写入 Chunk 分析结果（无 Reduce）。

- TimeChunk: 每个 Chunk 一条，标签用日期
- TopicTag: 纯代码聚合所有 Chunk 的话题
- SemanticMemory: 用户事实 + 女友事实 + 关系事实
"""

import json

from web.models.character import Character
from web.models.chat_message import ChatMessage
from web.models.import_analysis import ImportAnalysis, TimeChunk, TopicTag
from web.models.friend import Friend
from web.models.memory import SemanticMemory
from ai.memory.semantic import add_fact, sync_friend_memory_cache


def write_results(
    character_id: int,
    chunk_results: list[dict],
    chunks: list[dict],
    total_messages: int,
    relationship_analysis: dict | None = None,
):
    """写入预处理结果。chunk_results 是 Map 阶段的直接输出，不再经过 Reduce。"""
    # 清理旧数据
    ImportAnalysis.objects.filter(character_id=character_id).delete()
    TimeChunk.objects.filter(character_id=character_id).delete()
    TopicTag.objects.filter(character_id=character_id).delete()
    SemanticMemory.objects.filter(
        friend__character_id=character_id, source="import"
    ).update(is_active=False)

    character = Character.objects.get(id=character_id)

    # 构建 Chunk 索引 → 消息 msg_index 映射（TopicTag 展开用）
    chunk_msg_map = _build_chunk_msg_map(chunks)
    # 构建 Chunk 索引 → chunk 元信息（TimeChunk 用）
    chunk_meta = {c["index"]: c for c in chunks}

    # 1. ImportAnalysis（简化：只存状态和总数）
    ImportAnalysis.objects.create(
        character=character,
        total_messages=total_messages,
        status="done",
        relationship_overview=_relationship_overview_text(relationship_analysis, chunk_results),
        timeline_json=json.dumps(
            (relationship_analysis or {}).get("timeline", {}),
            ensure_ascii=False,
        ),
    )

    # 2. TimeChunk — 每个 Chunk 一条，用日期做标签
    valid_results = [r for r in chunk_results if not r.get("error")]
    for r in valid_results:
        ci = r["chunk_index"]
        meta = chunk_meta.get(ci, {})
        # 标签用日期，如 "2024-03-15"
        label = (meta.get("time_start", "") or "")[:10]
        TimeChunk.objects.create(
            character=character,
            label=label[:100],
            start_msg_index=meta.get("start_msg_index", 0),
            end_msg_index=meta.get("end_msg_index", 0),
            summary=r.get("chunk_summary", "")[:200],
            key_events=json.dumps(r.get("key_events", []), ensure_ascii=False),
        )

    # 3. TopicTag — 纯代码聚合所有 Chunk 的话题
    _write_topic_tags(character, chunk_results, chunk_msg_map)

    # 4. 用户事实 → SemanticMemory
    _write_fragments_to_semantic(
        character_id, chunk_results,
        fragment_key="user_fragments", subject="user",
    )

    # 5. 女友事实 → SemanticMemory（继承人格，默认锁定）
    _write_fragments_to_semantic(
        character_id, chunk_results,
        fragment_key="girlfriend_fragments", subject="girlfriend",
    )

    # 6. 两人共同记忆 → SemanticMemory（导入的过去事实默认锁定）
    _write_fragments_to_semantic(
        character_id, chunk_results,
        fragment_key="relationship_fragments", subject="relationship",
    )

    # 7. 女友事实 → Character.profile 自动学习区块（可重建）
    _append_fragments_to_profile(character, chunk_results)


def _build_chunk_msg_map(chunks: list[dict]) -> dict[int, list[int]]:
    """构建 {chunk_index: [msg_index, ...]} 映射。"""
    mapping = {}
    for c in chunks:
        indices = [m["msg_index"] for m in c.get("messages", [])]
        if indices:
            mapping[c["index"]] = indices
    return mapping


def _write_topic_tags(character: Character, chunk_results: list[dict], chunk_msg_map: dict[int, list[int]]):
    """纯代码聚合话题：按 tag 合并所有 Chunk 中出现的 chunk_index，展开为 msg_indices 后写入。"""
    # 聚合：{tag: {chunk_index, ...}}
    topic_chunks: dict[str, set[int]] = {}
    for r in chunk_results:
        if r.get("error"):
            continue
        for topic in r.get("topics", []):
            tag = topic.strip()
            if not tag:
                continue
            if tag not in topic_chunks:
                topic_chunks[tag] = set()
            topic_chunks[tag].add(r["chunk_index"])

    # 按出现 Chunk 数降序排列后写入
    sorted_topics = sorted(topic_chunks.items(), key=lambda x: -len(x[1]))
    for tag, chunk_indices in sorted_topics:
        msg_indices: list[int] = []
        for ci in chunk_indices:
            msg_indices.extend(chunk_msg_map.get(ci, []))

        if msg_indices:
            TopicTag.objects.create(
                character=character,
                tag=tag[:100],
                msg_indices=json.dumps(sorted(msg_indices), ensure_ascii=False),
            )


def _build_relationship_overview(chunk_results: list[dict]) -> str:
    """从 chunk 摘要、关键事件和关系 fragments 生成轻量关系概览。"""
    lines: list[str] = []
    for r in chunk_results:
        if r.get("error"):
            continue
        summary = str(r.get("chunk_summary", "")).strip()
        if summary:
            lines.append(f"- {summary}")
        for event in r.get("key_events", [])[:3]:
            event = str(event).strip()
            if event:
                lines.append(f"- {event}")
        for frag in r.get("relationship_fragments", [])[:3]:
            fact = str(frag.get("fact", "")).strip()
            if fact:
                lines.append(f"- {fact}")

    deduped = list(dict.fromkeys(lines))
    if not deduped:
        return ""
    return "从导入聊天记录中整理出的两人关系概览：\n" + "\n".join(deduped[:40])


def _relationship_overview_text(
    relationship_analysis: dict | None,
    chunk_results: list[dict],
) -> str:
    overview = str((relationship_analysis or {}).get("overview", "")).strip()
    if overview:
        return overview[:5000]
    return _build_relationship_overview(chunk_results)


def _write_fragments_to_semantic(
    character_id: int,
    chunk_results: list[dict],
    fragment_key: str = "user_fragments",
    subject: str = "user",
):
    """将 chunk_results 中的 fragments 写入 SemanticMemory。

    fragment_key: "user_fragments"、"girlfriend_fragments" 或 "relationship_fragments"
    subject: "user"、"girlfriend" 或 "relationship"
    """
    friends = Friend.objects.filter(character_id=character_id)
    if not friends.exists():
        return

    # 收集所有 fragment，去重
    seen: set[str] = set()
    all_fragments: list[dict] = []
    for r in chunk_results:
        if r.get("error"):
            continue
        for frag in r.get(fragment_key, []):
            fact = frag.get("fact", "").strip()
            if not fact or len(fact) < 6:
                continue
            if fact in seen:
                continue
            seen.add(fact)
            all_fragments.append(frag)

    for friend in friends:
        for frag in all_fragments[:50]:  # 每人最多 50 条
            fact = frag["fact"]
            category = frag.get("category", "identity")
            # 跳过已存在的活跃记录
            if SemanticMemory.objects.filter(
                friend=friend, fact=fact, is_active=True
            ).exists():
                continue
            add_fact(
                friend=friend,
                fact=fact,
                category=category,
                confidence=0.7,
                evidence="来自导入聊天记录预处理分析",
                source="import",
                subject=subject,
            )
        sync_friend_memory_cache(friend)


def _append_fragments_to_profile(character: Character, chunk_results: list[dict]):
    """将新提取的 character_fragments 追加到 Character.profile。

    只追加 profile 中尚不存在的 fragments，不覆盖已有内容。
    每次预处理只补充新学到的角色特征，已存在的保持不动（包括手动编辑的内容）。
    """
    # 收集所有 character_fragments，去重
    seen: set[str] = set()
    new_fragments: list[str] = []
    for r in chunk_results:
        if r.get("error"):
            continue
        fragments = r.get("girlfriend_fragments") or r.get("character_fragments", [])
        for frag in fragments:
            fact = frag.get("fact", "").strip()
            if not fact or len(fact) < 6:
                continue
            if fact in seen:
                continue
            seen.add(fact)
            new_fragments.append(fact)

    current_profile = character.profile or ""
    marker = "【从聊天记录自动学习】"
    base_profile = current_profile.split(marker, 1)[0].rstrip()

    if not new_fragments:
        if marker in current_profile:
            character.profile = base_profile
            character.save(update_fields=["profile"])
        return

    # 子串匹配去重：已在手动基础 profile 中的不重复追加
    truly_new: list[str] = []
    for fact in new_fragments:
        if fact not in base_profile:
            truly_new.append(fact)

    if not truly_new:
        if marker in current_profile:
            character.profile = base_profile
            character.save(update_fields=["profile"])
        return

    new_section = f"\n\n{marker}\n" + "\n".join(f"- {f}" for f in truly_new)
    character.profile = base_profile + new_section
    character.save(update_fields=["profile"])
