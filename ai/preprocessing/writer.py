"""Step 3: Writer — 直接写入 Chunk 分析结果（无 Reduce）。

- TimeChunk: 每个 Chunk 一条，标签用日期
- TopicTag: 纯代码聚合所有 Chunk 的话题
- SemanticMemory: 用户事实（is_locked=False）+ 角色事实（is_locked=True）
"""

import json

from web.models.character import Character
from web.models.chat_message import ChatMessage
from web.models.import_analysis import ImportAnalysis, TimeChunk, TopicTag
from web.models.friend import Friend
from web.models.memory import SemanticMemory


def write_results(character_id: int, chunk_results: list[dict], chunks: list[dict], total_messages: int):
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

    # 4. 用户事实 → SemanticMemory（is_locked=False，允许 reflection 更新）
    _write_fragments_to_semantic(
        character_id, chunk_results,
        fragment_key="user_fragments", is_locked=False,
    )

    # 5. AI 角色事实 → SemanticMemory（is_locked=True，锁定不变）
    _write_fragments_to_semantic(
        character_id, chunk_results,
        fragment_key="character_fragments", is_locked=True,
    )

    # 6. 角色事实 → Character.profile（追加，不覆盖已有内容）
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


def _write_fragments_to_semantic(
    character_id: int,
    chunk_results: list[dict],
    fragment_key: str = "user_fragments",
    is_locked: bool = False,
):
    """将 chunk_results 中的 fragments 写入 SemanticMemory。

    fragment_key: "user_fragments" 或 "character_fragments"
    is_locked: 角色事实锁定不变，用户事实允许 reflection 更新
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
            SemanticMemory.objects.create(
                friend=friend,
                fact=fact,
                category=category,
                confidence=0.7,
                source="import",
                is_locked=is_locked,
                evidence="来自导入聊天记录预处理分析",
            )


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
        for frag in r.get("character_fragments", []):
            fact = frag.get("fact", "").strip()
            if not fact or len(fact) < 6:
                continue
            if fact in seen:
                continue
            seen.add(fact)
            new_fragments.append(fact)

    if not new_fragments:
        return

    current_profile = character.profile or ""

    # 子串匹配去重：已在 profile 中的不重复追加
    truly_new: list[str] = []
    for fact in new_fragments:
        if fact not in current_profile:
            truly_new.append(fact)

    if not truly_new:
        return

    # 追加到 profile 末尾
    new_section = "\n\n【从聊天记录自动学习】\n" + "\n".join(f"- {f}" for f in truly_new)
    character.profile = current_profile + new_section
    character.save(update_fields=["profile"])
