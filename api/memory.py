"""用户可查看和维护自己与角色之间的长期记忆。"""
from fastapi import APIRouter, Depends

from ai.memory.semantic import (
    add_fact, add_memory_evidence, delete_semantic_index_entries,
    index_semantic_memory,
)
from ai.memory.import_access import can_access_imported_context
from api.deps import get_current_user
from api.errors import ApiError
from api.schemas import MemoryCreateRequest, MemoryUpdateRequest
from web.models.chat_message import ChatMessage
from web.models.friend import Friend, Message
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
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
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
        raise ApiError(404, "friend_not_found", "好友不存在")
    memories = SemanticMemory.objects.filter(friend=friend, is_active=True).prefetch_related(
        "evidences"
    ).order_by(
        "subject", "category", "memory_state", "-confidence", "-id"
    )
    return {"result": "success", "memories": [_serialize(memory) for memory in memories]}


@router.get("/api/friend/memory/{memory_id}/evidence/")
def get_memory_evidence(memory_id: int, user=Depends(get_current_user)):
    """Return permission-checked source context for one visible memory card."""
    memory = SemanticMemory.objects.filter(
        id=memory_id,
        friend__me__user=user,
        is_active=True,
    ).prefetch_related("evidences").first()
    if not memory:
        raise ApiError(404, "memory_not_found", "记忆不存在")

    evidences = []
    for evidence in memory.evidences.all():
        context = []
        context_available = True
        refs = sorted({int(ref) for ref in (evidence.message_refs or [])})

        if evidence.source_type == "online_chat" and refs:
            messages = Message.objects.filter(
                id__in=refs[:30],
                friend=memory.friend,
            ).order_by("create_time", "id")
            context = [
                {
                    "id": message.id,
                    "timestamp": message.create_time.isoformat(),
                    "user_message": message.user_message,
                    "output": message.output,
                }
                for message in messages
            ]
        elif evidence.source_type == "import_chat":
            context_available = can_access_imported_context(memory.friend)
            if context_available:
                start = evidence.start_message_ref
                end = evidence.end_message_ref
                if start is None and refs:
                    start = min(refs)
                if end is None and refs:
                    end = max(refs)
                if start is not None and end is not None:
                    imported_messages = ChatMessage.objects.filter(
                        character=memory.friend.character,
                        msg_index__gte=max(0, start - 3),
                        msg_index__lte=end + 3,
                    ).order_by("msg_index")[:30]
                    context = [
                        {
                            "msg_index": message.msg_index,
                            "timestamp": message.timestamp,
                            "sender": message.sender,
                            "content": message.content,
                        }
                        for message in imported_messages
                    ]

        evidences.append({
            "id": evidence.id,
            "source_type": evidence.source_type,
            "chat_day": evidence.chat_day,
            "excerpt": evidence.excerpt,
            "message_refs": refs,
            "context_available": context_available,
            "context": context,
        })

    return {
        "result": "success",
        "memory": {
            "id": memory.id,
            "fact": memory.fact,
            "source": memory.source,
            "subject": memory.subject,
            "category": memory.category,
        },
        "evidences": evidences,
    }


@router.post("/api/friend/memory/")
def create_memory(data: MemoryCreateRequest, user=Depends(get_current_user)):
    if data.category not in _CATEGORIES:
        raise ApiError(422, "invalid_memory_category", "无效的记忆分类")
    if data.subject not in _SUBJECTS:
        raise ApiError(422, "invalid_memory_subject", "无效的记忆主体")
    friend = Friend.objects.filter(id=data.friend_id, me__user=user).first()
    if not friend:
        raise ApiError(404, "friend_not_found", "好友不存在")
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
    index_semantic_memory(memory)
    return {"result": "success", "memory": _serialize(memory)}


@router.put("/api/friend/memory/{memory_id}/")
def update_memory(memory_id: int, data: MemoryUpdateRequest, user=Depends(get_current_user)):
    if data.category not in _CATEGORIES:
        raise ApiError(422, "invalid_memory_category", "无效的记忆分类")
    if data.subject not in _SUBJECTS:
        raise ApiError(422, "invalid_memory_subject", "无效的记忆主体")
    memory = SemanticMemory.objects.filter(id=memory_id, friend__me__user=user, is_active=True).first()
    if not memory:
        raise ApiError(404, "memory_not_found", "记忆不存在")

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
    delete_semantic_index_entries(memory.friend_id, [memory.id])
    index_semantic_memory(memory)
    return {"result": "success", "memory": _serialize(memory)}


@router.post("/api/friend/memory/{memory_id}/forget/")
def forget_memory(memory_id: int, user=Depends(get_current_user)):
    memory = SemanticMemory.objects.filter(id=memory_id, friend__me__user=user, is_active=True).first()
    if not memory:
        raise ApiError(404, "memory_not_found", "记忆不存在")
    memory.is_active = False
    memory.save(update_fields=["is_active", "updated_at"])
    delete_semantic_index_entries(memory.friend_id, [memory.id])
    return {"result": "success"}
