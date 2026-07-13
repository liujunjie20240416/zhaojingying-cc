import pytest


class TestEpisodicMemory:
    @pytest.mark.llm_integration
    def test_extract_episodic_info(self, api_key, api_base):
        from ai.memory.episodic import extract_episodic_info
        result = extract_episodic_info("我今天吃了火锅，超好吃", "火锅确实很棒！你最喜欢哪家店？",
                                       api_key=api_key, api_base=api_base)
        assert "summary" in result
        assert "keywords" in result
        assert "importance" in result
        assert len(result["summary"]) > 0
        assert 0 <= result["importance"] <= 1

    def test_write_episodic_filters_low_importance(self, api_key, api_base):
        """低 importance 的对话应跳过不写"""
        from unittest.mock import patch
        from ai.memory.episodic import write_episodic
        with patch("ai.memory.episodic.extract_episodic_info") as mock_extract:
            mock_extract.return_value = {"summary": "test", "keywords": "test", "importance": 0.2}
            result = write_episodic(None, "你好", "你好呀", api_key=api_key, api_base=api_base)
            assert result is None


class TestSemanticMemory:
    @pytest.mark.django_db
    def test_get_active_facts_empty(self):
        from ai.memory.semantic import get_active_facts
        facts = get_active_facts(99999)
        assert isinstance(facts, list)
        assert len(facts) == 0

    @pytest.mark.django_db
    def test_category_choices(self):
        """验证分类已收紧为 4 类，不包含 personality 和 other"""
        from web.models.memory import SemanticMemory
        cats = [c[0] for c in SemanticMemory.CATEGORY_CHOICES]
        assert "identity" in cats
        assert "preference" in cats
        assert "experience" in cats
        assert "relationship" in cats
        assert "personality" not in cats
        assert "other" not in cats
        assert len(cats) == 4

    @pytest.mark.django_db
    def test_subject_choices(self):
        from web.models.memory import SemanticMemory
        subjects = [s[0] for s in SemanticMemory.SUBJECT_CHOICES]
        assert subjects == ["user", "girlfriend", "relationship"]

    def test_default_mutability_policy(self):
        from ai.memory.semantic import default_mutability
        assert default_mutability("girlfriend", "identity", "import") is False
        assert default_mutability("relationship", "experience", "import") is False
        assert default_mutability("relationship", "relationship", "import") is False
        assert default_mutability("user", "preference", "ai") is True
        assert default_mutability("user", "identity", "ai") is False

    @pytest.mark.django_db
    def test_resolve_conflict_archives_old_preference(self, monkeypatch):
        from ai.memory.semantic import add_fact, resolve_conflict
        from web.models.memory import SemanticMemory
        from django.contrib.auth.models import User
        from web.models.user import UserProfile
        from web.models.character import Character
        from web.models.friend import Friend

        monkeypatch.setattr("ai.memory.semantic._index_fact", lambda *args, **kwargs: None)
        user = User.objects.create_user(username="memory-test")
        profile = UserProfile.objects.create(user=user)
        character = Character.objects.create(
            author=profile,
            name="女友",
            profile="温柔",
            photo="character/photos/default.jpg",
            background_image="character/background_images/default.jpg",
        )
        friend = Friend.objects.create(me=profile, character=character)
        old = add_fact(
            friend=friend,
            fact="用户喜欢吃辣",
            subject="user",
            category="preference",
            source="ai",
        )

        new = resolve_conflict(friend.id, "用户喜欢吃辣", "用户现在不能吃辣")
        old.refresh_from_db()

        assert old.is_active is True
        assert old.memory_state == "historical"
        assert old.valid_to is not None
        assert old.replaced_by_id == new.id
        assert new.memory_state == "current"

        current = SemanticMemory.objects.filter(friend_id=friend.id, memory_state="current")
        historical = SemanticMemory.objects.filter(friend_id=friend.id, memory_state="historical")
        assert current.count() == 1
        assert historical.count() == 1


