import datetime

import pytest
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from fastapi.testclient import TestClient
from rest_framework_simplejwt.tokens import AccessToken

from main import app
from web.models.character import Character
from web.models.friend import Friend, Message, MessageAttachment
from web.models.memory import EpisodicMemory, MemoryEvidence, SemanticMemory
from web.models.reflection_job import ReflectionJob
from web.models.user import UserProfile


@pytest.mark.django_db(transaction=True)
def test_clear_history_deletes_online_derivatives_but_preserves_import_and_user_memories(
    monkeypatch,
    settings,
    tmp_path,
):
    settings.MEDIA_ROOT = tmp_path
    user = User.objects.create_user(username="clear-history-owner")
    profile = UserProfile.objects.create(user=user)
    character = Character.objects.create(
        author=profile,
        name="测试角色",
        profile="温柔",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
    )
    friend = Friend.objects.create(
        me=profile,
        character=character,
        memory="旧缓存",
        conversation_summary="旧对话摘要",
    )
    message = Message.objects.create(
        friend=friend,
        user_message="我不吃香菜",
        input="prompt",
        output="好，我记住了",
    )
    ai_memory = SemanticMemory.objects.create(
        friend=friend,
        fact="用户不吃香菜",
        source="ai",
    )
    MemoryEvidence.objects.create(
        memory=ai_memory,
        source_type="online_chat",
        message_refs=[message.id],
        excerpt="我不吃香菜",
    )
    imported_memory = SemanticMemory.objects.create(
        friend=friend,
        fact="两人去过杭州",
        source="import",
        subject="relationship",
        category="experience",
        is_locked=True,
        is_mutable=False,
    )
    manual_memory = SemanticMemory.objects.create(
        friend=friend,
        fact="用户喜欢轻音乐",
        source="user",
        is_locked=True,
        is_mutable=False,
    )
    stale_online_evidence = MemoryEvidence.objects.create(
        memory=manual_memory,
        source_type="online_chat",
        message_refs=[message.id],
        excerpt="编辑前的在线聊天依据",
    )
    manual_evidence = MemoryEvidence.objects.create(
        memory=manual_memory,
        source_type="user_assertion",
        excerpt="用户喜欢轻音乐",
    )
    attachment = MessageAttachment(
        friend=friend,
        message=message,
        mime_type="image/webp",
        file_size=7,
        width=1,
        height=1,
        sha256="0" * 64,
    )
    attachment.file.save("clear-me.webp", ContentFile(b"private"), save=False)
    attachment.save()
    attachment_path = attachment.file.path
    EpisodicMemory.objects.create(
        friend=friend,
        summary="用户谈到饮食偏好",
        raw_messages="[]",
    )
    ReflectionJob.objects.create(
        friend=friend,
        chat_day=datetime.date(2026, 7, 14),
    )
    indexed_deletions = []
    dropped = []
    monkeypatch.setattr(
        "api.message.delete_semantic_index_entries",
        lambda friend_id, memory_ids: indexed_deletions.append((friend_id, memory_ids)),
        raising=False,
    )
    monkeypatch.setattr(
        "api.message.rebuild_semantic_index",
        lambda *_: (_ for _ in ()).throw(AssertionError("clear must not rebuild kept memories")),
        raising=False,
    )
    monkeypatch.setattr("api.message.drop_online_history_index", lambda friend_id: dropped.append(friend_id))

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/friend/message/clear/",
            json={"friend_id": friend.id},
            headers={"Authorization": f"Bearer {AccessToken.for_user(user)}"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == {
        "messages": 1,
        "attachments": 1,
        "semantic_memories": 1,
        "online_evidences": 2,
        "episodic_memories": 1,
        "reflection_jobs": 1,
    }
    assert not Message.objects.filter(friend=friend).exists()
    assert not SemanticMemory.objects.filter(id=ai_memory.id).exists()
    assert not MemoryEvidence.objects.filter(memory_id=ai_memory.id).exists()
    assert SemanticMemory.objects.filter(id=imported_memory.id, source="import").exists()
    assert SemanticMemory.objects.filter(id=manual_memory.id, source="user").exists()
    assert not MemoryEvidence.objects.filter(id=stale_online_evidence.id).exists()
    assert MemoryEvidence.objects.filter(id=manual_evidence.id).exists()
    assert not MessageAttachment.objects.filter(id=attachment.id).exists()
    assert not attachment.file.storage.exists(attachment.file.name)
    assert attachment_path.startswith(str(tmp_path))
    assert not EpisodicMemory.objects.filter(friend=friend).exists()
    assert not ReflectionJob.objects.filter(friend=friend).exists()

    friend.refresh_from_db()
    assert friend.conversation_summary == ""
    assert friend.online_history_generation == 1
    assert friend.memory == "旧缓存"
    assert indexed_deletions == [(friend.id, [ai_memory.id])]
    assert dropped == [friend.id]
