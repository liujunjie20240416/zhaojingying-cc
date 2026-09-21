import pytest
from langchain_core.messages import HumanMessage


class TestSupervisorFastPath:
    def test_ack_skips_llm(self, monkeypatch):
        """无信息量的确认语不走 LLM。"""
        from ai.agents import supervisor

        def boom(*args, **kwargs):
            raise AssertionError("fast path must not call the classifier")

        monkeypatch.setattr(supervisor, "_classify_with_llm", boom)
        result = supervisor.supervisor_node({"messages": [HumanMessage(content="嗯嗯")]})

        assert result["has_emotion"] is False
        assert result["memory_kind"] == "none"
        assert result["classification_source"] == "fast_path"

    def test_punctuation_only_message_skips_llm(self, monkeypatch):
        """纯符号短消息也走快速通道。"""
        from ai.agents import supervisor

        monkeypatch.setattr(
            supervisor, "_classify_with_llm",
            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not classify")),
        )
        result = supervisor.supervisor_node({"messages": [HumanMessage(content="。。。")]})

        assert result["classification_source"] == "fast_path"

    def test_emoji_context_forces_classifier(self, monkeypatch):
        """带 emoji 语义的消息必须过分类器——快速通道看不见 emoji。"""
        from ai.agents import supervisor

        called = {}
        monkeypatch.setattr(supervisor, "_classify_with_llm", lambda *a, **kw: (
            called.update({"yes": True}) or
            {"has_emotion": True, "memory_kind": "none",
             "classification_source": "llm"}
        ))
        result = supervisor.supervisor_node({
            "messages": [HumanMessage(content="好")],
            "emotion_context": [{"emoji": "🙂‍↕️", "meaning": "不满、别扭"}],
        })

        assert called.get("yes"), "emoji context must reach the classifier"
        assert result["has_emotion"] is True

    def test_emoji_context_reaches_classifier_as_list(self, monkeypatch):
        from ai.agents import supervisor

        captured = {}
        monkeypatch.setattr(supervisor, "_classify_with_llm", lambda user_msg, emotion_context, *a, **kw: (
            captured.update({"msg": user_msg, "ctx": emotion_context}) or
            {"has_emotion": True, "memory_kind": "none", "classification_source": "llm"}
        ))
        supervisor.supervisor_node({
            "messages": [HumanMessage(content="随便吧")],
            "emotion_context": ["🙂‍↕️：不满、别扭"],
        })

        assert captured["msg"] == "随便吧"
        assert captured["ctx"] == ["🙂‍↕️：不满、别扭"]

    def test_emoji_outside_frontend_table_still_reaches_classifier(self, monkeypatch):
        """表外 emoji 不能被当成无信息量消息吞掉。

        emotion_context 由前端那张固定的 18 项表生成，💔/😔/😂 都不在表里，
        所以它们到不了「emotion_context 非空」那条豁免。若判定用字符白名单，
        裸的 💔 会因为不含汉字/字母/数字而走快速通道 → has_emotion=False →
        用户发一个心碎的表情，什么情绪回应都得不到。
        """
        from ai.agents import supervisor

        captured = []
        monkeypatch.setattr(supervisor, "_classify_with_llm", lambda *a, **kw: (
            captured.append(a[0]) or
            {"has_emotion": True, "memory_kind": "none", "classification_source": "llm"}
        ))

        for emoji in ("💔", "😔", "😂"):
            supervisor.supervisor_node({"messages": [HumanMessage(content=emoji)]})

        assert captured == ["💔", "😔", "😂"], "表外 emoji 必须走分类器"

    def test_fullwidth_alphanumerics_reach_classifier(self, monkeypatch):
        """全角字母数字同样不含 [A-Za-z0-9]，不能走快速通道。"""
        from ai.agents import supervisor

        captured = []
        monkeypatch.setattr(supervisor, "_classify_with_llm", lambda *a, **kw: (
            captured.append(a[0]) or
            {"has_emotion": False, "memory_kind": "recall", "classification_source": "llm"}
        ))

        for text in ("ｈｅｌｌｏ", "１２３"):
            supervisor.supervisor_node({"messages": [HumanMessage(content=text)]})

        assert captured == ["ｈｅｌｌｏ", "１２３"], "全角字母数字必须走分类器"

    def test_whitespace_only_message_still_skips_llm(self, monkeypatch):
        """空白消息要走快速通道：strip 后为空，all() 对空序列返回 True。"""
        from ai.agents import supervisor

        monkeypatch.setattr(
            supervisor, "_classify_with_llm",
            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not classify")),
        )
        result = supervisor.supervisor_node({"messages": [HumanMessage(content="   ")]})

        assert result["classification_source"] == "fast_path"


