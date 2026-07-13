"""Step 3: Writer — 直接写入 Chunk 分析结果（无 Reduce）。

- TimeChunk: 每个 Chunk 一条，标签用日期
- TopicTag: 纯代码聚合所有 Chunk 的话题
- SemanticMemory: 用户事实 + 女友事实 + 关系事实
"""

import json

from django.db import transaction
from web.models.character import Character
from web.models.chat_message import ChatMessage
from web.models.import_analysis import ImportAnalysis, TimeChunk, TopicTag
from web.models.friend import Friend
from web.models.memory import SemanticMemory
from ai.memory.semantic import (
    add_fact,
    add_memory_evidence,
    rebuild_semantic_index,
    sync_friend_memory_cache,
)
from ai.memory.style import build_style_profile


@transaction.atomic
def write_results(
    character_id: int,
    chunk_results: list[dict],
    chunks: list[dict],
    total_messages: int,
    relationship_analysis: dict | None = None,
    style_profile: str = "",
    total_chunks: int = 0,
):
    """写入预处理结果。chunk_results 是 Map 阶段的直接输出，不再经过 Reduce。"""
    # 清理旧数据
    ImportAnalysis.objects.filter(character_id=character_id).delete()
    TimeChunk.objects.filter(character_id=character_id).delete()
    TopicTag.objects.filter(character_id=character_id).delete()
    # 导入分析是可重建投影。重跑时直接清除旧 import 事实及其证据，
    # 不影响用户手动维护和后续 Reflection 生成的事实。
    SemanticMemory.objects.filter(
        friend__character_id=character_id, source="import"
    ).delete()

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
        total_chunks=total_chunks,
        completed_chunks=total_chunks,
        failed_chunks=0,
        stage="done",
        relationship_overview=_relationship_overview_text(relationship_analysis, chunk_results),
        timeline_json=json.dumps(
            (relationship_analysis or {}).get("timeline", {}),
            ensure_ascii=False,
        ),
    )

    # 2. TimeChunk — AnalysisChunk 可一天多个，但运行时每天只保留一条
    valid_results = [r for r in chunk_results if not r.get("error")]
    day_groups: dict[str, list[tuple[dict, dict]]] = {}
    for r in valid_results:
        meta = chunk_meta.get(r["chunk_index"], {})
        day = meta.get("chat_day") or (meta.get("time_start", "") or "")[:10]
        day_groups.setdefault(day, []).append((r, meta))
    for day, items in sorted(day_groups.items()):
        reduced_day = (relationship_analysis or {}).get("day_summaries", {}).get(day, {})
        summaries = list(dict.fromkeys(
            str(r.get("chunk_summary", "")).strip() for r, _ in items
            if str(r.get("chunk_summary", "")).strip()
        ))
        events = list(dict.fromkeys(
            str(event).strip() for r, _ in items for event in r.get("key_events", [])
            if str(event).strip()
        ))
        TimeChunk.objects.create(
            character=character,
            label=day[:100],
            start_msg_index=min(meta.get("start_msg_index", 0) for _, meta in items),
            end_msg_index=max(meta.get("end_msg_index", 0) for _, meta in items),
            summary=str(reduced_day.get("summary") or "；".join(summaries))[:2000],
            key_events=json.dumps(reduced_day.get("key_events") or events[:20], ensure_ascii=False),
        )

    # 3. TopicTag — 纯代码聚合所有 Chunk 的话题
    _write_topic_tags(character, chunk_results, chunk_msg_map)

    # 4. 用户事实 → SemanticMemory
    _write_fragments_to_semantic(
        character_id, chunk_results,
        chunk_msg_map, fragment_key="user_fragments", subject="user",
    )

    # 5. 女友事实 → SemanticMemory（继承人格，默认锁定）
    _write_fragments_to_semantic(
        character_id, chunk_results,
        chunk_msg_map, fragment_key="girlfriend_fragments", subject="girlfriend",
    )

    # 6. 两人共同记忆 → SemanticMemory（导入的过去事实默认锁定）
    _write_fragments_to_semantic(
        character_id, chunk_results,
        chunk_msg_map, fragment_key="relationship_fragments", subject="relationship",
    )

    # 7. 角色说话风格 → 独立短摘要；Character.profile 保持人工核心人设
    character.style_profile = style_profile or _build_style_profile(chunk_results)
    marker = "【从聊天记录自动学习】"
    if marker in (character.profile or ""):
        character.profile = character.profile.split(marker, 1)[0].rstrip()
    character.save(update_fields=["style_profile", "profile"])


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
                msg_indices=json.dumps(sorted(set(msg_indices)), ensure_ascii=False),
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
    chunk_msg_map: dict[int, list[int]],
    fragment_key: str = "user_fragments",
    subject: str = "user",
):
    """将 chunk_results 中的 fragments 写入 SemanticMemory。

    fragment_key: "user_fragments"、"girlfriend_fragments" 或 "relationship_fragments"
    subject: "user"、"girlfriend" 或 "relationship"
    """
    character = Character.objects.get(id=character_id)
    # Imported Chat is Character-scoped, but private by default. The owner's
    # Friend is the canonical holder of imported Semantic Memory projections.
    Friend.objects.get_or_create(character=character, me=character.author)
    friends = Friend.objects.filter(character=character)
    if character.imported_memory_visibility != "public":
        friends = friends.filter(me=character.author)

    # 收集所有 fragment，去重
    fragments_by_fact: dict[str, dict] = {}
    for r in chunk_results:
        if r.get("error"):
            continue
        for frag in r.get(fragment_key, []):
            fact = frag.get("fact", "").strip()
            if not fact or len(fact) < 6:
                continue
            entry = fragments_by_fact.setdefault(fact, {
                "fragment": frag,
                "message_refs": set(),
            })
            supplied_refs = frag.get("evidence_msg_indices") or []
            allowed_refs = set(chunk_msg_map.get(r.get("chunk_index"), []))
            valid_refs = {
                int(ref) for ref in supplied_refs
                if str(ref).isdigit() and int(ref) in allowed_refs
            }
            # Older or malformed model output still gets coarse chunk-level provenance.
            entry["message_refs"].update(valid_refs or allowed_refs)

    all_refs = {
        ref for entry in fragments_by_fact.values() for ref in entry["message_refs"]
    }
    message_lookup = {
        message.msg_index: message
        for message in ChatMessage.objects.filter(
            character_id=character_id, msg_index__in=all_refs
        ).order_by("msg_index")
    }

    for friend in friends:
        for fact, entry in fragments_by_fact.items():
            frag = entry["fragment"]
            fact = frag["fact"]
            category = frag.get("category", "identity")
            sm = SemanticMemory.objects.filter(
                friend=friend, fact=fact, is_active=True
            ).first()
            if not sm:
                sm = add_fact(
                    friend=friend,
                    fact=fact,
                    category=category,
                    confidence=0.7,
                    evidence="来自导入聊天记录预处理分析",
                    source="import",
                    subject=subject,
                    index=False,
                )
            refs = sorted(entry["message_refs"])
            excerpt_lines = []
            for ref in refs[:12]:
                message = message_lookup.get(ref)
                if message:
                    excerpt_lines.append(
                        f"[{message.timestamp}] {message.sender}: {message.content[:200]}"
                    )
            first_message = message_lookup.get(refs[0]) if refs else None
            chat_day = None
            if first_message and first_message.timestamp:
                try:
                    import datetime
                    chat_day = datetime.date.fromisoformat(first_message.timestamp[:10])
                except ValueError:
                    chat_day = None
            add_memory_evidence(
                sm,
                source_type="import_chat",
                message_refs=refs,
                excerpt="\n".join(excerpt_lines),
                chat_day=chat_day,
            )
        rebuild_semantic_index(friend.id)
        sync_friend_memory_cache(friend)


def _build_style_profile(chunk_results: list[dict]) -> str:
    """Build a bounded always-on style signature, not a dump of character facts."""
    facts: list[str] = []
    for result in chunk_results:
        if result.get("error"):
            continue
        for fragment in result.get("girlfriend_fragments", []):
            fact = str(fragment.get("fact", "")).strip()
            if fact:
                facts.append(fact)
    return build_style_profile(facts)


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
