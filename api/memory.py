"""用户可查看和维护自己与角色之间的长期记忆。"""
from fastapi import APIRouter, Depends

from ai.memory.semantic import (
    add_fact, add_memory_evidence, rebuild_semantic_index, sync_friend_memory_cache,
)
from api.deps import get_current_user
from api.schemas import MemoryCreateRequest, MemoryUpdateRequest
from web.models.friend import Friend
from web.models.memory import SemanticMemory

router = APIRouter()
_CATEGORIES = {"identity", "preference", "experience", "relationship"}
_SUBJECTS = {"user", "girlfriend", "relationship"}


def _serialize(memory: SemanticMemory) -> dict:
    return {
        "id": memory.id,
        "fact": memory.fact,
        "subject": memory.subject,
        "category": memory.category,
        "confidence": memory.confidence,
        "source": memory.source,
        "memory_state": memory.memory_state,
        "is_locked": memory.is_locked,
        "is_mutable": memory.is_mutable,
        "valid_from": memory.valid_from,
        "valid_to": memory.valid_to,
        "evidences": [
            {
                "id": evidence.id,
                "source_type": evidence.source_type,
                "message_refs": evidence.message_refs,
                "chat_day": evidence.chat_day,
                "excerpt": evidence.excerpt,
            }
            for evidence in memory.evidences.all()
        ],
    }


@router.get("/api/friend/memory/")
def list_memories(friend_id: int, user=Depends(get_current_user)):
    friend = Friend.objects.filter(id=friend_id, me__user=user).first()
    if not friend:
        return {"result": "好友不存在", "memories": []}
    memories = SemanticMemory.objects.filter(friend=friend, is_active=True).prefetch_related(
        "evidences"
    ).order_by(
        "subject", "category", "memory_state", "-confidence", "-id"
    )
    return {"result": "success", "memories": [_serialize(memory) for memory in memories]}


@router.post("/api/friend/memory/")
def create_memory(data: MemoryCreateRequest, user=Depends(get_current_user)):
    if data.category not in _CATEGORIES:
        return {"result": "无效的记忆分类"}
    if data.subject not in _SUBJECTS:
        return {"result": "无效的记忆主体"}
    friend = Friend.objects.filter(id=data.friend_id, me__user=user).first()
    if not friend:
        return {"result": "好友不存在"}
    memory = add_fact(
        friend, data.fact.strip(), data.category, confidence=1.0,
        source="user", is_locked=True, is_mutable=False, subject=data.subject,
        index=False,
    )
    add_memory_evidence(
        memory,
        source_type="user_assertion",
        excerpt=data.fact.strip(),
    )
    sync_friend_memory_cache(friend)
    rebuild_semantic_index(friend.id)
    return {"result": "success", "memory": _serialize(memory)}


@router.put("/api/friend/memory/{memory_id}/")
def update_memory(memory_id: int, data: MemoryUpdateRequest, user=Depends(get_current_user)):
    if data.category not in _CATEGORIES:
        return {"result": "无效的记忆分类"}
    if data.subject not in _SUBJECTS:
        return {"result": "无效的记忆主体"}
    memory = SemanticMemory.objects.filter(id=memory_id, friend__me__user=user, is_active=True).first()
    if not memory:
        return {"result": "记忆不存在"}

    memory.fact = data.fact.strip()
    memory.subject = data.subject
    memory.category = data.category
    memory.source = "user"
    memory.is_locked = True
    memory.is_mutable = False
    memory.save(update_fields=[
        "fact", "subject", "category", "source", "is_locked", "is_mutable", "updated_at",
    ])
    add_memory_evidence(
        memory,
        source_type="user_assertion",
        excerpt=data.fact.strip(),
    )
    sync_friend_memory_cache(memory.friend)
    rebuild_semantic_index(memory.friend_id)
    return {"result": "success", "memory": _serialize(memory)}


@router.post("/api/friend/memory/{memory_id}/forget/")
def forget_memory(memory_id: int, user=Depends(get_current_user)):
    memory = SemanticMemory.objects.filter(id=memory_id, friend__me__user=user, is_active=True).first()
    if not memory:
        return {"result": "记忆不存在"}
    memory.is_active = False
    memory.save(update_fields=["is_active", "updated_at"])
    sync_friend_memory_cache(memory.friend)
    rebuild_semantic_index(memory.friend_id)
    return {"result": "success"}