class TestSupervisorClassifier:
    def test_both_labels_returned(self, monkeypatch):
        """一次调用同时回答两个问题——这是本次改造的核心。"""
        from ai.agents import supervisor

        monkeypatch.setattr(supervisor, "_classify_with_llm", lambda *a, **kw: {
            "has_emotion": True, "memory_kind": "recall",
            "classification_source": "llm", "confidence": 0.9,
        })
        result = supervisor.supervisor_node({"messages": [HumanMessage(content="上次吵架我好难过")]})

        assert result["has_emotion"] is True
        assert result["memory_kind"] == "recall"
        assert result["classification_source"] == "llm"

    def test_recent_dialogue_is_passed_to_classifier(self, monkeypatch):
        """最近对话要传进分类器，用于理解省略的指代。"""
        from ai.agents import supervisor

        captured = {}

        def fake_classify(user_msg, emotion_context, api_key, api_base, recent_dialogue=""):
            captured["dialogue"] = recent_dialogue
            captured["msg"] = user_msg
            return {"has_emotion": False, "memory_kind": "recall", "classification_source": "llm"}

        monkeypatch.setattr(supervisor, "_classify_with_llm", fake_classify)
        Msg = type("msg", (), {})
        earlier_user = Msg()
        earlier_user.content = "咱们哪一年认识的"
        earlier_user.type = "human"
        earlier_ai = Msg()
        earlier_ai.content = "我记不清了，我们去翻聊天记录吧"
        earlier_ai.type = "ai"
        follow_up = Msg()
        follow_up.content = "你去翻，翻完跟我说"
        follow_up.type = "human"

        result = supervisor.supervisor_node({"messages": [earlier_user, earlier_ai, follow_up]})

        assert result["memory_kind"] == "recall"
        assert "咱们哪一年认识的" in captured["dialogue"]
        assert "翻聊天记录" in captured["dialogue"]
        assert captured["msg"] == "你去翻，翻完跟我说"

    def test_empty_message_yields_no_op_decision(self):
        from ai.agents import supervisor

        result = supervisor.supervisor_node({"messages": []})

        assert result["has_emotion"] is False
        assert result["memory_kind"] == "none"
        assert result["classification_source"] == "empty"

    def test_tool_message_is_not_user_input(self, monkeypatch):
        """ToolMessage 没有 tool_calls 属性，旧实现会把它当成用户消息。

        这才是 I3 的真正失败模式：工具调用落地后，检索结果会作为 ToolMessage
        进入 state，排在真正的用户消息后面。

        这里刻意不用「带 tool_calls 的 AIMessage」当用例——AIMessage 本来就带
        tool_calls 属性，旧的 `hasattr(msg, "tool_calls")` 判定会**碰巧**跳过它，
        于是那个用例对着旧代码也是绿的，证明不了任何事。写错过一次。
        """
        from ai.agents import supervisor
        from langchain_core.messages import AIMessage, ToolMessage

        captured = {}

        def fake_classify(user_msg, *a, **kw):
            captured["msg"] = user_msg
            return {"has_emotion": False, "memory_kind": "recall", "classification_source": "llm"}

        monkeypatch.setattr(supervisor, "_classify_with_llm", fake_classify)
        ai_with_tools = AIMessage(content="我查一下", tool_calls=[
            {"name": "search", "args": {}, "id": "call_1"},
        ])
        tool_result = ToolMessage(content="搜索结果：2023 年去过京都", tool_call_id="call_1")
        supervisor.supervisor_node({"messages": [
            HumanMessage(content="你还记得那次旅行吗"), ai_with_tools, tool_result,
        ]})

        assert captured["msg"] == "你还记得那次旅行吗"

    def test_supervisor_trace_carries_trace_metadata(self, monkeypatch):
        """trace_metadata 要透传，否则 LangSmith 里筛不出这个 friend 的 supervisor 决策。"""
        from ai.agents import supervisor

        seen = []
        monkeypatch.setattr(supervisor, "record_trace", lambda name, inputs, outputs=None, **kw: (
            seen.append(kw.get("metadata")) or outputs
        ))
        monkeypatch.setattr(supervisor, "_classify_with_llm", lambda *a, **kw: {
            "has_emotion": False, "memory_kind": "none", "classification_source": "llm",
        })
        meta = {"friend_id": 7, "entrypoint": "test"}

        supervisor.supervisor_node({"messages": [HumanMessage(content="   ")], "trace_metadata": meta})
        supervisor.supervisor_node({"messages": [HumanMessage(content="今天干嘛")], "trace_metadata": meta})
        supervisor.supervisor_node({"messages": [], "trace_metadata": meta})

        assert seen == [meta, meta, meta]

    def test_llm_trace_records_what_was_actually_sent(self, monkeypatch):
        """LLM 路径的 trace 要能逐条复盘。

        这次改造刻意没做评测集（spec §7），准确率没有仪表盘可看，trace 就是唯一
        的手段。模型名、prompt 原文、最近对话都要在里面——只记下判断结果没用，
        判错了要能看出为什么。
        """
        from ai.agents import supervisor

        calls = []
        monkeypatch.setattr(supervisor, "record_trace", lambda name, inputs, outputs=None, **kw: (
            calls.append((name, inputs, kw)) or outputs
        ))
        monkeypatch.setattr(supervisor, "_classify_with_llm", lambda *a, **kw: {
            "has_emotion": True, "memory_kind": "recall", "classification_source": "llm",
        })

        supervisor.supervisor_node({
            "messages": [HumanMessage(content="上次吵架我好难过")],
            "emotion_context": ["😭：难过、哭、情绪很重"],
            "trace_metadata": {"friend_id": 7},
        })

        assert len(calls) == 1
        name, inputs, kw = calls[0]
        assert name == "supervisor.route"
        assert kw["run_type"] == "llm"
        assert kw["metadata"] == {"friend_id": 7}
        assert inputs["model"]
        assert inputs["emotion_context"] == ["😭：难过、哭、情绪很重"]
        # prompt 原文要原样记下来，字段齐了才复盘得出来
        prompt = inputs["messages"][0]["content"]
        assert "上次吵架我好难过" in prompt
        assert "memory_kind" in prompt
        assert "😭：难过、哭、情绪很重" in prompt


