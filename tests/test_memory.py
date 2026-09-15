import pytest


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

    def test_detect_when_we_met_as_early_recall(self):
        from ai.memory.intent import detect_memory_intent

        assert detect_memory_intent("咱们什么时候认识的")["time_mode"] == "early"


@pytest.mark.django_db
def test_origin_question_uses_earliest_imported_chunk_and_evidence():
    from django.contrib.auth.models import User
    from ai.agents.memory_agent import _imported_anchor_evidence, _search_time_chunks
    from web.models.character import Character
    from web.models.chat_message import ChatMessage
    from web.models.import_analysis import TimeChunk
    from web.models.user import UserProfile

    profile = UserProfile.objects.create(user=User.objects.create_user(username="origin-evidence"))
    character = Character.objects.create(
        author=profile, name="女友", profile="温柔",
        photo="character/photos/default.jpg", background_image="character/background_images/default.jpg",
    )
    TimeChunk.objects.create(character=character, label="最初相识", start_msg_index=1, end_msg_index=2)
    TimeChunk.objects.create(character=character, label="后来聊天", start_msg_index=10, end_msg_index=20, summary="咱们吃什么")
    ChatMessage.objects.create(character=character, sender="用户", content="你好呀", timestamp="2024-01-01", msg_index=1)
    ChatMessage.objects.create(character=character, sender="女友", content="认识你很开心", timestamp="2024-01-01", msg_index=2)

    plan = {
        "temporal_anchor": "origin",
        "source_policy": "import_preferred",
    }
    chunk = _search_time_chunks(character.id, "咱们什么时候认识的", plan)
    evidence = _imported_anchor_evidence(
        character.id,
        (chunk["start_msg_index"], chunk["end_msg_index"]),
        plan,
    )

    assert chunk["label"] == "最初相识"
    assert evidence["source_type"] == "import_chat"
    assert evidence["message_refs"] == [1, 2]


def test_recall_plan_fallback_prefers_imported_history_when_available():
    from ai.rag.query_rewriter import _fallback_plan

    plan = _fallback_plan(
        "我们最初是怎么熟起来的",
        {"time_mode": "early", "needs_raw_chat": True, "target_subject": "relationship"},
        imported_available=True,
    )

    assert plan["source_policy"] == "import_preferred"
    assert plan["evidence_policy"] == "raw_required"


def test_source_diverse_selection_keeps_imported_and_online_evidence():
    from ai.agents.memory_agent import _select_source_diverse_hits

    hits = [
        {"source_type": "online_chat", "message_refs": [10], "content": "在线1", "score": 0.99},
        {"source_type": "online_chat", "message_refs": [11], "content": "在线2", "score": 0.98},
        {"source_type": "import_chat", "message_refs": [20], "content": "导入1", "score": 0.7},
    ]
    selected = _select_source_diverse_hits(
        hits,
        {"source_policy": "balanced"},
        {"source_type": "import_chat", "message_refs": [1], "content": "最早原文", "score": 1.2},
        limit=5,
    )

    assert selected[0]["source_type"] == "import_chat"
    assert {hit["source_type"] for hit in selected} == {"import_chat", "online_chat"}


class TestRelationshipOverview:
    def test_relationship_overview_fallback(self, monkeypatch):
        from ai.ingestion.relationship_overview import analyze_relationship_overview

        def fail_analyze(*args, **kwargs):
            raise RuntimeError("api failed")

        monkeypatch.setattr("ai.ingestion.relationship_overview._do_analyze", fail_analyze)
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
        from ai.ingestion.chunker import chunk_messages

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

    @pytest.mark.django_db
    def test_reflection_prompt_requires_date_anchoring(self):
        """reflection prompt 必须要求相对日期换算为绝对日期"""
        from ai.memory.reflection import reflect_memories
        import inspect
        source = inspect.getsource(reflect_memories)
        assert "日期必须锚定" in source
        assert "绝对日期" in source
        assert "禁止" in source or "不允许" in source
