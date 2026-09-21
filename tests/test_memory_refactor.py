import datetime
import time
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone


def test_chat_day_boundary_and_range():
    from ai.time.chat_day import get_chat_day, get_chat_day_range

    tz = ZoneInfo("Asia/Shanghai")
    before = timezone.make_aware(datetime.datetime(2026, 7, 9, 2, 59), tz)
    at = timezone.make_aware(datetime.datetime(2026, 7, 9, 3, 0), tz)
    assert get_chat_day(before, 3) == datetime.date(2026, 7, 8)
    assert get_chat_day(at, 3) == datetime.date(2026, 7, 9)
    start, end = get_chat_day_range(datetime.date(2026, 7, 8), 3)
    assert start.hour == 3
    assert end - start == datetime.timedelta(days=1)


def test_detect_day_start_defaults_to_five():
    from ai.time.chat_day import detect_day_start_hour_from_datetimes

    values = [timezone.make_aware(datetime.datetime(2026, 7, 8, 12, 0), ZoneInfo("Asia/Shanghai"))]
    assert detect_day_start_hour_from_datetimes(values) == 5


@pytest.mark.django_db
def test_analysis_chunks_cover_every_message():
    from django.contrib.auth.models import User
    from storage.models.user import UserProfile
    from storage.models.character import Character
    from storage.models.chat_message import ChatMessage
    from ai.ingestion.chunker import chunk_messages
    from ai.ingestion.pipeline import _count_unique_messages

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
    from ai.vector_store import lance_distance_to_relevance

    assert lance_distance_to_relevance(0.1) > lance_distance_to_relevance(0.9)
    assert lance_distance_to_relevance(0) == 1.0


def test_structured_bubbles_preserve_markdown_newlines():
    from ai.agents.bubbles import parse_bubble_response

    raw = '{"bubbles":["宝宝", "需要准备：\\n1. 身份证\\n2. 手机"]}'
    assert parse_bubble_response(raw) == [
        "宝宝",
        "需要准备：\n1. 身份证\n2. 手机",
    ]


def test_plain_short_chat_lines_become_separate_bubbles():
    from ai.agents.bubbles import parse_bubble_response

    raw = '{"bubbles":["啧啧\\n没事找我就是想我咯\\n嘻嘻"]}'
    assert parse_bubble_response(raw) == [
        "啧啧",
        "没事找我就是想我咯",
        "嘻嘻",
    ]


def test_plain_short_chat_lines_ignore_blank_lines_between_bubbles():
    from ai.agents.bubbles import parse_bubble_response

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
    from storage.models.character import Character
    from storage.models.friend import Friend, Message
    from storage.models.user import UserProfile

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
    from ai.ingestion import pipeline
    from storage.models.character import Character
    from storage.models.user import UserProfile

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
    from ai.ingestion import pipeline
    from storage.models.character import Character
    from storage.models.user import UserProfile

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
    from ai.ingestion.chunk_analyzer import _ensure_fragments

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


def test_chunk_analyzer_prompt_requires_date_anchoring():
    """导入提取 prompt 必须要求相对日期结合消息时间戳换算为绝对日期。"""
    import inspect
    from ai.ingestion.chunk_analyzer import _do_analyze

    source = inspect.getsource(_do_analyze)
    assert "日期必须锚定" in source
    assert "绝对日期" in source
    assert "时间戳" in source


@pytest.mark.django_db
def test_memory_evidence_deduplicates_message_refs():
    from django.contrib.auth.models import User
    from ai.memory.semantic import add_memory_evidence
    from storage.models.character import Character
    from storage.models.friend import Friend
    from storage.models.memory import MemoryEvidence, SemanticMemory
    from storage.models.user import UserProfile

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
    from storage.models.character import Character
    from storage.models.friend import Friend
    from storage.models.memory import SemanticMemory
    from storage.models.user import UserProfile

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


