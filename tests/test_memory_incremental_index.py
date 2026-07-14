import pytest
from django.contrib.auth.models import User
from fastapi.testclient import TestClient
from rest_framework_simplejwt.tokens import AccessToken

from main import app
from web.models.character import Character
from web.models.friend import Friend
from web.models.user import UserProfile


@pytest.mark.django_db(transaction=True)
def test_manual_memory_mutations_update_only_the_affected_vector(monkeypatch):
    user = User.objects.create_user(username="memory-incremental")
    profile = UserProfile.objects.create(user=user)
    character = Character.objects.create(
        author=profile,
        name="测试角色",
        profile="温柔",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
    )
    friend = Friend.objects.create(me=profile, character=character)
    indexed = []
    deleted = []
    monkeypatch.setattr(
        "api.memory.index_semantic_memory",
        lambda memory: indexed.append(memory.id) or True,
        raising=False,
    )
    monkeypatch.setattr(
        "api.memory.delete_semantic_index_entries",
        lambda friend_id, memory_ids: deleted.append((friend_id, memory_ids)) or True,
        raising=False,
    )
    monkeypatch.setattr(
        "api.memory.rebuild_semantic_index",
        lambda *_: (_ for _ in ()).throw(AssertionError("manual mutation must not rebuild")),
        raising=False,
    )
    monkeypatch.setattr(
        "api.memory.sync_friend_memory_cache",
        lambda *_: (_ for _ in ()).throw(AssertionError("legacy cache must not sync")),
        raising=False,
    )
    headers = {"Authorization": f"Bearer {AccessToken.for_user(user)}"}

    with TestClient(app, raise_server_exceptions=False) as client:
        created = client.post(
            "/api/friend/memory/",
            json={
                "friend_id": friend.id,
                "fact": "用户喜欢轻音乐",
                "subject": "user",
                "category": "preference",
            },
            headers=headers,
        )
        assert created.status_code == 200, created.text
        memory_id = created.json()["memory"]["id"]
        assert indexed == [memory_id]
        assert deleted == []

        indexed.clear()
        updated = client.put(
            f"/api/friend/memory/{memory_id}/",
            json={
                "fact": "用户喜欢睡前听轻音乐",
                "subject": "user",
                "category": "preference",
            },
            headers=headers,
        )
        assert updated.status_code == 200, updated.text
        assert deleted == [(friend.id, [memory_id])]
        assert indexed == [memory_id]

        indexed.clear()
        deleted.clear()
        forgotten = client.post(
            f"/api/friend/memory/{memory_id}/forget/",
            headers=headers,
        )
        assert forgotten.status_code == 200, forgotten.text
        assert deleted == [(friend.id, [memory_id])]
        assert indexed == []
