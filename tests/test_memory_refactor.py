import datetime
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone


def test_chat_day_boundary_and_range():
    from ai.memory.chat_day import get_chat_day, get_chat_day_range

    tz = ZoneInfo("Asia/Shanghai")
    before = timezone.make_aware(datetime.datetime(2026, 7, 9, 2, 59), tz)
    at = timezone.make_aware(datetime.datetime(2026, 7, 9, 3, 0), tz)
    assert get_chat_day(before, 3) == datetime.date(2026, 7, 8)
    assert get_chat_day(at, 3) == datetime.date(2026, 7, 9)
    start, end = get_chat_day_range(datetime.date(2026, 7, 8), 3)
    assert start.hour == 3
    assert end - start == datetime.timedelta(days=1)


def test_detect_day_start_defaults_to_five():
    from ai.memory.chat_day import detect_day_start_hour_from_datetimes

    values = [timezone.make_aware(datetime.datetime(2026, 7, 8, 12, 0), ZoneInfo("Asia/Shanghai"))]
    assert detect_day_start_hour_from_datetimes(values) == 5


@pytest.mark.django_db
def test_analysis_chunks_cover_every_message():
    from django.contrib.auth.models import User
    from web.models.user import UserProfile
    from web.models.character import Character
    from web.models.chat_message import ChatMessage
    from ai.preprocessing.chunker import chunk_messages
    from ai.preprocessing.pipeline import _count_unique_messages

    user = User.objects.create_user(username="analysis-chunks")
    profile = UserProfile.objects.create(user=user)
    character = Character.objects.create(
        author=profile, name="女友", profile="温柔",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
    )
    ChatMessage.objects.bulk_create([
        ChatMessage(
            character=character, sender="用户", content=f"消息{i}",
            timestamp=f"2026-07-08 12:{i % 60:02d}:00", msg_index=i,
        ) for i in range(250)
    ])
    chunks = chunk_messages(character.id)
    covered = {message["msg_index"] for chunk in chunks for message in chunk["messages"]}
    assert len(chunks) >= 3
    assert covered == set(range(250))
    assert len({chunk["chat_day"] for chunk in chunks}) == 1
    assert sum(len(chunk["messages"]) for chunk in chunks) > 250
    assert _count_unique_messages(chunks) == 250


def test_lance_distance_is_normalized_to_higher_relevance():
    from ai.rag.scoring import lance_distance_to_relevance

    assert lance_distance_to_relevance(0.1) > lance_distance_to_relevance(0.9)
    assert lance_distance_to_relevance(0) == 1.0


def test_structured_bubbles_preserve_markdown_newlines():
    from ai.chat.bubbles import parse_bubble_response

    raw = '{"bubbles":["宝宝", "需要准备：\\n1. 身份证\\n2. 手机"]}'
    assert parse_bubble_response(raw) == [
        "宝宝",
        "需要准备：\n1. 身份证\n2. 手机",
    ]


def test_plain_short_chat_lines_become_separate_bubbles():
    from ai.chat.bubbles import parse_bubble_response

    raw = '{"bubbles":["啧啧\\n没事找我就是想我咯\\n嘻嘻"]}'
    assert parse_bubble_response(raw) == [
        "啧啧",
        "没事找我就是想我咯",
        "嘻嘻",
    ]


def test_plain_short_chat_lines_ignore_blank_lines_between_bubbles():
    from ai.chat.bubbles import parse_bubble_response

    raw = (
        '{"bubbles":["哇哦\\n这谁呀好看是好看\\n\\n'
        '哼\\n不会是在看别的小姐姐吧【吃醋】"]}'
    )
    assert parse_bubble_response(raw) == [
        "哇哦",
        "这谁呀好看是好看",
        "哼",
        "不会是在看别的小姐姐吧【吃醋】",
    ]