def test_recall_definition_covers_referential_followups():
    """`recall` 的定义必须包含「没有回忆词、靠前文指代」的追问。

    这条测的是**提示词文本**，不是分类行为——它不能证明模型会判对，只能证明
    这行说明还在。之所以值得钉住：改造前这类消息由路由阶段的
    `detect_memory_intent(...).get("needs_lightweight_recall")` 兜底（旧
    `route_from_supervisor` 把它当 OR 条件用），改造后该信号只在 memory 节点
    **内部**生效，而分类器判 none 时那个节点根本不跑。于是「那个作业后来怎么样
    了」能不能进检索，全押在这行提示词上——删掉它没有任何测试会变红。

    真正测量它需要标注集，目前没有（见 spec §6）。这条测试只保证守卫不被
    悄悄拆掉，不保证守卫有效。
    """
    from ai.agents.supervisor import _build_classifier_prompt

    prompt = _build_classifier_prompt("那个作业后来怎么样了", [], "")

    assert "指代性追问" in prompt, "recall 定义丢了「无回忆词的指代性追问」这一条"
    assert "那个作业后来怎么样了" in prompt, "少样本例子没了，模型只能靠抽象描述猜"


def test_supervisor_labels_independent_of_keywords(monkeypatch):
    """每个标签都要经过分类器，没有任何关键词表能替它下判断。

    断言的一半是分类器**收到**了哪条消息：给「你好呀」加一条关键词短路或快速
    通道，`seen` 会红。另一半是分类器的答案没有被**事后改写**——stub 对含
    「记得」的消息故意返回 "fact"，也就是关键词表会给出的 "recall" 的反面，
    所以「记得 → recall」这类规则一旦绕过分类器生效，断言就会红。
    返回值的常规覆盖在 test_agents.py:test_both_labels_returned。
    """
    from langchain_core.messages import HumanMessage
    from ai.agents import supervisor as module

    seen = []
    monkeypatch.setattr(module, "_classify_with_llm", lambda user_msg, *a, **kw: (
        seen.append(user_msg) or
        {
            "has_emotion": False,
            "memory_kind": "fact" if "记得" in user_msg else "none",
            "classification_source": "llm",
        }
    ))

    plain = module.supervisor_node({"messages": [HumanMessage(content="你好呀")]})
    retrospective = module.supervisor_node({
        "messages": [HumanMessage(content="还记得第一次见面吗")]
    })

    assert seen == ["你好呀", "还记得第一次见面吗"]
    assert plain["memory_kind"] == "none"
    assert retrospective["memory_kind"] == "fact", "分类器的答案就是答案，关键词表不能推翻它"


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
    from storage.models.character import Character
    from storage.models.friend import Friend, Message
    from storage.models.user import UserProfile

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
    from storage.models.character import Character
    from storage.models.friend import Friend, Message
    from storage.models.user import UserProfile

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
            friend=friend,
            user_message=f"用户{index}" + "问" * 500,
            input="",
            output=f"回复{index}" + "答" * 500,
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


@pytest.mark.django_db
def test_summary_fold_stops_at_budget_and_defers_the_rest(monkeypatch):
    """折叠有总预算，烧完就停手，剩下的留给下一轮。

    摘要折叠发生在**进图之前**，同步阻塞，用户已经在等了。单批有超时，但批数不限，
    所以它是整条请求路径上最大的无界项——没有这个预算，供应商慢的时候用户能等上
    好几分钟，而轮级 deadline 管的是图，管不到这里。

    停手不是失败：检查点是每批之后落的，已经折过的不会重复折，这一轮没折到的
    下一轮接着折。所以断言要同时钉住两件事——真的停了，以及停的位置之前的
    进度确实落库了。
    """
    from django.contrib.auth.models import User
    from ai.memory import conversation_summary as summary_module
    from storage.models.character import Character
    from storage.models.friend import Friend, Message
    from storage.models.user import UserProfile

    profile = UserProfile.objects.create(
        user=User.objects.create_user(username="fold-budget")
    )
    character = Character.objects.create(
        author=profile, name="女友", profile="温柔",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
    )
    friend = Friend.objects.create(me=profile, character=character)
    rows = [
        Message.objects.create(
            friend=friend,
            user_message=f"用户{index}" + "问" * 500,
            input="",
            output=f"回复{index}" + "答" * 500,
        )
        for index in range(40)
    ]
    calls = []

    def summarize(previous, batch, *args, **kwargs):
        # 每一批都慢到把预算烧穿：第一批折完，第二次检查就该停手。
        time.sleep(0.05)
        calls.append([message.id for message in batch])
        return "较早对话摘要。"

    monkeypatch.setattr(summary_module, "_summarize_batch", summarize)
    monkeypatch.setattr(summary_module, "SUMMARY_FOLD_BUDGET_SECONDS", 0.02)

    summary, recent = summary_module.prepare_conversation_context(friend)
    friend.refresh_from_db()

    assert len(calls) == 1, (
        f"预算耗尽后还在继续折：折了 {len(calls)} 批。"
        "SUMMARY_FOLD_BUDGET_SECONDS 没有在批与批之间被检查。"
    )
    assert summary == "较早对话摘要。"
    assert friend.summary_through_message_id == rows[len(calls[0]) - 1].id, (
        "停手前已折完的那批必须落库——检查点没推进的话，下一轮会从同一位置重折"
    )
    assert recent, "无论折了几批，返回的原文投影都不能是空的"


