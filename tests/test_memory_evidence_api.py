import datetime

import pytest
from django.contrib.auth.models import User
from fastapi.testclient import TestClient
from rest_framework_simplejwt.tokens import AccessToken

from main import app
from web.models.character import Character
from web.models.chat_message import ChatMessage
from web.models.friend import Friend, Message
from web.models.memory import MemoryEvidence, SemanticMemory
from web.models.user import UserProfile


def _make_friend(username="memory-evidence-owner"):
    user = User.objects.create_user(username=username)
    profile = UserProfile.objects.create(user=user)
    character = Character.objects.create(
        author=profile,
        name="测试角色",
        profile="温柔",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
    )
    return user, Friend.objects.create(me=profile, character=character)


@pytest.mark.django_db(transaction=True)
def test_memory_evidence_endpoint_returns_online_chat_context_only_to_owner():
    user, friend = _make_friend()
    message = Message.objects.create(
        friend=friend,
        user_message="我不吃香菜",
        input="prompt",
        output="好，我记住啦",
    )
    memory = SemanticMemory.objects.create(
        friend=friend,
        fact="用户不吃香菜",
        source="ai",
    )
    MemoryEvidence.objects.create(
        memory=memory,
        source_type="online_chat",
        message_refs=[message.id],
        chat_day=datetime.date(2026, 7, 14),
        excerpt="用户说不吃香菜",
    )
    token = str(AccessToken.for_user(user))

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            f"/api/friend/memory/{memory.id}/evidence/",
            headers={"Authorization": f"Bearer {token}"},
        )
        other_user = User.objects.create_user(username="memory-evidence-other")
        UserProfile.objects.create(user=other_user)
        other = client.get(
            f"/api/friend/memory/{memory.id}/evidence/",
            headers={"Authorization": f"Bearer {AccessToken.for_user(other_user)}"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["memory"]["source"] == "ai"
    assert payload["evidences"][0]["source_type"] == "online_chat"
    assert payload["evidences"][0]["context"] == [{
        "id": message.id,
        "timestamp": message.create_time.isoformat(),
        "user_message": "我不吃香菜",
        "output": "好，我记住啦",
    }]
    assert other.status_code == 404


@pytest.mark.django_db(transaction=True)
def test_memory_evidence_endpoint_returns_import_window_and_manual_assertion():
    user, friend = _make_friend("memory-import-owner")
    ChatMessage.objects.bulk_create([
        ChatMessage(
            character=friend.character,
            sender="用户" if index % 2 else "女友",
            content=f"导入消息 {index}",
            timestamp=f"2026-07-14 12:0{index}:00",
            msg_index=index,
        )
        for index in range(1, 7)
    ])
    imported = SemanticMemory.objects.create(
        friend=friend,
        fact="一条导入记忆",
        source="import",
    )
    MemoryEvidence.objects.create(
        memory=imported,
        source_type="import_chat",
        message_refs=[3],
        start_message_ref=3,
        end_message_ref=3,
        excerpt="导入消息 3",
    )
    manual = SemanticMemory.objects.create(
        friend=friend,
        fact="一条手动记忆",
        source="user",
    )
    MemoryEvidence.objects.create(
        memory=manual,
        source_type="user_assertion",
        excerpt="一条手动记忆",
    )
    headers = {"Authorization": f"Bearer {AccessToken.for_user(user)}"}

    with TestClient(app, raise_server_exceptions=False) as client:
        imported_response = client.get(
            f"/api/friend/memory/{imported.id}/evidence/", headers=headers
        )
        manual_response = client.get(
            f"/api/friend/memory/{manual.id}/evidence/", headers=headers
        )

    imported_payload = imported_response.json()["evidences"][0]
    assert imported_payload["context_available"] is True
    assert [item["msg_index"] for item in imported_payload["context"]] == [1, 2, 3, 4, 5, 6]
    manual_payload = manual_response.json()["evidences"][0]
    assert manual_payload["context_available"] is True
    assert manual_payload["context"] == []
    assert manual_payload["excerpt"] == "一条手动记忆"