@pytest.mark.django_db
def test_unified_history_search_finds_online_raw_chat():
    from django.contrib.auth.models import User
    from ai.memory.history_search import ConversationHistorySearch
    from web.models.character import Character
    from web.models.friend import Friend, Message
    from web.models.user import UserProfile

    user = User.objects.create_user(username="online-history-search")
    profile = UserProfile.objects.create(user=user)
    character = Character.objects.create(
        author=profile, name="女友", profile="温柔",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
    )
    friend = Friend.objects.create(me=profile, character=character)
    message = Message.objects.create(
        friend=friend,
        user_message="我最近胃不舒服，已经不能吃辣了",
        input="",
        output="那最近先不吃辣啦",
    )

    hits = ConversationHistorySearch(
        api_key="test", api_base="https://example.invalid"
    ).search(
        ["不能吃辣"],
        friend_id=friend.id,
        character_id=character.id,
    )

    assert hits
    assert hits[0]["source_type"] == "online_chat"
    assert hits[0]["message_refs"] == [message.id]
    assert "胃不舒服" in hits[0]["content"]


@pytest.mark.django_db
def test_reimport_regenerates_existing_style_profile(monkeypatch):
    from django.contrib.auth.models import User
    from ai.preprocessing import pipeline
    from web.models.character import Character
    from web.models.user import UserProfile

    user = User.objects.create_user(username="style-reimport")
    profile = UserProfile.objects.create(user=user)
    character = Character.objects.create(
        author=profile, name="女友", profile="温柔", style_profile="旧风格",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
    )
    chunk = {
        "index": 0,
        "chat_day": "2026-07-08",
        "time_start": "2026-07-08",
        "time_end": "2026-07-08",
        "start_msg_index": 1,
        "end_msg_index": 1,
        "messages": [{"msg_index": 1}],
    }
    result = {
        "chunk_index": 0, "error": False, "chunk_summary": "摘要",
        "topics": [], "key_events": [], "user_fragments": [],
        "girlfriend_fragments": [], "relationship_fragments": [],
    }
    written = {}
    monkeypatch.setattr(pipeline, "require_llm_config", lambda: None)
    monkeypatch.setattr(pipeline, "chunk_messages", lambda *_: [chunk])
    monkeypatch.setattr(pipeline, "analyze_chunk", lambda *args, **kwargs: result)
    monkeypatch.setattr(
        pipeline, "analyze_relationship_overview", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr(
        pipeline, "analyze_style_profile", lambda *args, **kwargs: "重新生成的风格"
    )
    monkeypatch.setattr(
        pipeline,
        "write_results",
        lambda *args, **kwargs: written.update({"style_profile": args[5]}),
    )

    pipeline.run_preprocessing(character.id, max_workers=1)

    assert written["style_profile"] == "重新生成的风格"


@pytest.mark.django_db
def test_preprocessing_resumes_successful_map_chunks(monkeypatch):
    from django.contrib.auth.models import User
    from ai.preprocessing import pipeline
    from web.models.character import Character
    from web.models.user import UserProfile

    user = User.objects.create_user(username="checkpoint-resume")
    profile = UserProfile.objects.create(user=user)
    character = Character.objects.create(
        author=profile,
        name="女友",
        profile="温柔",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
    )
    chunks = [
        {
            "index": index,
            "chat_day": f"2026-07-{index + 1:02d}",
            "time_start": f"2026-07-{index + 1:02d}",
            "time_end": f"2026-07-{index + 1:02d}",
            "start_msg_index": index,
            "end_msg_index": index,
            "messages": [{
                "msg_index": index,
                "sender": "大白鹅",
                "content": f"消息 {index}",
                "timestamp": f"2026-07-{index + 1:02d} 12:00:00",
            }],
        }
        for index in range(2)
    ]
    calls = []

    def analyze(chunk, *args, **kwargs):
        calls.append(chunk["index"])
        return {
            "chunk_index": chunk["index"],
            "error": False,
            "chunk_summary": "摘要",
            "topics": [],
            "key_events": [],
            "user_fragments": [],
            "girlfriend_fragments": [],
            "relationship_fragments": [],
        }

    monkeypatch.setattr(pipeline, "require_llm_config", lambda: None)
    monkeypatch.setattr(pipeline, "chunk_messages", lambda *_: chunks)
    monkeypatch.setattr(pipeline, "analyze_chunk", analyze)
    monkeypatch.setattr(pipeline, "analyze_relationship_overview", lambda *args, **kwargs: {})
    monkeypatch.setattr(pipeline, "analyze_style_profile", lambda *args, **kwargs: "风格")
    monkeypatch.setattr(pipeline, "write_results", lambda *args, **kwargs: None)

    pipeline.run_preprocessing(character.id, max_workers=1)
    pipeline.run_preprocessing(character.id, max_workers=1)

    assert calls == [0, 1]


def test_fragment_evidence_indices_are_validated():
    from ai.preprocessing.chunk_analyzer import _ensure_fragments

    fragments = _ensure_fragments([
        {
            "fact": "用户喜欢吃火锅",
            "category": "preference",
            "evidence_msg_indices": [10, "11", 999, "bad"],
        }
    ], "identity", {10, 11, 12})
    assert fragments == [{
        "fact": "用户喜欢吃火锅",
        "category": "preference",
        "evidence_msg_indices": [10, 11],
    }]


@pytest.mark.django_db
def test_memory_evidence_deduplicates_message_refs():
    from django.contrib.auth.models import User
    from ai.memory.semantic import add_memory_evidence
    from web.models.character import Character
    from web.models.friend import Friend
    from web.models.memory import MemoryEvidence, SemanticMemory
    from web.models.user import UserProfile

    user = User.objects.create_user(username="memory-evidence")
    profile = UserProfile.objects.create(user=user)
    character = Character.objects.create(
        author=profile, name="女友", profile="温柔",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
    )
    friend = Friend.objects.create(me=profile, character=character)
    memory = SemanticMemory.objects.create(
        friend=friend, fact="用户喜欢吃火锅", subject="user", category="preference"
    )

    first = add_memory_evidence(
        memory, source_type="import_chat", message_refs=[3, 2, 2, 1], excerpt="证据"
    )
    second = add_memory_evidence(
        memory, source_type="import_chat", message_refs=[1, 2, 3]
    )

    assert first.id == second.id
    assert second.message_refs == [1, 2, 3]
    assert MemoryEvidence.objects.filter(memory=memory).count() == 1


@pytest.mark.django_db
def test_failed_semantic_rebuild_keeps_previous_table(monkeypatch):
    from django.contrib.auth.models import User
    from ai.memory import semantic as module
    from web.models.character import Character
    from web.models.friend import Friend
    from web.models.memory import SemanticMemory
    from web.models.user import UserProfile

    user = User.objects.create_user(username="semantic-rebuild")
    profile = UserProfile.objects.create(user=user)
    character = Character.objects.create(
        author=profile, name="女友", profile="温柔",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
    )
    friend = Friend.objects.create(me=profile, character=character)
    SemanticMemory.objects.create(
        friend=friend, fact="用户喜欢吃火锅", subject="user", category="preference"
    )
    original_table = f"semantic_{friend.id}"

    class Listing:
        tables = [original_table]

    class FakeDB:
        def __init__(self):
            self.dropped = []
            self.renamed = []

        def list_tables(self):
            return Listing()

        def drop_table(self, name):
            self.dropped.append(name)

        def rename_table(self, old, new):
            self.renamed.append((old, new))

    fake_db = FakeDB()
    monkeypatch.setattr(module.lancedb, "connect", lambda *_: fake_db)
    monkeypatch.setattr(
        module.LanceDB,
        "from_texts",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("embedding failed")),
    )

    assert module.rebuild_semantic_index(friend.id) is False
    assert original_table not in fake_db.dropped
    assert fake_db.renamed == []