@pytest.mark.django_db
def test_concurrent_fold_requester_does_not_fold_again(monkeypatch):
    """Stale checkpoint readers must not re-fold the same range.

    Two chat requests on the same Friend can both read the checkpoint before
    either saves.  The loser must re-read the checkpoint under the per-friend
    lock and find nothing left to fold — otherwise the same range is folded
    twice with different bases and the checkpoint rolls backward.
    """
    from django.contrib.auth.models import User
    from ai.memory.conversation_summary import prepare_conversation_context
    from storage.models.character import Character
    from storage.models.friend import Friend, Message
    from storage.models.user import UserProfile

    profile = UserProfile.objects.create(
        user=User.objects.create_user(username="context-race")
    )
    character = Character.objects.create(
        author=profile, name="女友", profile="温柔",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
    )
    friend = Friend.objects.create(me=profile, character=character)
    rows = [
        Message.objects.create(
            friend=friend,
            user_message=f"用户{index}" + "问" * 400,
            input="",
            output=f"回复{index}" + "答" * 400,
        )
        for index in range(14)
    ]
    calls = []

    def summarize(previous, batch, *args, **kwargs):
        calls.append([message.id for message in batch])
        return "较早对话摘要：并发折叠被正确去重。"

    monkeypatch.setattr("ai.memory.conversation_summary._summarize_batch", summarize)

    # A concurrent request would have read the Friend row before the fold
    # saved its checkpoint — model it with a stale instance.
    stale = Friend.objects.get(id=friend.id)

    summary, recent = prepare_conversation_context(friend)
    summary2, recent2 = prepare_conversation_context(stale)
    stale.refresh_from_db()

    assert calls == [[message.id for message in rows[:4]]]
    assert summary == summary2
    assert summary2.startswith("较早对话摘要")
    assert [message.id for message in recent] == [message.id for message in rows[-10:]]
    assert len(recent2) == len(recent)
    assert stale.summary_through_message_id == rows[3].id


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
    from storage.models.character import Character
    from storage.models.friend import Friend
    from storage.models.reflection_job import ReflectionJob
    from storage.models.user import UserProfile

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
        lambda current_friend, target_chat_day, expected_history_generation: calls.append(
            (current_friend.id, target_chat_day, expected_history_generation)
        ),
    )

    first = module.process_pending_reflection_jobs(friend_id=friend.id, limit=3)
    second = module.process_pending_reflection_jobs(friend_id=friend.id, limit=3)
    job.refresh_from_db()

    assert first == {"done": 1, "failed": 0}
    assert second == {"done": 0, "failed": 0}
    assert calls == [(friend.id, datetime.date(2026, 7, 8), 0)]
    assert job.status == "done"
    assert job.attempts == 1


def test_busy_day_uses_conditional_llm_reduce(monkeypatch):
    from ai.ingestion import relationship_overview as module

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


