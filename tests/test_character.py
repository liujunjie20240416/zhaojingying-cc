import io
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User

from api.character import create_character
from web.models.character import Character, Voice


@pytest.mark.django_db
def test_create_character_accepts_fastapi_upload_file_shape(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = User.objects.create_user(username="creator", password="password")
    voice = Voice.objects.create(name="女", voice_id="longxing_v3")

    result = create_character(
        name="测试角色",
        voice_id=voice.id,
        profile="温柔体贴",
        photo=SimpleNamespace(filename="photo.jpg", file=io.BytesIO(b"photo")),
        background_image=SimpleNamespace(filename="background.jpg", file=io.BytesIO(b"bg")),
        user=user,
    )

    assert result["result"] == "success"
    assert result["character_id"]

    character = Character.objects.get(id=result["character_id"])
    assert character.name == "测试角色"
    assert character.photo.name
    assert character.background_image.name


@pytest.mark.django_db
def test_memory_agent_skips_wechat_search_without_imported_messages(monkeypatch):
    from ai.agents import memory_agent
    from langchain_core.messages import HumanMessage

    def fail_if_called(*args, **kwargs):
        raise AssertionError("No-import characters should not search stale WeChat indexes")

    monkeypatch.setattr(
        memory_agent.ConversationHistorySearch, "search", fail_if_called
    )

    result = memory_agent.memory_agent_node(
        {
            "messages": [HumanMessage(content="你还记得以前的事情吗")],
            "friend_id": 999999,
            "character_id": 999999,
            "semantic_facts": [],
        }
    )

    assert result["memory_context"] == ""
