import json
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from langchain_core.messages import HumanMessage

from api.message import clear_history
from api.schemas import RemoveFriendRequest
from web.models.character import Character
from web.models.friend import Friend, Message
from web.models.memory import SemanticMemory
from web.models.user import UserProfile


def _make_friend(username):
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
def test_completed_chat_from_old_generation_is_not_saved():
    from api.chat import _save_completed_message

    _, friend = _make_friend("stale-chat-save")
    friend.online_history_generation = 1
    friend.save(update_fields=["online_history_generation"])

    saved = _save_completed_message(
        friend_id=friend.id,
        expected_generation=0,
        display_message="这条消息已经过期",
        inputs={"messages": [HumanMessage(content="这条消息已经过期")]},
        full_output="旧回复",
        output_bubbles=["旧回复"],
        full_usage={},
        attachment_ids=[],
    )

    assert saved is None
    assert not Message.objects.filter(friend=friend).exists()


@pytest.mark.django_db(transaction=True)
def test_clear_during_reflection_prevents_ai_memory_writeback(monkeypatch):
    from ai.memory import reflection

    user, friend = _make_friend("reflection-clear-race")
    message = Message.objects.create(
        friend=friend,
        user_message="我不吃香菜，而且以后都不要放",
        input="prompt",
        output="好，我以后都会记住不放香菜。",
    )
    monkeypatch.setattr("api.message.drop_online_history_index", lambda *_: None)
    monkeypatch.setattr("api.message.delete_semantic_index_entries", lambda *_: True)
    monkeypatch.setattr(reflection, "index_semantic_memory", lambda *_: True, raising=False)
    monkeypatch.setattr(
        reflection,
        "rebuild_semantic_index",
        lambda *_: (_ for _ in ()).throw(AssertionError("reflection must not rebuild")),
        raising=False,
    )

    class FakeCompletions:
        def create(self, **kwargs):
            clear_history(RemoveFriendRequest(friend_id=friend.id), user=user)
            content = json.dumps([{
                "fact": "用户不吃香菜",
                "subject": "user",
                "category": "preference",
                "confidence": 0.9,
                "conflicts_with": None,
                "replaces": [],
                "evidence_message_ids": [message.id],
            }])
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(reflection, "_get_client", lambda *args, **kwargs: fake_client)

    result = reflection.reflect_memories(
        friend,
        force=True,
        api_key="test",
        expected_history_generation=0,
    )

    assert result == []
    assert not SemanticMemory.objects.filter(friend=friend, source="ai").exists()


@pytest.mark.django_db(transaction=True)
def test_reflection_indexes_only_its_new_memories(monkeypatch):
    from ai.memory import reflection

    _, friend = _make_friend("reflection-incremental-index")
    message = Message.objects.create(
        friend=friend,
        user_message="我喜欢睡前听轻音乐",
        input="prompt",
        output="好，我记住啦。",
    )
    content = json.dumps([{
        "fact": "用户喜欢睡前听轻音乐",
        "subject": "user",
        "category": "preference",
        "confidence": 0.9,
        "conflicts_with": None,
        "replaces": [],
        "evidence_message_ids": [message.id],
    }])
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: fake_response)
        )
    )
    indexed = []
    monkeypatch.setattr(reflection, "_get_client", lambda *args, **kwargs: fake_client)
    monkeypatch.setattr(
        reflection,
        "index_semantic_memory",
        lambda memory: indexed.append(memory.id) or True,
    )

    result = reflection.reflect_memories(
        friend,
        force=True,
        api_key="test",
        expected_history_generation=0,
    )

    memory = SemanticMemory.objects.get(friend=friend, source="ai")
    assert result == [{
        "fact": "用户喜欢睡前听轻音乐",
        "category": "preference",
        "confidence": 0.9,
    }]
    assert indexed == [memory.id]