@pytest.mark.django_db
def test_memory_kind_drives_retrieval_strategy(monkeypatch):
    """memory_kind 的三个取值分别决定检索策略。

    - none：不调 QueryRewriter，也不翻原文——这一支只能证伪「过度触发」，
      钉不住 memory_agent 里的任何一行
    - recall：钉 `memory_kind == "recall"`——强制调 QueryRewriter 与翻原文，
      且用的是规划后的 query
    - fact：钉另外两处——`or memory_kind == "fact"`（调 QueryRewriter）与
      `memory_kind == "fact" and not semantic_reliable`（不可靠才回退翻原文）

    fact 的两条断言分别跑语义记忆不可靠（空结果）与可靠（一条 current 事实）
    两种情况，这正是 `and not semantic_reliable` 这个守卫存在的理由：去掉守卫，
    「语义记忆可靠时 fact 不该回退翻原文」立刻变红；否则 fact 与 recall 的观察
    结果完全相同，一个布尔 has_memory 也能全绿。

    只改 memory_kind 一个变量，消息文本和其余 state 都相同——这样才能证明
    检索策略真的是由 memory_kind 决定的。
    """
    from django.contrib.auth.models import User
    from langchain_core.messages import HumanMessage
    from ai.agents import memory_agent as module
    from storage.models.character import Character
    from storage.models.friend import Friend, Message
    from storage.models.user import UserProfile

    profile = UserProfile.objects.create(user=User.objects.create_user(username="memory-kind"))
    character = Character.objects.create(author=profile, name="女友", profile="温柔")
    friend = Friend.objects.create(me=profile, character=character)
    # 第 2 轮混合检索有前置条件（`if should_search_raw and (has_imported or
    # has_online)`）：既没有导入聊天也没有在线消息时，should_search_raw 为真
    # 也不会查库。放一条在线消息，让 recall 与 none 的差异真正落在检索上。
    Message.objects.create(friend=friend, user_message="普通在线消息", input="", output="好")

    planned = []
    searched = []
    monkeypatch.setattr(module, "search_semantic", lambda *a, **kw: [])
    monkeypatch.setattr(module.Reranker, "rerank", lambda self, query, docs, top_k: docs)
    monkeypatch.setattr(
        module.ConversationHistorySearch, "search",
        lambda self, queries, **kw: searched.append(queries) or [],
    )
    # 计划里的 query 必须与默认路径（queries = [user_msg]）不同，否则「规划器
    # 到底跑没跑」在查库结果里看不出来——stub 返回同样的字符串就等于没测。
    monkeypatch.setattr(
        module.QueryRewriter, "plan",
        lambda self, *a, **kw: planned.append(True) or {
            "queries": ["今天天气不错", "换个说法再问一次"],
            "temporal_anchor": "unknown",
        },
    )

    base = {
        "messages": [HumanMessage(content="今天天气不错")],
        "friend_id": friend.id,
        "character_id": character.id,
        "semantic_facts": [],
    }
    planned_queries = [["今天天气不错", "换个说法再问一次"]]

    module.memory_agent_node({**base, "memory_kind": "none"}, api_key="t", api_base="u")
    assert planned == [], "闲聊不该调 QueryRewriter"
    assert searched == [], "闲聊不该翻原文"

    # key 整个缺席时（state.get 的默认值）必须与 "none" 同解：supervisor_graph
    # 的路由函数用的也是 "none"，两边对「state 里没写」得给出同一个答案。
    planned.clear()
    searched.clear()
    module.memory_agent_node(base, api_key="t", api_base="u")
    assert planned == [], "缺席当 none：不该调 QueryRewriter"
    assert searched == [], "缺席当 none：不该翻原文"

    module.memory_agent_node({**base, "memory_kind": "recall"}, api_key="t", api_base="u")
    assert len(planned) == 1, "recall 必须调 QueryRewriter"
    assert searched == planned_queries, "recall 必须翻原文，且用的是规划后的 query"

    planned.clear()
    searched.clear()
    module.memory_agent_node({**base, "memory_kind": "fact"}, api_key="t", api_base="u")
    assert len(planned) == 1, "fact 必须调 QueryRewriter"
    assert searched == planned_queries, "语义记忆不可靠时 fact 必须回退到翻原文"

    # 换一条可靠的语义记忆，两个标签在这里必须分道扬镳：上面 search_semantic
    # 一律返回空，只有这一组断言能钉住 `and not semantic_reliable`。
    monkeypatch.setattr(module, "search_semantic", lambda *a, **kw: [
        {"id": 1, "fact": "用户住在杭州", "memory_state": "current", "subject": "user"},
    ])
    planned.clear()
    searched.clear()
    module.memory_agent_node({**base, "memory_kind": "fact"}, api_key="t", api_base="u")
    assert len(planned) == 1, "事实类仍要调 QueryRewriter"
    assert searched == [], "语义记忆可靠时 fact 不该回退翻原文"

    planned.clear()
    searched.clear()
    module.memory_agent_node({**base, "memory_kind": "recall"}, api_key="t", api_base="u")
    assert searched == planned_queries, "recall 无视语义可靠性，强制翻原文"