class TestMemoryIntent:
    def test_detect_historical_user_preference(self):
        from ai.memory.intent import detect_memory_intent
        intent = detect_memory_intent("我以前是不是不能吃辣？")
        assert intent["target_subject"] == "user"
        assert intent["time_mode"] == "historical"
        assert intent["category_hint"] == "preference"
        assert intent["needs_raw_chat"] is True

    def test_detect_relationship_early_recall(self):
        from ai.memory.intent import detect_memory_intent
        intent = detect_memory_intent("你还记得我们刚认识的时候吗")
        assert intent["target_subject"] == "relationship"
        assert intent["time_mode"] == "early"
        assert intent["needs_raw_chat"] is True


class TestRelationshipOverview:
    def test_relationship_overview_fallback(self, monkeypatch):
        from ai.preprocessing.relationship_overview import analyze_relationship_overview

        def fail_analyze(*args, **kwargs):
            raise RuntimeError("api failed")

        monkeypatch.setattr("ai.preprocessing.relationship_overview._do_analyze", fail_analyze)
        chunks = [{
            "index": 0,
            "time_start": "2024-01-01",
            "time_end": "2024-01-01",
            "start_msg_index": 0,
            "end_msg_index": 10,
        }]
        chunk_results = [{
            "chunk_index": 0,
            "error": False,
            "chunk_summary": "两人开始频繁聊天",
            "key_events": ["两人第一次互道晚安"],
            "relationship_fragments": [{"fact": "两人形成晚安习惯", "category": "relationship"}],
            "topics": ["关系/晚安"],
        }]
        result = analyze_relationship_overview(chunk_results, chunks, "女友")
        assert "overview" in result
        assert "两人开始频繁聊天" in result["overview"]
        assert result["timeline"]["stages"]


class TestPreprocessingChunker:
    @pytest.mark.django_db
    def test_chunk_messages_keeps_msg_index(self):
        from django.contrib.auth.models import User
        from web.models.user import UserProfile
        from web.models.character import Character
        from web.models.chat_message import ChatMessage
        from ai.preprocessing.chunker import chunk_messages

        user = User.objects.create_user(username="chunk-test")
        profile = UserProfile.objects.create(user=user)
        character = Character.objects.create(
            author=profile,
            name="女友",
            profile="温柔",
            photo="character/photos/default.jpg",
            background_image="character/background_images/default.jpg",
        )
        ChatMessage.objects.create(
            character=character,
            sender="用户",
            content="你好",
            timestamp="2024-01-01 12:00:00",
            msg_index=7,
        )

        chunks = chunk_messages(character.id)
        assert chunks[0]["messages"][0]["msg_index"] == 7


class TestReflection:
    @pytest.mark.django_db
    def test_reflection_skip_when_recent(self):
        """没有已完成聊天日时不执行 reflection。"""
        from ai.memory.reflection import reflect_memories
        from django.contrib.auth.models import User
        from web.models.user import UserProfile
        from web.models.character import Character
        from web.models.friend import Friend

        user = User.objects.create_user(username="reflection-empty")
        profile = UserProfile.objects.create(user=user)
        character = Character.objects.create(
            author=profile, name="女友", profile="温柔",
            photo="character/photos/default.jpg",
            background_image="character/background_images/default.jpg",
        )
        friend = Friend.objects.create(me=profile, character=character)
        result = reflect_memories(friend, force=False)
        assert result == []

    @pytest.mark.django_db
    def test_reflection_new_categories(self, api_key, api_base):
        """reflection prompt 应使用新的 category 列表，数据源改为 Message"""
        from ai.memory.reflection import reflect_memories
        import inspect
        source = inspect.getsource(reflect_memories)
        assert "identity" in source
        assert "personality" not in source
        assert '"other"' not in source
        # 确认数据源为 Message，且支持 domain 替换
        assert "Message.objects" in source
        assert "replaces" in source
