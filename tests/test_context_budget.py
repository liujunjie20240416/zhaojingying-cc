import pytest


def test_context_budget_prefers_conservative_chinese_estimate():
    from ai.memory.context_budget import context_diagnostics, estimate_tokens

    assert estimate_tokens("你好呀") >= 2
    diagnostics = context_diagnostics({"system_prompt": "规则" * 100})
    assert diagnostics["estimated_input_tokens"] > 0
    assert diagnostics["pressure"] == "normal"


@pytest.mark.django_db
def test_core_memory_context_uses_one_current_fact_per_bucket():
    from django.contrib.auth.models import User
    from ai.memory.semantic import build_core_memory_context
    from storage.models.character import Character
    from storage.models.friend import Friend
    from storage.models.memory import SemanticMemory
    from storage.models.user import UserProfile

    profile = UserProfile.objects.create(user=User.objects.create_user(username="core-profile"))
    character = Character.objects.create(
        author=profile, name="女友", profile="温柔",
        photo="character/photos/default.jpg", background_image="character/background_images/default.jpg",
    )
    friend = Friend.objects.create(me=profile, character=character)
    SemanticMemory.objects.create(
        friend=friend, subject="user", category="preference", fact="用户喜欢火锅",
        confidence=0.7,
    )
    SemanticMemory.objects.create(
        friend=friend, subject="user", category="preference", fact="用户喜欢烧烤",
        confidence=0.5,
    )
    SemanticMemory.objects.create(
        friend=friend, subject="relationship", category="relationship", fact="两人习惯互道晚安",
        confidence=0.8,
    )

    result = build_core_memory_context(friend.id)

    assert "用户喜欢火锅" in result
    assert "用户喜欢烧烤" not in result
    assert "两人习惯互道晚安" in result


def test_implicit_reference_requests_lightweight_recall():
    from ai.memory.intent import detect_memory_intent

    assert detect_memory_intent("那个作业后来怎么样了")["needs_lightweight_recall"] is True
    assert detect_memory_intent("晚安呀")["needs_lightweight_recall"] is False


def test_historical_question_requests_state_trajectory():
    from ai.memory.intent import detect_memory_intent

    assert detect_memory_intent("我以前是不是不能吃辣？")["needs_state_trajectory"] is True
    assert detect_memory_intent("今晚吃什么呀")["needs_state_trajectory"] is False


def test_dynamic_projection_trims_retrieval_before_stable_context(monkeypatch):
    from ai.memory import context_budget

    monkeypatch.setattr(context_budget, "SOFT_INPUT_TOKEN_BUDGET", 80)
    monkeypatch.setattr(context_budget, "HARD_INPUT_TOKEN_BUDGET", 100)
    result = context_budget.project_dynamic_context(
        stable_prefix="角色规则" * 10,
        recent_messages="最近对话" * 10,
        summary="旧摘要" * 20,
        memory_context="检索证据" * 100,
    )

    assert result["memory_budget"] < context_budget.MEMORY_CONTEXT_TOKEN_BUDGET
    assert len(result["memory_context"]) < len("检索证据" * 100)


def test_quote_budget_prioritizes_raw_evidence():
    from ai.memory.context_budget import assemble_memory_sections, estimate_tokens

    result = assemble_memory_sections([
        {"kind": "semantic", "text": "【语义事实】\n" + "偏好" * 600},
        {"kind": "raw_evidence", "text": "【相关聊天原文】\n" + "原话" * 600},
        {"kind": "collapse", "text": "【历史阶段】\n" + "阶段" * 400},
    ], 1200, {"request_mode": "quote"})

    assert result.startswith("【相关聊天原文】")
    assert estimate_tokens(result) <= 1200


def test_current_fact_budget_prioritizes_semantic_memory():
    from ai.memory.context_budget import assemble_memory_sections

    result = assemble_memory_sections([
        {"kind": "raw_evidence", "text": "【相关聊天原文】\n" + "原话" * 600},
        {"kind": "semantic", "text": "【用户记忆】\n用户现在喜欢火锅"},
    ], 600, {"time_mode": "current", "category_hint": "preference"})

    assert result.startswith("【用户记忆】")


@pytest.mark.django_db
def test_state_trajectory_keeps_every_known_phase():
    from django.contrib.auth.models import User
    from django.utils.timezone import now
    from ai.memory.semantic import expand_state_trajectories, resolve_conflict
    from storage.models.character import Character
    from storage.models.friend import Friend
    from storage.models.memory import SemanticMemory
    from storage.models.user import UserProfile

    profile = UserProfile.objects.create(user=User.objects.create_user(username="trajectory-user"))
    character = Character.objects.create(
        author=profile, name="女友", profile="温柔",
        photo="character/photos/default.jpg", background_image="character/background_images/default.jpg",
    )
    friend = Friend.objects.create(me=profile, character=character)
    first = SemanticMemory.objects.create(
        friend=friend, fact="用户喜欢吃辣", subject="user", category="preference",
        trajectory_key="user.preference.spiciness", valid_from=now(),
    )
    second = resolve_conflict(
        friend.id, first.fact, "用户胃不舒服时暂时不能吃辣",
        trajectory_key="user.preference.spiciness", index=False,
    )
    third = resolve_conflict(
        friend.id, second.fact, "用户后来又可以吃辣了",
        trajectory_key="user.preference.spiciness", index=False,
    )

    expanded = expand_state_trajectories(friend.id, [{
        "id": second.id, "fact": second.fact, "trajectory_key": second.trajectory_key,
        "memory_state": second.memory_state, "score": 1,
    }])

    assert {item["id"] for item in expanded} == {first.id, second.id, third.id}
    assert SemanticMemory.objects.get(id=first.id).memory_state == "historical"


@pytest.mark.django_db
def test_historical_collapse_is_a_projection_not_a_deletion():
    from django.contrib.auth.models import User
    from ai.memory.conversation_summary import search_conversation_collapses
    from storage.models.character import Character
    from storage.models.friend import Friend, Message
    from storage.models.memory import ConversationCollapse
    from storage.models.user import UserProfile

    profile = UserProfile.objects.create(user=User.objects.create_user(username="collapse-user"))
    character = Character.objects.create(
        author=profile, name="女友", profile="温柔",
        photo="character/photos/default.jpg", background_image="character/background_images/default.jpg",
    )
    friend = Friend.objects.create(me=profile, character=character)
    message = Message.objects.create(friend=friend, user_message="说起樱花", input="", output="我们约好去看")
    ConversationCollapse.objects.create(
        friend=friend, start_message_id=message.id, end_message_id=message.id,
        summary="两人约好春天一起看樱花", topics=["樱花", "约定"], token_count=10,
    )

    results = search_conversation_collapses(friend.id, "以前说的樱花约定", time_mode="historical")

    assert results[0].summary.startswith("两人约好")
    assert Message.objects.filter(id=message.id).exists()