def _seed_stage_fixture(username: str):
    """建一套「三轮检索都有前置条件」的最小数据。

    分阶段兜底的测试必须让**两个**阶段同时有产物可出，否则「只丢这一块」证不出来
    ——另一块本来就是空的，断言恒真。
    """
    from django.contrib.auth.models import User
    from storage.models.character import Character
    from storage.models.friend import Friend, Message
    from storage.models.user import UserProfile

    profile = UserProfile.objects.create(user=User.objects.create_user(username=username))
    character = Character.objects.create(author=profile, name="女友", profile="温柔")
    friend = Friend.objects.create(me=profile, character=character)
    # 第 2 轮的前置条件 `has_online`：没有在线消息时 should_search_raw 为真也不查库。
    Message.objects.create(friend=friend, user_message="普通在线消息", input="", output="好")
    return character, friend


HISTORY_TEXT = "我们上次说好一起去看海"


def _stub_retrieval(monkeypatch, module, *, semantic=None, rerank=None):
    """把三轮检索的外部依赖换成可控 stub；只留被测的那一个可以炸。"""
    monkeypatch.setattr(module, "search_semantic", semantic or (lambda *a, **kw: []))
    monkeypatch.setattr(module.Reranker, "rerank", rerank or (lambda self, q, docs, top_k: docs))
    monkeypatch.setattr(
        module.ConversationHistorySearch, "search",
        lambda self, queries, **kw: [{
            "source_type": "online_chat",
            "content": HISTORY_TEXT,
            "score": 0.9,
            "message_refs": [1],
            "timestamp": "2026-07-01",
        }],
    )
    monkeypatch.setattr(
        module.QueryRewriter, "plan",
        lambda self, *a, **kw: {"queries": ["今天天气不错"], "temporal_anchor": "unknown"},
    )


def _recall_state(character, friend):
    from langchain_core.messages import HumanMessage

    return {
        "messages": [HumanMessage(content="今天天气不错")],
        "friend_id": friend.id,
        "character_id": character.id,
        "semantic_facts": [],
        "memory_kind": "recall",
    }


@pytest.mark.django_db
def test_semantic_stage_failure_keeps_history_stage(monkeypatch):
    """语义检索炸了，原文检索的结果必须还在。

    只在最外层包一个 try 的话这里会退化成「这轮没有记忆」——把整段 context 一起
    丢掉。这条断言就是分阶段兜底存在的全部理由。
    """
    from ai.agents import memory_agent as module

    character, friend = _seed_stage_fixture("stage-semantic")

    def boom(*a, **kw):
        raise RuntimeError("向量库挂了")

    _stub_retrieval(monkeypatch, module, semantic=boom)
    result = module.memory_agent_node(_recall_state(character, friend), api_key="t", api_base="u")

    assert result["semantic_facts"] == [], "语义检索炸了，这一块就该是空的"
    assert HISTORY_TEXT in result["memory_context"], "语义那块炸了不该把原文那块一起赔进去"