def test_supervisor_skips_memory_for_chat_and_time():
    from langchain_core.messages import HumanMessage
    from ai.agents.supervisor import supervisor_node

    assert supervisor_node({"messages": [HumanMessage(content="你好呀")]})["delegate_to"] == "conversation"
    assert supervisor_node({"messages": [HumanMessage(content="现在几点了")]})["intent"] == "time"
    assert supervisor_node({"messages": [HumanMessage(content="还记得第一次见面吗")]})["delegate_to"] == "memory"


def test_conversation_builds_one_system_message(monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage
    from ai.agents.conversation_agent import conversation_agent_node

    captured = {}

    class FakeLLM:
        def invoke(self, messages):
            captured["messages"] = messages
            return AIMessage(content='{"bubbles":["好的", "宝宝"]}')

    monkeypatch.setattr("ai.agents.conversation_agent.ChatOpenAI", lambda **kwargs: FakeLLM())
    result = conversation_agent_node({
        "messages": [HumanMessage(content="现在几点")],
        "base_system_prompt": "核心规则",
        "character_profile": "温柔",
        "style_profile": "称呼用户为宝宝",
        "time_context": "当前时间 12:00",
        "memory_context": "",
        "emotion_analysis": None,
    }, api_key="test", api_base="https://example.invalid")
    assert sum(message.type == "system" for message in captured["messages"]) == 1
    assert "称呼用户为宝宝" in captured["messages"][0].content
    assert "Friend.memory" not in captured["messages"][0].content
    assert result["messages"][0].content == "好的\n宝宝"
    assert result["messages"][0].additional_kwargs["bubbles"] == ["好的", "宝宝"]


@pytest.mark.django_db
def test_conversation_context_keeps_small_history_without_summarizing(monkeypatch):
    from django.contrib.auth.models import User
    from ai.memory.conversation_summary import prepare_conversation_context
    from web.models.character import Character
    from web.models.friend import Friend, Message
    from web.models.user import UserProfile

    profile = UserProfile.objects.create(
        user=User.objects.create_user(username="context-small-history")
    )
    character = Character.objects.create(
        author=profile, name="女友", profile="温柔",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
    )
    friend = Friend.objects.create(me=profile, character=character)
    for index in range(12):
        Message.objects.create(
            friend=friend, user_message=f"用户{index}", input="", output=f"回复{index}"
        )
    monkeypatch.setattr(
        "ai.memory.conversation_summary._summarize_batch",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("不应压缩")),
    )

    summary, recent = prepare_conversation_context(friend)

    assert summary == ""
    assert len(recent) == 12
    assert recent[0].user_message == "用户0"


