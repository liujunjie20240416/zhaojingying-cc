"""用户可查看和维护自己与角色之间的长期记忆。"""
from fastapi import APIRouter, Depends

from ai.memory.semantic import _index_fact, add_fact, sync_friend_memory_cache
from api.deps import get_current_user
from api.schemas import MemoryCreateRequest, MemoryUpdateRequest
from web.models.friend import Friend
from web.models.memory import SemanticMemory

router = APIRouter()
_CATEGORIES = {"identity", "preference", "experience", "relationship"}


def _serialize(memory: SemanticMemory) -> dict:
    return {
        "id": memory.id,
        "fact": memory.fact,
        "category": memory.category,
        "confidence": memory.confidence,
        "source": memory.source,
        "is_locked": memory.is_locked,
    }


@router.get("/api/friend/memory/")
def list_memories(friend_id: int, user=Depends(get_current_user)):
    friend = Friend.objects.filter(id=friend_id, me__user=user).first()
    if not friend:
        return {"result": "好友不存在", "memories": []}
    memories = SemanticMemory.objects.filter(friend=friend, is_active=True).order_by("category", "-confidence", "-id")
    return {"result": "success", "memories": [_serialize(memory) for memory in memories]}


@router.post("/api/friend/memory/")
def create_memory(data: MemoryCreateRequest, user=Depends(get_current_user)):
    if data.category not in _CATEGORIES:
        return {"result": "无效的记忆分类"}
    friend = Friend.objects.filter(id=data.friend_id, me__user=user).first()
    if not friend:
        return {"result": "好友不存在"}
    memory = add_fact(
        friend, data.fact.strip(), data.category, confidence=1.0,
        source="user", is_locked=True,
    )
    sync_friend_memory_cache(friend)
    return {"result": "success", "memory": _serialize(memory)}


@router.put("/api/friend/memory/{memory_id}/")
def update_memory(memory_id: int, data: MemoryUpdateRequest, user=Depends(get_current_user)):
    if data.category not in _CATEGORIES:
        return {"result": "无效的记忆分类"}
    memory = SemanticMemory.objects.filter(id=memory_id, friend__me__user=user, is_active=True).first()
    if not memory:
        return {"result": "记忆不存在"}

    memory.fact = data.fact.strip()
    memory.category = data.category
    memory.source = "user"
    memory.is_locked = True
    memory.save(update_fields=["fact", "category", "source", "is_locked", "updated_at"])
    _index_fact(memory.friend_id, memory.fact)
    sync_friend_memory_cache(memory.friend)
    return {"result": "success", "memory": _serialize(memory)}


@router.post("/api/friend/memory/{memory_id}/forget/")
def forget_memory(memory_id: int, user=Depends(get_current_user)):
    memory = SemanticMemory.objects.filter(id=memory_id, friend__me__user=user, is_active=True).first()
    if not memory:
        return {"result": "记忆不存在"}
    memory.is_active = False
    memory.save(update_fields=["is_active", "updated_at"])
    sync_friend_memory_cache(memory.friend)
    return {"result": "success"}