@pytest.mark.django_db
def test_history_stage_failure_keeps_semantic_stage(monkeypatch):
    """原文检索炸了，语义事实必须还在，而且不能留下半截证据。

    `retrieved_raw` 是从 `history_hits` 派生的。失败时只清 context 不清 hits 的话，
    provenance 会列出一批「有证据」但从来没进过 prompt 的原文——前端证据面板会
    指着一段没被用上的聊天说这是依据。
    """
    from ai.agents import memory_agent as module

    character, friend = _seed_stage_fixture("stage-history")

    def boom(self, query, docs, top_k):
        raise RuntimeError("rerank 挂了")

    _stub_retrieval(
        monkeypatch, module,
        semantic=lambda *a, **kw: [
            {"id": 1, "fact": "用户住在杭州", "memory_state": "current", "subject": "user"},
        ],
        rerank=boom,
    )
    result = module.memory_agent_node(_recall_state(character, friend), api_key="t", api_base="u")

    assert "用户住在杭州" in result["memory_context"], "原文那块炸了不该把语义事实一起赔进去"
    assert HISTORY_TEXT not in result["memory_context"]
    assert result["reply_provenance"]["retrieved_raw"] == [], "炸掉那一轮的证据不能留在 provenance 里"
    assert "用户住在杭州" in [f["fact"] for f in result["reply_provenance"]["semantic_facts"]]


@pytest.mark.django_db
def test_client_construction_failure_only_costs_the_history_stage(monkeypatch):
    """检索 client **构造**失败也只得丢原文那一块。

    跑真实故障时发现的：`ConversationHistorySearch()` 的构造里会 new 一个
    CustomEmbeddings，缺 DASHSCOPE_API_KEY 时构造函数直接抛 OpenAIError。原先两个
    client 建在所有阶段守卫**之前**，于是一次 embedding 鉴权失效带走全部五个阶段
    ——时间块、话题、关系概览都是纯 SQL，本来活得下来。外层 except 还在，所以整轮
    没死，用户看不出问题；作废的是「一块失败只丢那一块」，而且失败现场从
    history_search 那条 trace 挪到了外层那条，事后更难查。
    """
    from ai.agents import memory_agent as module

    character, friend = _seed_stage_fixture("stage-ctor")

    def boom(*a, **kw):
        raise RuntimeError("Missing credentials. Please pass an `api_key`")

    _stub_retrieval(
        monkeypatch, module,
        semantic=lambda *a, **kw: [
            {"id": 1, "fact": "用户住在杭州", "memory_state": "current", "subject": "user"},
        ],
    )
    monkeypatch.setattr(module.ConversationHistorySearch, "__init__", boom)
    monkeypatch.setattr(module.Reranker, "__init__", boom)

    result = module.memory_agent_node(_recall_state(character, friend), api_key="t", api_base="u")

    assert "用户住在杭州" in result["memory_context"], "构造失败不该把语义那块一起赔进去"
    assert HISTORY_TEXT not in result["memory_context"]


@pytest.mark.django_db
def test_history_stage_failure_after_rerank_clears_half_built_evidence(monkeypatch):
    """失败点落在 rerank **之后**时，已经拿到的 hits 也要清掉。

    上面那条测试证不出这一点：rerank 是这段里最靠前的可失败点，在它那里炸掉时
    history_hits 还是初始的 []，「清空」等于没做——把 `history_hits = []` 那行删掉
    测试照样绿。

    这里让 rerank 成功、下游失败：上游给了一个 message_refs 不是可迭代对象的 hit，
    `_select_source_diverse_hits` 里的 `tuple(hit.get("message_refs") or [])` 直接抛
    TypeError。这是真实可能发生的上游数据形状错误，不是硬造的场景。
    """
    from ai.agents import memory_agent as module

    character, friend = _seed_stage_fixture("stage-half-built")
    _stub_retrieval(monkeypatch, module)
    monkeypatch.setattr(
        module.ConversationHistorySearch, "search",
        lambda self, queries, **kw: [{
            "source_type": "online_chat",
            "content": HISTORY_TEXT,
            "score": 0.9,
            "message_refs": 1,  # 不是 list —— tuple(1) 会抛 TypeError
            "timestamp": "2026-07-01",
        }],
    )
    result = module.memory_agent_node(_recall_state(character, friend), api_key="t", api_base="u")

    assert HISTORY_TEXT not in result["memory_context"]
    assert result["reply_provenance"]["retrieved_raw"] == [], (
        "rerank 已经把 hits 交出来了，但这一段整体作废——provenance 不能留着没进过 prompt 的证据"
    )


