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
        earlier_ai = Msg()
        earlier_ai.content = "我记不清了，我们去翻聊天记录吧"
        follow_up = Msg()
        follow_up.content = "你去翻，翻完跟我说"

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