class TestSupervisorFallback:
    def test_llm_failure_falls_back_to_recall(self, monkeypatch):
        """分类器失败时宁可多检索一次——陪伴产品的承诺是「她记得」。"""
        from ai.agents import supervisor

        def boom(**kwargs):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(supervisor, "OpenAI", boom)
        result = supervisor._classify_with_llm("今天天气不错", [], "", "", "")

        assert result["has_emotion"] is False
        assert result["memory_kind"] == "recall"
        assert result["classification_source"] == "fallback"

    def test_unknown_memory_kind_is_normalized_to_none(self, monkeypatch):
        """模型返回意料之外的取值时不能崩，收敛到 none。"""
        from ai.agents import supervisor

        class FakeCompletions:
            def create(self, **kwargs):
                return type("R", (), {"choices": [type("C", (), {
                    "message": type("M", (), {"content": '{"has_emotion": false, "memory_kind": "banana"}'})()
                })()]})()

        class FakeClient:
            def __init__(self, **kwargs):
                self.chat = type("Chat", (), {"completions": FakeCompletions()})()

        monkeypatch.setattr(supervisor, "OpenAI", FakeClient)
        result = supervisor._classify_with_llm("随便", [], "", "", "")

        assert result["memory_kind"] == "none"
        assert result["classification_source"] == "llm"

    def test_classifier_timeout_is_five_seconds(self, monkeypatch):
        """分类器从偶发调用变成每条都调，20s 最坏情况不可接受。"""
        from ai.agents import supervisor

        captured = {}

        class FakeCompletions:
            def create(self, **kwargs):
                return type("R", (), {"choices": [type("C", (), {
                    "message": type("M", (), {"content": '{"has_emotion": false, "memory_kind": "none"}'})()
                })()]})()

        class FakeClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.chat = type("Chat", (), {"completions": FakeCompletions()})()

        monkeypatch.setattr(supervisor, "OpenAI", FakeClient)
        supervisor._classify_with_llm("今天几点", [], "", "", "")

        assert captured["timeout"] == 5
        assert supervisor.CLASSIFIER_TIMEOUT == 5

    def test_fallback_trace_records_why_it_fell_back(self, monkeypatch):
        """降级的原因要进 trace。

        「降级率涨了」是这次改造里唯一一个会自己冒出来的信号（spec §7 没做评测集），
        但光看 classification_source == "fallback" 不知道涨在哪：超时？JSON 烂？
        鉴权挂了？三种的修法完全不同。
        """
        from ai.agents import supervisor

        calls = []
        monkeypatch.setattr(supervisor, "record_trace", lambda name, inputs, outputs=None, **kw: (
            calls.append((inputs, kw)) or outputs
        ))

        def boom(**kwargs):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(supervisor, "OpenAI", boom)
        result = supervisor.supervisor_node({"messages": [HumanMessage(content="今天天气不错")]})

        assert result["classification_source"] == "fallback"
        inputs, kw = calls[0]
        assert "connection refused" in inputs["error"]
        assert "RuntimeError" in inputs["error"]
        assert kw["run_type"] == "llm"
        # prompt 原文照记：降级时更要知道当时问的是什么
        assert "今天天气不错" in inputs["messages"][0]["content"]

    def test_error_key_is_confined_to_the_fallback_path(self, monkeypatch):
        """_error 是私有键，只该出现在降级结果里。

        它会被 LangGraph 静默丢弃（不在 MultiAgentState schema 里），所以留在
        降级结果里是安全的；但要是哪天有别的路径也带上它，`result.get("_error")`
        就会往 trace 里写一个假的失败原因，把真正的降级信号淹掉。
        """
        from ai.agents import supervisor

        def boom(**kwargs):
            raise RuntimeError("nope")

        monkeypatch.setattr(supervisor, "OpenAI", boom)
        fallback = supervisor._classify_with_llm("今天天气不错", [], "", "", "")

        assert set(fallback) == {
            "has_emotion", "memory_kind", "confidence", "classification_source", "_error",
        }
        assert set(supervisor._no_op_decision("fast_path")) == {
            "has_emotion", "memory_kind", "classification_source",
        }

    def test_empty_completion_is_a_failure_not_a_confident_no_op(self, monkeypatch):
        """空回复必须降级，不能变成一次看起来健康的「不需要记忆」。"""
        from ai.agents import supervisor

        class FakeCompletions:
            def create(self, **kwargs):
                return type("R", (), {"choices": [type("C", (), {
                    "message": type("M", (), {"content": ""})()
                })()]})()

        class FakeClient:
            def __init__(self, **kwargs):
                self.chat = type("Chat", (), {"completions": FakeCompletions()})()

        monkeypatch.setattr(supervisor, "OpenAI", FakeClient)
        result = supervisor._classify_with_llm("今天天气不错", [], "", "", "")

        assert result["classification_source"] == "fallback"
        assert result["memory_kind"] == "recall"

    def test_garbage_confidence_does_not_discard_the_classification(self, monkeypatch):
        """confidence 脏不能让整条判断陪葬。"""
        from ai.agents import supervisor

        class FakeCompletions:
            def create(self, **kwargs):
                return type("R", (), {"choices": [type("C", (), {
                    "message": type("M", (), {
                        "content": '{"has_emotion": true, "memory_kind": "fact", "confidence": "很高"}'
                    })()
                })()]})()

        class FakeClient:
            def __init__(self, **kwargs):
                self.chat = type("Chat", (), {"completions": FakeCompletions()})()

        monkeypatch.setattr(supervisor, "OpenAI", FakeClient)
        result = supervisor._classify_with_llm("我叫什么", [], "", "", "")

        assert result["classification_source"] == "llm"
        assert result["has_emotion"] is True
        assert result["memory_kind"] == "fact"
        assert result["confidence"] == 0.0

    def test_string_false_has_emotion_is_not_truthy(self, monkeypatch):
        """bool("false") 是 True——模型把布尔写成字符串时必须显式解析。"""
        from ai.agents import supervisor

        class FakeCompletions:
            def create(self, **kwargs):
                return type("R", (), {"choices": [type("C", (), {
                    "message": type("M", (), {
                        "content": '{"has_emotion": "false", "memory_kind": "none"}'
                    })()
                })()]})()

        class FakeClient:
            def __init__(self, **kwargs):
                self.chat = type("Chat", (), {"completions": FakeCompletions()})()

        monkeypatch.setattr(supervisor, "OpenAI", FakeClient)
        result = supervisor._classify_with_llm("今天天气不错", [], "", "", "")

        assert result["has_emotion"] is False