@pytest.mark.django_db
def test_stage_failure_records_why_in_trace(monkeypatch):
    """降级本身只是「没查成」，得知道原因才知道该去修什么。

    原因带异常原文而不是 "semantic_search_failed" 这种原因码：超时、向量库挂了、
    鉴权过期三种修法完全不同（同 supervisor.py 对 _error 的取舍）。
    """
    from ai.agents import memory_agent as module

    character, friend = _seed_stage_fixture("stage-trace")
    calls = []
    monkeypatch.setattr(
        module, "record_trace",
        lambda name, inputs, outputs=None, **kw: calls.append((name, outputs)) or outputs,
    )

    def boom(*a, **kw):
        raise RuntimeError("向量库挂了")

    _stub_retrieval(monkeypatch, module, semantic=boom)
    module.memory_agent_node(_recall_state(character, friend), api_key="t", api_base="u")

    stage_spans = [outputs for name, outputs in calls if name == "memory_agent.semantic_search"]
    assert stage_spans, "降级必须留下一个 span，否则复盘时只看到「这轮没记忆」"
    assert "RuntimeError" in stage_spans[0]["error"]
    assert "向量库挂了" in stage_spans[0]["error"]


@pytest.mark.django_db
def test_outer_fallback_does_not_return_provenance(monkeypatch):
    """外层兜底不能返回 reply_provenance。

    api/chat.py 在图跑之前就播好了 provenance 的种子（recent_online /
    summary_through_message_id / has_working_summary）。正常路径是在 `**` 合并的
    基础上写，兜底路径整个不返回，种子就原样留着；返回一个全新的 dict 会把种子
    覆盖成只剩内层那几个字段。
    """
    from langchain_core.messages import HumanMessage
    from ai.agents import memory_agent as module

    def boom(*a, **kw):
        raise RuntimeError("检索炸了")

    monkeypatch.setattr(module, "detect_memory_intent", boom)
    result = module.memory_agent_node(
        {"messages": [HumanMessage(content="你好")], "semantic_facts": ["已有事实"]},
        api_key="t",
        api_base="u",
    )

    assert "reply_provenance" not in result, "兜底返回 provenance 会覆盖掉 api/chat.py 播的种"
    assert set(result) == {"memory_context", "memory_sections", "semantic_facts"}
    assert result["semantic_facts"] == ["已有事实"], "炸了也不能把上游已有的语义事实抹掉"


@pytest.mark.django_db
def test_memory_agent_closes_its_connection(monkeypatch):
    """memory_agent 每轮都要自己关数据库连接——正常返回和降级返回都一样。

    那层 finally 是**防御性**的：实测节点跑在 asyncio.run 的默认 executor 线程上，
    该线程随请求一起销毁，连接本来也不会长期驻留（见 _run_memory_agent 的 docstring）。
    真正无人关闭的是跨请求复用的 anyio 工作线程。留着它是因为它便宜，不是因为它在补
    一个正在漏的连接。

    两条断言分开测：正常返回要关，降级返回也要关。后者才是把 try/finally 和
    「在函数末尾补一句」区分开的那个——只留前一条的话，把 close 挪到
    `return result` 前面照样绿。

    必须带 django_db：不带的话 Friend.objects 会先抛 pytest-django 的
    RuntimeError("Database access not allowed")，boom 根本执行不到，而
    `pytest.raises(RuntimeError)` 照样绿——这条测试曾经就是这么假绿的。
    """
    from langchain_core.messages import HumanMessage
    from ai.agents import memory_agent as module

    closed = []
    monkeypatch.setattr(module, "close_old_connections", lambda: closed.append(True))

    module.memory_agent_node({"messages": [HumanMessage(content="")]}, api_key="t", api_base="u")
    assert closed == [True], "正常返回也要关"

    closed.clear()

    def boom(*a, **kw):
        raise RuntimeError("检索炸了")

    monkeypatch.setattr(module, "detect_memory_intent", boom)
    result = module.memory_agent_node(
        {"messages": [HumanMessage(content="你好")], "semantic_facts": []},
        api_key="t",
        api_base="u",
    )
    assert result["memory_context"] == "", "检索炸了要降级成「这轮没有记忆」，不是赔掉整轮"
    assert closed == [True], "降级返回同样要关，否则连接就留在线程上了"


