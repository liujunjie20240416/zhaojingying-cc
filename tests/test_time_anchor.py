"""读取端加固:相对时间词检测、注释与检索排序。

背景:历史写入的 fact 可能只含相对时间("用户本周过生日")而没有绝对日期,
检索时会被当作当前事实注入,导致 AI 断言"你生日就这周嘛"。这些测试锁定
检测规则和读取端的降权/注释行为。
"""
import datetime

import pytest


def _make_friend(user, username="anchor-test"):
    from django.contrib.auth.models import User
    from storage.models.user import UserProfile
    from storage.models.character import Character
    from storage.models.friend import Friend

    user = User.objects.create_user(username=username)
    profile = UserProfile.objects.create(user=user)
    character = Character.objects.create(
        author=profile,
        name="女友",
        profile="温柔",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
    )
    return Friend.objects.create(me=profile, character=character)


class TestFindUnanchoredRelativeTime:
    def test_relative_week_detected(self):
        from ai.memory.time_anchor import find_unanchored_relative_time

        assert find_unanchored_relative_time("用户本周过生日，生日当天还要上课") == "本周"

    def test_relative_day_detected(self):
        from ai.memory.time_anchor import find_unanchored_relative_time

        assert find_unanchored_relative_time("大白鹅当天去做核酸") == "当天"
        assert find_unanchored_relative_time("用户今天去了医院") == "今天"

    def test_relative_recent_detected(self):
        from ai.memory.time_anchor import find_unanchored_relative_time

        assert find_unanchored_relative_time("用户最近开始健身") == "最近"

    def test_month_day_is_anchored(self):
        from ai.memory.time_anchor import find_unanchored_relative_time

        assert find_unanchored_relative_time("用户生日是2月3号") is None

    def test_full_date_is_anchored(self):
        from ai.memory.time_anchor import find_unanchored_relative_time

        assert find_unanchored_relative_time("两人2024年2月1日聊过生日") is None
        assert find_unanchored_relative_time("2024-02-01 用户说这周过生日") is None

    def test_relative_word_beside_absolute_date_is_safe(self):
        from ai.memory.time_anchor import find_unanchored_relative_time

        assert find_unanchored_relative_time("2024年2月这周过生日") is None

    def test_weekend_is_not_a_stale_relative_term(self):
        from ai.memory.time_anchor import find_unanchored_relative_time

        assert find_unanchored_relative_time("两人周末经常一起做饭") is None

    def test_plain_day_is_not_a_relative_term(self):
        from ai.memory.time_anchor import find_unanchored_relative_time

        assert find_unanchored_relative_time("用户喜欢白天出门") is None

    def test_empty_fact_has_no_relative_time(self):
        from ai.memory.time_anchor import find_unanchored_relative_time

        assert find_unanchored_relative_time("") is None


class TestAnnotateRelativeTimeFact:
    def test_annotation_appended_for_unanchored_fact(self):
        from ai.memory.time_anchor import annotate_relative_time_fact

        assert annotate_relative_time_fact("用户本周过生日") == (
            "用户本周过生日（时间不确定，可能已过期）"
        )

    def test_annotation_skipped_for_anchored_fact(self):
        from ai.memory.time_anchor import annotate_relative_time_fact

        assert annotate_relative_time_fact("用户生日是2月3号") == "用户生日是2月3号"


def test_semantic_ranking_penalises_unanchored_relative_time():
    from ai.agents.memory_agent import _rank_semantic_results

    results = [
        {"id": 1, "fact": "用户本周过生日，生日当天还要上课", "score": 1.0,
         "subject": "user", "category": "identity", "memory_state": "current"},
        {"id": 2, "fact": "用户生日是2月3号", "score": 1.0,
         "subject": "user", "category": "identity", "memory_state": "current"},
    ]
    ranked = _rank_semantic_results(
        results,
        {"target_subject": "user", "category_hint": "identity", "time_mode": "any"},
    )
    assert ranked[0]["id"] == 2
    assert ranked[1]["id"] == 1


@pytest.mark.django_db
def test_core_profile_prefers_date_anchored_facts(monkeypatch):
    monkeypatch.setattr("ai.memory.semantic._index_fact", lambda *args, **kwargs: None)
    from ai.memory.semantic import add_fact, build_core_memory_context

    friend = _make_friend(None)
    # 锚定事实先创建(id 更小),未锚定事实后创建——原逻辑按 -id 会选中后者,
    # 修复后必须优先选锚定事实。
    add_fact(friend, "用户生日是2月3号", category="identity",
             subject="user", confidence=0.7, index=False)
    add_fact(friend, "用户本周过生日，生日当天还要上课", category="identity",
             subject="user", confidence=0.7, index=False)

    context = build_core_memory_context(friend.id)
    assert "用户生日是2月3号" in context
    assert "用户本周过生日" not in context


@pytest.mark.django_db
def test_memory_agent_annotates_and_orders_unanchored_facts(monkeypatch):
    from langchain_core.messages import HumanMessage

    from ai.agents.memory_agent import QueryRewriter, memory_agent_node

    friend = _make_friend(None, username="annotate-node")

    def fake_search(friend_id, query, top_k=10, include_imported=True):
        return [
            {"id": 1, "fact": "用户本周过生日，生日当天还要上课", "category": "identity",
             "confidence": 0.7, "source": "import", "subject": "user",
             "is_locked": True, "is_mutable": False, "memory_state": "current",
             "trajectory_key": "", "score": 1.0},
            {"id": 2, "fact": "用户生日是2月3号", "category": "identity",
             "confidence": 0.7, "source": "import", "subject": "user",
             "is_locked": True, "is_mutable": False, "memory_state": "current",
             "trajectory_key": "", "score": 1.0},
        ]

    def no_llm_plan(*args, **kwargs):
        raise RuntimeError("tests must not call the LLM")

    monkeypatch.setattr("ai.agents.memory_agent.search_semantic", fake_search)
    monkeypatch.setattr(QueryRewriter, "plan", no_llm_plan)
    state = {
        "messages": [HumanMessage(content="你记得我的生日吗")],
        "friend_id": friend.id,
        "character_id": None,
    }
    result = memory_agent_node(state)

    semantic_section = next(
        section["text"] for section in result["memory_sections"]
        if section["kind"] == "semantic"
    )
    anchored_index = semantic_section.index("用户生日是2月3号")
    unanchored_index = semantic_section.index("用户本周过生日")
    assert anchored_index < unanchored_index
    assert "（时间不确定，可能已过期）" in semantic_section


def test_reflection_dialogue_lines_include_timestamps():
    from ai.memory.reflection import _build_dialogue_text

    class FakeMessage:
        def __init__(self, message_id, create_time, user_message, output):
            self.id = message_id
            self.create_time = create_time
            self.user_message = user_message
            self.output = output

    messages = [
        FakeMessage(10, datetime.datetime(2026, 8, 14, 10, 30), "我生日是明天", "真的吗"),
        FakeMessage(11, datetime.datetime(2026, 8, 14, 10, 31), "对呀", "那我要准备礼物"),
    ]
    text = _build_dialogue_text(messages)
    assert "[message_id=10][2026-08-14 10:30]" in text
    assert "[message_id=11][2026-08-14 10:31]" in text
