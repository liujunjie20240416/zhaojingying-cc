import pytest


class TestEpisodicMemory:
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


class TestReflection:
    @pytest.mark.django_db
    def test_reflection_skip_when_recent(self):
        """距上次 reflection < 6h 应跳过"""
        from django.utils.timezone import now
        from ai.memory.reflection import reflect_memories
        friend = type("f", (), {
            "id": 1,
            "last_reflection_time": now(),
        })()
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