def test_tts_sender_persists_supervisor_decision():
    """provenance 里的 supervisor_decision 由三个 state 字段拼装。

    `supervisor_decision` 只是 provenance 的字段名，state 里没有这个键——
    写成 result.get("supervisor_decision", {}) 会永远存下一个空字典
    （spec 初版就是这么写的）。
    """
    import asyncio
    from langchain_core.messages import AIMessage
    from api.chat import tts_sender

    queue: list[dict] = []

    class FakeApp:
        async def ainvoke(self, inputs, config=None):
            return {
                "messages": [AIMessage(content="嗨")],
                "reply_provenance": {"has_working_summary": False, "retrieved_raw": []},
                "has_emotion": True,
                "memory_kind": "recall",
                "classification_source": "llm",
            }

    class FakeMQ:
        def put_nowait(self, item):
            queue.append(item)

    class FakeWS:
        async def send(self, payload):
            pass

    # 不需要任何 SSE / TTS 机器：tts_sender 只用到 app.ainvoke、mq.put_nowait、
    # ws.send 三件事，三个 duck type 的假件就够了。
    asyncio.run(tts_sender(FakeApp(), {"reply_provenance": {}}, FakeMQ(), FakeWS(), "task-1"))

    provenance = next(item["reply_provenance"] for item in queue if "reply_provenance" in item)
    assert provenance["supervisor_decision"] == {
        "has_emotion": True,
        "memory_kind": "recall",
        "classification_source": "llm",
    }
    assert "supervisor_intent" not in provenance
    # 图里带出来的其它 provenance 字段照旧保留。
    assert provenance["has_working_summary"] is False


def test_emoji_meaning_does_not_modify_user_message():
    from api.chat import _build_conversation_messages

    messages = _build_conversation_messages(
        "你还记得那次吗🙂‍↕️",
        [{"emoji": "🙂‍↕️", "meaning": "不满、别扭、有点抗拒"}],
        [],
    )

    assert messages[-1].content == "你还记得那次吗🙂‍↕️"
    assert "用户表情含义" not in messages[-1].content


@pytest.mark.django_db
def test_private_imported_context_is_visible_only_to_character_owner():
    from django.contrib.auth.models import User
    from ai.memory.import_access import can_access_imported_context
    from storage.models.character import Character
    from storage.models.friend import Friend
    from storage.models.user import UserProfile

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
    from storage.models.character import Character
    from storage.models.chat_message import ChatMessage
    from storage.models.friend import Friend, Message
    from storage.models.user import UserProfile

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
        "memory_kind": "recall",
        "friend_id": friend.id,
        "character_id": character.id,
        "semantic_facts": [],
    }, api_key="test", api_base="https://example.invalid")

    assert captured["character_id"] is None


@pytest.mark.django_db
def test_private_friend_semantic_search_excludes_imported_facts():
    from django.contrib.auth.models import User
    from ai.memory.semantic import search_semantic
    from storage.models.character import Character
    from storage.models.friend import Friend
    from storage.models.memory import SemanticMemory
    from storage.models.user import UserProfile

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
    from storage.models.character import Character
    from storage.models.friend import Friend
    from storage.models.memory import MemoryEvidence, SemanticMemory
    from storage.models.user import UserProfile

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
    from storage.models.user import UserProfile
    from storage.models.character import Character
    from storage.models.friend import Friend, Message
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
    from ai.ingestion import pipeline

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