@pytest.mark.django_db
def test_conversation_context_compacts_old_turns_and_restores_latest_ten(monkeypatch):
    from django.contrib.auth.models import User
    from ai.memory.conversation_summary import prepare_conversation_context
    from web.models.character import Character
    from web.models.friend import Friend, Message
    from web.models.user import UserProfile

    profile = UserProfile.objects.create(
        user=User.objects.create_user(username="context-compaction")
    )
    character = Character.objects.create(
        author=profile, name="女友", profile="温柔",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
    )
    friend = Friend.objects.create(me=profile, character=character)
    rows = [
        Message.objects.create(
            friend=friend, user_message=f"用户{index}", input="", output=f"回复{index}"
        )
        for index in range(16)
    ]
    calls = []

    def summarize(previous, batch, *args, **kwargs):
        calls.append([message.id for message in batch])
        return "较早对话摘要：用户0到用户5已经讨论完。"

    monkeypatch.setattr("ai.memory.conversation_summary._summarize_batch", summarize)

    summary, recent = prepare_conversation_context(friend)
    friend.refresh_from_db()

    assert calls == [[message.id for message in rows[:6]]]
    assert summary.startswith("较早对话摘要")
    assert [message.id for message in recent] == [message.id for message in rows[-10:]]
    assert friend.summary_through_message_id == rows[5].id