class TestMemoryAgent:
    @pytest.mark.django_db
    def test_memory_agent_returns_context(self, api_key, api_base):
        from ai.agents.memory_agent import memory_agent_node
        Msg = type("msg", (), {"content": "我喜欢吃什么"})
        state = {
            "messages": [Msg()],
            "memory_context": "",
            "semantic_facts": [],
            "character_profile": "温柔女友",
            "friend_id": 0,
            "character_id": None,
        }
        result = memory_agent_node(state, api_key=api_key, api_base=api_base)
        assert "memory_context" in result
        assert isinstance(result["memory_context"], str)


class TestEmotionAgent:
    @pytest.mark.llm_integration
    def test_detect_emotion(self, api_key, api_base):
        from ai.agents.emotion_agent import emotion_agent_node
        Msg = type("msg", (), {"content": "我今天好难过，工作好累"})
        state = {"messages": [Msg()]}
        result = emotion_agent_node(state, api_key=api_key, api_base=api_base)
        assert "emotion_analysis" in result
        assert "emotion" in result["emotion_analysis"]


class TestConversationAgent:
    @pytest.mark.llm_integration
    def test_generates_response(self, api_key, api_base):
        from ai.agents.conversation_agent import conversation_agent_node
        from langchain_core.messages import HumanMessage
        state = {
            "messages": [HumanMessage(content="你好")],
            "memory_context": "",
            "emotion_analysis": None,
            "character_profile": "你是一个温柔体贴的女友。",
        }
        result = conversation_agent_node(state, api_key=api_key, api_base=api_base)
        assert "messages" in result
        assert len(result["messages"]) > 0


class TestSupervisorGraph:
    @pytest.mark.django_db
    @pytest.mark.llm_integration
    def test_create_supervisor_app(self, api_key, api_base):
        from ai.agents.supervisor_graph import create_supervisor_app
        from langchain_core.messages import HumanMessage
        app = create_supervisor_app(
            friend_id=1, character_id=1,
            character_name="测试角色", character_profile="温柔体贴的女友",
        )
        result = app.invoke({
            "messages": [HumanMessage(content="你好")],
            "intent": "", "delegate_to": "", "memory_context": "",
            "emotion_analysis": None, "character_profile": "温柔体贴的女友",
            "character_name": "测试角色", "chat_sender_name": "测试角色",
            "semantic_facts": [], "friend_id": 1, "character_id": 1,
        })
        assert len(result["messages"]) > 1
