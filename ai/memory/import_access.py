"""Authorization and projection sync for Character-scoped Imported Chat."""

from web.models.character import Character
from web.models.friend import Friend
from web.models.memory import MemoryEvidence, SemanticMemory


VALID_VISIBILITIES = {"private", "public"}


def can_access_imported_context(friend: Friend) -> bool:
    """Return whether this Friend may use Character-scoped imported context."""
    character = friend.character
    return (
        character.imported_memory_visibility == "public"
        or friend.me_id == character.author_id
    )


def _copy_imported_memories(source_friend: Friend, target_friend: Friend) -> None:
    if source_friend.id == target_friend.id:
        return
    source_memories = SemanticMemory.objects.filter(
        friend=source_friend,
        source="import",
        is_active=True,
    ).prefetch_related("evidences")
    for source in source_memories:
        target, _ = SemanticMemory.objects.update_or_create(
            friend=target_friend,
            source="import",
            subject=source.subject,
            fact=source.fact,
            defaults={
                "category": source.category,
                "confidence": source.confidence,
                "evidence": source.evidence,
                "memory_state": source.memory_state,
                "is_locked": source.is_locked,
                "is_mutable": source.is_mutable,
                "is_active": source.is_active,
                "valid_from": source.valid_from,
                "valid_to": source.valid_to,
            },
        )
        for evidence in source.evidences.all():
            MemoryEvidence.objects.update_or_create(
                memory=target,
                source_type=evidence.source_type,
                start_message_ref=evidence.start_message_ref,
                end_message_ref=evidence.end_message_ref,
                defaults={
                    "message_refs": evidence.message_refs,
                    "chat_day": evidence.chat_day,
                    "excerpt": evidence.excerpt,
                },
            )


def sync_imported_context_to_friend(friend: Friend) -> None:
    """Materialize public imported Semantic Memory for one Friend."""
    if not can_access_imported_context(friend):
        return
    owner_friend = Friend.objects.filter(
        character_id=friend.character_id,
        me_id=friend.character.author_id,
    ).first()
    if owner_friend:
        _copy_imported_memories(owner_friend, friend)
        from ai.memory.semantic import rebuild_semantic_index, sync_friend_memory_cache

        rebuild_semantic_index(friend.id)
        sync_friend_memory_cache(friend)


def set_imported_context_visibility(
    character: Character,
    visibility: str,
) -> None:
    """Change visibility and reconcile all derived Friend-scoped projections."""
    if visibility not in VALID_VISIBILITIES:
        raise ValueError("无效的导入记忆可见性")
    if character.imported_memory_visibility != visibility:
        character.imported_memory_visibility = visibility
        character.save(update_fields=["imported_memory_visibility"])

    owner_friend, _ = Friend.objects.get_or_create(
        character=character,
        me=character.author,
    )
    affected_friends = list(Friend.objects.filter(character=character))

    if visibility == "private":
        private_friend_ids = [
            friend.id for friend in affected_friends if friend.me_id != character.author_id
        ]
        SemanticMemory.objects.filter(
            friend_id__in=private_friend_ids,
            source="import",
        ).delete()
    else:
        for friend in affected_friends:
            _copy_imported_memories(owner_friend, friend)

    from ai.memory.semantic import rebuild_semantic_index, sync_friend_memory_cache

    for friend in affected_friends:
        rebuild_semantic_index(friend.id)
        sync_friend_memory_cache(friend)