def test_conversation_prompt_includes_older_conversation_summary(monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage
    from ai.agents.conversation_agent import conversation_agent_node

    captured = {}

    class FakeLLM:
        def invoke(self, messages):
            captured["messages"] = messages
            return AIMessage(content='{"bubbles":["记得呀"]}')

    monkeypatch.setattr("ai.agents.conversation_agent.ChatOpenAI", lambda **kwargs: FakeLLM())
    conversation_agent_node({
        "messages": [HumanMessage(content="然后呢")],
        "base_system_prompt": "核心规则",
        "character_profile": "温柔",
        "style_profile": "",
        "time_context": "",
        "conversation_summary": "之前正在讨论下周去杭州，用户还没确定日期。",
        "memory_context": "",
        "emotion_analysis": None,
    }, api_key="test", api_base="https://example.invalid")

    assert "之前正在讨论下周去杭州" in captured["messages"][0].content


@pytest.mark.django_db
def test_reflection_jobs_are_unique_and_claimed_once(monkeypatch):
    from django.contrib.auth.models import User
    from ai.memory import reflection_jobs as module
    from web.models.character import Character
    from web.models.friend import Friend
    from web.models.reflection_job import ReflectionJob
    from web.models.user import UserProfile

    user = User.objects.create_user(username="reflection-job")
    profile = UserProfile.objects.create(user=user)
    character = Character.objects.create(
        author=profile, name="女友", profile="温柔",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
    )
    friend = Friend.objects.create(me=profile, character=character)
    job = ReflectionJob.objects.create(
        friend=friend, chat_day=datetime.date(2026, 7, 8)
    )
    calls = []
    monkeypatch.setattr(
        module,
        "reflect_memories",
        lambda current_friend, target_chat_day: calls.append(
            (current_friend.id, target_chat_day)
        ),
    )

    first = module.process_pending_reflection_jobs(friend_id=friend.id, limit=3)
    second = module.process_pending_reflection_jobs(friend_id=friend.id, limit=3)
    job.refresh_from_db()

    assert first == {"done": 1, "failed": 0}
    assert second == {"done": 0, "failed": 0}
    assert calls == [(friend.id, datetime.date(2026, 7, 8))]
    assert job.status == "done"
    assert job.attempts == 1


def test_busy_day_uses_conditional_llm_reduce(monkeypatch):
    from ai.preprocessing import relationship_overview as module

    calls = []
    monkeypatch.setattr(module, "_reduce_period_entry", lambda label, items, *args: (
        calls.append((label, len(items))) or module._aggregate_period_entry(label, items)
    ))
    entries = [
        {"chat_day": "2026-07-08", "time_start": "2026-07-08", "time_end": "2026-07-08",
         "start_msg_index": index, "end_msg_index": index, "summary": f"摘要{index}",
         "key_events": [], "relationship_facts": [], "topics": []}
        for index in range(2)
    ]
    days = module._build_day_entries(entries, "女友", "", "")
    assert calls == [("2026-07-08", 2)]
    assert len(days) == 1


def test_emoji_context_uses_llm_supervisor(monkeypatch):
    from langchain_core.messages import HumanMessage
    from ai.agents import supervisor as module

    monkeypatch.setattr(module, "_classify_with_llm", lambda *args: {
        "intent": "emotional", "delegate_to": "emotion", "classification_source": "llm",
    })
    result = module.supervisor_node({
        "messages": [HumanMessage(content="🙂‍↕️")],
        "emotion_context": [{"emoji": "🙂‍↕️", "meaning": "不满、别扭"}],
    })
    assert result["delegate_to"] == "emotion"


def test_emoji_meaning_does_not_modify_user_message():
    from api.chat import _build_conversation_messages

    messages = _build_conversation_messages(
        "你还记得那次吗🙂‍↕️",
        [{"emoji": "🙂‍↕️", "meaning": "不满、别扭、有点抗拒"}],
        [],
    )

    assert messages[-1].content == "你还记得那次吗🙂‍↕️"
    assert "用户表情含义" not in messages[-1].content


def test_emotion_and_memory_each_run_at_most_once():
    from langchain_core.messages import HumanMessage
    from ai.agents.supervisor_graph import route_after_emotion, route_after_memory

    state = {
        "messages": [HumanMessage(content="你记得上次我很难过吗")],
        "intent": "emotional",
        "memory_done": False,
        "emotion_done": True,
    }
    assert route_after_emotion(state) == "memory"

    state["memory_done"] = True
    assert route_after_memory(state) == "conversation"
    assert route_after_emotion(state) == "conversation"


def test_supervisor_graph_does_not_loop_for_emotional_recall(monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage
    from ai.agents import supervisor_graph as module

    calls = []
    monkeypatch.setattr(module, "supervisor_node", lambda state: {
        "intent": "emotional", "delegate_to": "emotion",
    })
    monkeypatch.setattr(module, "emotion_agent_node", lambda state: (
        calls.append("emotion") or {"emotion_analysis": {"intensity": 8}}
    ))
    monkeypatch.setattr(module, "memory_agent_node", lambda state: (
        calls.append("memory") or {"memory_context": "上次很难过", "semantic_facts": []}
    ))
    monkeypatch.setattr(module, "conversation_agent_node", lambda state: (
        calls.append("conversation") or {"messages": [AIMessage(content="我记得")]}
    ))
    app = module.create_supervisor_app()

    result = app.invoke({
        "messages": [HumanMessage(content="你记得上次我很难过吗")],
        "intent": "",
        "delegate_to": "",
        "memory_context": "",
        "emotion_analysis": None,
        "character_profile": "温柔",
        "style_profile": "",
        "base_system_prompt": "",
        "time_context": "",
        "character_name": "女友",
        "chat_sender_name": "女友",
        "semantic_facts": [],
        "friend_id": 0,
        "character_id": None,
    })

    assert calls == ["emotion", "memory", "conversation"]
    assert result["messages"][-1].content == "我记得"


@pytest.mark.django_db
def test_private_imported_context_is_visible_only_to_character_owner():
    from django.contrib.auth.models import User
    from ai.memory.import_access import can_access_imported_context
    from web.models.character import Character
    from web.models.friend import Friend
    from web.models.user import UserProfile

    owner = UserProfile.objects.create(
        user=User.objects.create_user(username="private-import-owner")
    )
    stranger = UserProfile.objects.create(
        user=User.objects.create_user(username="private-import-stranger")
    )
    character = Character.objects.create(
        author=owner,
        name="女友",
        profile="温柔",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
    )
    owner_friend = Friend.objects.create(me=owner, character=character)
    stranger_friend = Friend.objects.create(me=stranger, character=character)

    assert character.imported_memory_visibility == "private"
    assert can_access_imported_context(owner_friend) is True
    assert can_access_imported_context(stranger_friend) is False

    character.imported_memory_visibility = "public"
    character.save(update_fields=["imported_memory_visibility"])
    assert can_access_imported_context(stranger_friend) is True


@pytest.mark.django_db
def test_private_friend_history_search_receives_no_character_id(monkeypatch):
    from django.contrib.auth.models import User
    from langchain_core.messages import HumanMessage
    from ai.agents import memory_agent as module
    from web.models.character import Character
    from web.models.chat_message import ChatMessage
    from web.models.friend import Friend, Message
    from web.models.user import UserProfile

    owner = UserProfile.objects.create(
        user=User.objects.create_user(username="raw-private-owner")
    )
    stranger = UserProfile.objects.create(
        user=User.objects.create_user(username="raw-private-stranger")
    )
    character = Character.objects.create(
        author=owner,
        name="女友",
        profile="温柔",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
    )
    friend = Friend.objects.create(me=stranger, character=character)
    ChatMessage.objects.create(
        character=character,
        sender="女友",
        content="只有作者能看的秘密",
        timestamp="2026-07-01 12:00:00",
        msg_index=1,
    )
    Message.objects.create(friend=friend, user_message="普通在线消息", input="", output="好")
    captured = {}
    monkeypatch.setattr(module, "search_semantic", lambda *args, **kwargs: [])
    monkeypatch.setattr(module.Reranker, "rerank", lambda self, query, docs, top_k: docs)

    def fake_search(self, queries, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(module.ConversationHistorySearch, "search", fake_search)
    module.memory_agent_node({
        "messages": [HumanMessage(content="还记得以前的秘密吗")],
        "intent": "recall",
        "friend_id": friend.id,
        "character_id": character.id,
        "semantic_facts": [],
    }, api_key="test", api_base="https://example.invalid")

    assert captured["character_id"] is None


@pytest.mark.django_db
def test_private_friend_semantic_search_excludes_imported_facts():
    from django.contrib.auth.models import User
    from ai.memory.semantic import search_semantic
    from web.models.character import Character
    from web.models.friend import Friend
    from web.models.memory import SemanticMemory
    from web.models.user import UserProfile

    profile = UserProfile.objects.create(
        user=User.objects.create_user(username="private-semantic")
    )
    character = Character.objects.create(
        author=profile,
        name="女友",
        profile="温柔",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
    )
    friend = Friend.objects.create(me=profile, character=character)
    SemanticMemory.objects.create(
        friend=friend, fact="用户喜欢草莓蛋糕", source="import", subject="user"
    )
    visible = SemanticMemory.objects.create(
        friend=friend, fact="用户最近喜欢草莓蛋糕", source="user", subject="user"
    )

    results = search_semantic(
        friend.id, "草莓蛋糕", include_imported=False, top_k=10
    )

    assert [item["id"] for item in results] == [visible.id]


@pytest.mark.django_db
def test_visibility_change_reconciles_imported_memory_projections(monkeypatch):
    from django.contrib.auth.models import User
    from ai.memory.import_access import set_imported_context_visibility
    from web.models.character import Character
    from web.models.friend import Friend
    from web.models.memory import MemoryEvidence, SemanticMemory
    from web.models.user import UserProfile

    owner = UserProfile.objects.create(
        user=User.objects.create_user(username="visibility-owner")
    )
    stranger = UserProfile.objects.create(
        user=User.objects.create_user(username="visibility-stranger")
    )
    character = Character.objects.create(
        author=owner,
        name="女友",
        profile="温柔",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
    )
    owner_friend = Friend.objects.create(me=owner, character=character)
    stranger_friend = Friend.objects.create(me=stranger, character=character)
    source = SemanticMemory.objects.create(
        friend=owner_friend,
        fact="两人第一次旅行去了杭州",
        source="import",
        subject="relationship",
        category="experience",
        is_mutable=False,
        is_locked=True,
    )
    MemoryEvidence.objects.create(
        memory=source,
        source_type="import_chat",
        message_refs=[10, 11],
        start_message_ref=10,
        end_message_ref=11,
    )
    monkeypatch.setattr("ai.memory.semantic.rebuild_semantic_index", lambda *_: True)
    monkeypatch.setattr("ai.memory.semantic.sync_friend_memory_cache", lambda *_: None)

    set_imported_context_visibility(character, "public")
    copied = SemanticMemory.objects.get(friend=stranger_friend, source="import")
    assert copied.fact == source.fact
    assert copied.evidences.get().message_refs == [10, 11]

    set_imported_context_visibility(character, "private")
    assert not SemanticMemory.objects.filter(
        friend=stranger_friend, source="import"
    ).exists()
    assert SemanticMemory.objects.filter(friend=owner_friend, source="import").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("message_count,text", [(4, "长文本" * 100), (5, "短")])
def test_auto_reflection_skips_low_signal_completed_day(monkeypatch, message_count, text):
    from django.contrib.auth.models import User
    from web.models.user import UserProfile
    from web.models.character import Character
    from web.models.friend import Friend, Message
    from ai.memory.reflection import reflect_memories

    user = User.objects.create_user(username=f"reflection-gate-{message_count}-{len(text)}")
    profile = UserProfile.objects.create(user=user)
    character = Character.objects.create(
        author=profile, name="女友", profile="温柔",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
    )
    friend = Friend.objects.create(me=profile, character=character)
    created = timezone.make_aware(
        datetime.datetime(2026, 7, 8, 12, 0), ZoneInfo("Asia/Shanghai")
    )
    for index in range(message_count):
        Message.objects.create(
            friend=friend, user_message=text, input="", output="好",
            create_time=created + datetime.timedelta(minutes=index),
        )
    monkeypatch.setattr(
        "ai.memory.reflection._get_client",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("LLM should not run")),
    )
    assert reflect_memories(friend) == []
    friend.refresh_from_db()
    assert friend.last_reflected_chat_day == datetime.date(2026, 7, 8)


def test_preprocessing_does_not_write_partial_map_results(monkeypatch):
    """One successful chunk must never make an incomplete import look done."""
    from types import SimpleNamespace
    from ai.preprocessing import pipeline

    chunks = [
        {"index": 0, "messages": [{"msg_index": 0, "content": "a"}]},
        {"index": 1, "messages": [{"msg_index": 1, "content": "b"}]},
    ]
    statuses = []
    writes = []
    monkeypatch.setattr(pipeline, "require_llm_config", lambda: None)
    monkeypatch.setattr(
        pipeline.Character.objects, "get",
        lambda **kwargs: SimpleNamespace(name="女友", chat_sender_name="大白鹅"),
    )
    monkeypatch.setattr(pipeline, "chunk_messages", lambda *_: chunks)
    monkeypatch.setattr(
        pipeline, "analyze_chunk",
        lambda chunk, *args, **kwargs: (
            {"chunk_index": 0, "chunk_summary": "ok"}
            if chunk["index"] == 0
            else {"chunk_index": 1, "error": True, "error_msg": "429 insufficient balance"}
        ),
    )
    monkeypatch.setattr(pipeline, "_save_checkpoint", lambda *args: None)
    monkeypatch.setattr(pipeline, "_set_status", lambda _id, status, error="": statuses.append((status, error)))
    monkeypatch.setattr(pipeline, "_initialize_progress", lambda *args: None)
    monkeypatch.setattr(pipeline, "_update_chunk_progress", lambda *args: None)
    monkeypatch.setattr(pipeline, "write_results", lambda *args, **kwargs: writes.append(args))
    monkeypatch.setattr(
        pipeline.PreprocessingCheckpoint.objects, "filter",
        lambda **kwargs: [],
    )

    pipeline.run_preprocessing(1, max_workers=1)

    assert not writes
    assert statuses[-1][0] == "partial"
