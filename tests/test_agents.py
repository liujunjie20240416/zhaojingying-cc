import pytest
from langchain_core.messages import AIMessage, HumanMessage


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

        # 前面两轮是给最近对话用的：意图继承被删掉之后，「还有呢」这类省略指代的
        # 短消息就靠 prompt 里的这段上下文理解（api/chat.py 的墓碑注释这么写的）。
        supervisor.supervisor_node({
            "messages": [
                HumanMessage(content="咱们哪一年认识的"),
                AIMessage(content="我记不清了，我们去翻聊天记录吧"),
                HumanMessage(content="上次吵架我好难过"),
            ],
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
        # 最近对话必须真的拼进 prompt（也就是真的发给模型的那串字），不只是
        # 作为参数传给 _classify_with_llm——trace 里的 recent_dialogue 字段
        # 传得对、prompt 里却没拼进去的话，指代照样理解不了。
        assert "咱们哪一年认识的" in prompt
        assert "翻聊天记录" in prompt


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
        # 降级本身由 json.loads("") 兜住也成立，所以这里钉的是那句 guard 真正
        # 提供的部分：降级原因得写成「模型没回内容」，而不是一句 JSON 解析错误。
        assert "empty content" in result["_error"]

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


class TestSupervisorGraphRouting:
    def test_route_matrix(self):
        from ai.agents.supervisor_graph import route_from_supervisor

        assert route_from_supervisor({"has_emotion": True, "memory_kind": "recall"}) == ["emotion", "memory"]
        assert route_from_supervisor({"has_emotion": True, "memory_kind": "none"}) == ["emotion"]
        assert route_from_supervisor({"has_emotion": False, "memory_kind": "fact"}) == ["memory"]
        assert route_from_supervisor({"has_emotion": False, "memory_kind": "none"}) == ["conversation"]
        # 缺字段时不能崩
        assert route_from_supervisor({}) == ["conversation"]


class TestSupervisorGraph:
    def _app_with_spies(self, monkeypatch, decision):
        from ai.agents import supervisor_graph as module
        from langchain_core.messages import AIMessage

        calls = []
        monkeypatch.setattr(module, "supervisor_node", lambda state: decision)
        monkeypatch.setattr(module, "emotion_agent_node", lambda state: (
            calls.append("emotion") or {"emotion_analysis": {"intensity": 8}}
        ))
        monkeypatch.setattr(module, "memory_agent_node", lambda state: (
            calls.append("memory") or {"memory_context": "上次很难过", "semantic_facts": []}
        ))
        monkeypatch.setattr(module, "conversation_agent_node", lambda state: (
            calls.append("conversation") or {"messages": [AIMessage(content="我记得")]}
        ))
        return module.create_supervisor_app(), calls

    def _invoke(self, app):
        from langchain_core.messages import HumanMessage

        return app.invoke({
            "messages": [HumanMessage(content="你记得上次我很难过吗")],
            "has_emotion": False,
            "memory_kind": "none",
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

    def test_both_labels_run_emotion_and_memory_then_conversation(self, monkeypatch):
        """核心验收：情绪与记忆同时命中时，两个节点都执行且各执行一次。"""
        app, calls = self._app_with_spies(
            monkeypatch,
            {"has_emotion": True, "memory_kind": "recall", "classification_source": "llm"},
        )
        result = self._invoke(app)

        assert sorted(calls) == ["conversation", "emotion", "memory"]
        assert calls[-1] == "conversation"
        assert result["messages"][-1].content == "我记得"
        # schema 里没声明的键会被 LangGraph 丢掉，这里端到端确认 classification_source
        # 真的活着走到了 api/chat.py 要读它的地方（同上一条断言的 schema 检查）。
        assert result["classification_source"] == "llm"

    def test_emotion_only(self, monkeypatch):
        app, calls = self._app_with_spies(
            monkeypatch,
            {"has_emotion": True, "memory_kind": "none", "classification_source": "llm"},
        )
        self._invoke(app)

        assert sorted(calls) == ["conversation", "emotion"]

    def test_memory_only(self, monkeypatch):
        app, calls = self._app_with_spies(
            monkeypatch,
            {"has_emotion": False, "memory_kind": "fact", "classification_source": "llm"},
        )
        self._invoke(app)

        assert sorted(calls) == ["conversation", "memory"]

    def test_chat_only(self, monkeypatch):
        app, calls = self._app_with_spies(
            monkeypatch,
            {"has_emotion": False, "memory_kind": "none", "classification_source": "fast_path"},
        )
        self._invoke(app)

        assert sorted(calls) == ["conversation"]

    def test_graph_topology_is_frozen(self):
        """边集是冻结的：spec §3.3 要求逻辑边数从 9 条降到 7 条。

        刻意写成精确集合——新增一条边（比如后续工具调用加的 tools 节点）
        必须是有意识的决定，而不是顺手漂进来的。
        """
        from ai.agents.supervisor_graph import MultiAgentState, create_supervisor_app

        # supervisor 的三个输出必须在 schema 里：LangGraph 会**静默丢弃**未声明的
        # 键，少一个（比如 classification_source）不报错、不抛异常，只是 api/chat.py
        # 读回来永远是 ""——spec §3.5 记的就是这个失败形态。
        assert {"has_emotion", "memory_kind", "classification_source"} <= set(
            MultiAgentState.__annotations__
        )

        drawable = create_supervisor_app().get_graph()
        node_names = {"supervisor", "emotion", "memory", "conversation"}
        edges = [(e.source, e.target) for e in drawable.edges if e.source in node_names and e.target in node_names]

        assert sorted(edges) == [
            ("emotion", "conversation"),
            ("memory", "conversation"),
            ("supervisor", "conversation"),
            ("supervisor", "emotion"),
            ("supervisor", "memory"),
        ]

    def test_graph_has_no_cycles(self):
        """图必须无环——防环标志已随本次改造删除。

        单独一个测试，因为上面那条断言的是「边集没变」，不是「图无环」：
        冻结集合只统计四个节点之间的边，所以一条绕道新节点的环（后续加
        tools 节点时的 supervisor → tools → supervisor）能完全瞒过它。
        """
        from langgraph.graph import START

        from ai.agents.supervisor_graph import create_supervisor_app

        drawable = create_supervisor_app().get_graph()
        adjacency: dict[str, set[str]] = {}
        for edge in drawable.edges:
            # 刻意不按节点名过滤：两端不全在已知节点名里的边，正是要防的
            # 那类环；一过滤就把它滤掉了（本测试的第一版就是这么漏的）。
            adjacency.setdefault(edge.source, set()).add(edge.target)

        def reachable(start: str) -> set[str]:
            seen: set[str] = set()
            stack = [start]
            while stack:
                for nxt in adjacency.get(stack.pop(), ()):
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            return seen

        # 从 START 可达的每个节点都不能绕回自己。绕回 supervisor（也就绕回
        # 自己）与 emotion ↔ memory 互达都包含在内。
        for node in reachable(START) | {START}:
            assert node not in reachable(node), f"{node} 处在环上"


class TestSupervisorGraphParallelDatabaseAccess:
    """spec §6 的风险项：emotion 与 memory 首次在同一超步内执行。

    emotion 只调 LLM 不写库，memory 读库。LangGraph 把并行分支放在线程池里跑，
    而 Django 的数据库连接是线程绑定的——这个测试确认两个分支都能正常读写。
    """

    @pytest.mark.django_db(transaction=True)
    def test_real_memory_agent_reads_database_alongside_emotion(self, monkeypatch):
        from django.contrib.auth.models import User
        from langchain_core.messages import AIMessage, HumanMessage
        from ai.agents import memory_agent as mem
        from ai.agents import supervisor_graph as module
        from storage.models.character import Character
        from storage.models.friend import Friend
        from storage.models.user import UserProfile

        profile = UserProfile.objects.create(user=User.objects.create_user(username="parallel-db"))
        character = Character.objects.create(author=profile, name="女友", profile="温柔")
        friend = Friend.objects.create(me=profile, character=character)

        semantic_calls = []
        monkeypatch.setattr(mem, "search_semantic", lambda *a, **kw: (
            semantic_calls.append(kw) or []
        ))
        monkeypatch.setattr(mem.Reranker, "rerank", lambda self, query, docs, top_k: docs)
        monkeypatch.setattr(
            mem.ConversationHistorySearch, "search",
            lambda self, queries, **kwargs: [],
        )
        monkeypatch.setattr(
            mem.QueryRewriter, "plan",
            lambda self, *a, **kw: {"queries": ["还记得上次吗"], "temporal_anchor": "unknown"},
        )
        monkeypatch.setattr(module, "supervisor_node", lambda state: {
            "has_emotion": True, "memory_kind": "recall", "classification_source": "llm",
        })
        monkeypatch.setattr(module, "emotion_agent_node", lambda state: {
            "emotion_analysis": {"emotion": "sad", "intensity": 7},
        })
        monkeypatch.setattr(module, "conversation_agent_node", lambda state: (
            {"messages": [AIMessage(content="我记得")]}
        ))

        result = module.create_supervisor_app().invoke({
            "messages": [HumanMessage(content="你记得上次我很难过吗")],
            "has_emotion": False,
            "memory_kind": "none",
            "memory_context": "",
            "emotion_analysis": None,
            "character_profile": "温柔",
            "style_profile": "",
            "base_system_prompt": "",
            "time_context": "",
            "character_name": "女友",
            "chat_sender_name": "女友",
            "semantic_facts": [],
            "friend_id": friend.id,
            "character_id": character.id,
        })

        # search_semantic 在 Friend 查询之后无条件调用，include_imported 由
        # `bool(friend and can_access_imported_context(friend))` 决定。
        # 工作线程读不到那一行时 friend 是 None、这里是 False——所以断言 True
        # 才真正证明了跨线程读到了数据。只断言 retrieval_plan 不够：
        # .first() 查不到时返回 None 而不是抛异常，plan() 照跑。
        assert semantic_calls, "memory 节点没跑"
        assert all(call["include_imported"] is True for call in semantic_calls)
        assert result["emotion_analysis"]["intensity"] == 7
        assert result["messages"][-1].content == "我记得"
