import pytest


class TestSupervisor:
    def test_short_follow_up_is_classified_with_recent_dialogue(self, monkeypatch):
        from ai.agents import supervisor

        captured = {}

        def fake_classify(user_msg, emotion_context, api_key, api_base, recent_dialogue=""):
            captured["dialogue"] = recent_dialogue
            return {"intent": "recall", "delegate_to": "memory"}

        monkeypatch.setattr(supervisor, "_classify_with_llm", fake_classify)
        Msg = type("msg", (), {})
        earlier_user = Msg()
        earlier_user.content = "咱们哪一年认识的"
        earlier_ai = Msg()
        earlier_ai.content = "我记不清了，我们去翻聊天记录吧"
        follow_up = Msg()
        follow_up.content = "你去翻，翻完跟我说"

        result = supervisor.supervisor_node({"messages": [earlier_user, earlier_ai, follow_up]})

        assert result["intent"] == "recall"
        assert "咱们哪一年认识的" in captured["dialogue"]
        assert "翻聊天记录" in captured["dialogue"]

    def test_short_follow_up_inherits_previous_intent(self, monkeypatch):
        """上轮 recall + 短消息 → 继承上轮意图，不调用 LLM 分类。"""
        from ai.agents import supervisor

        def fake_classify(*args, **kwargs):
            raise AssertionError("LLM classification must not run when inheriting")

        monkeypatch.setattr(supervisor, "_classify_with_llm", fake_classify)
        Msg = type("msg", (), {})
        earlier_user = Msg()
        earlier_user.content = "迪士尼那次你还记得吗"
        earlier_ai = Msg()
        earlier_ai.content = "记得呀"
        follow_up = Msg()
        follow_up.content = "还有呢？"

        result = supervisor.supervisor_node({
            "messages": [earlier_user, earlier_ai, follow_up],
            "previous_intent": "recall",
        })

        assert result["intent"] == "recall"
        assert result["classification_source"] == "inherit"

    def test_short_ack_after_chat_stays_chat(self, monkeypatch):
        """上轮 chat + 无指代线索的短消息 → 直接 chat，不调用 LLM。"""
        from ai.agents import supervisor

        def fake_classify(*args, **kwargs):
            raise AssertionError("LLM classification must not run for plain acks")

        monkeypatch.setattr(supervisor, "_classify_with_llm", fake_classify)
        Msg = type("msg", (), {})
        earlier_user = Msg()
        earlier_user.content = "今天天气不错"
        earlier_ai = Msg()
        earlier_ai.content = "是呀"
        follow_up = Msg()
        follow_up.content = "哈哈"

        result = supervisor.supervisor_node({
            "messages": [earlier_user, earlier_ai, follow_up],
            "previous_intent": "chat",
        })

        assert result["intent"] == "chat"
        assert result["classification_source"] == "short_chat"

    def test_short_referential_question_after_chat_still_classifies(self, monkeypatch):
        """上轮 chat + 指代性短问（"迪士尼呢？"）→ 仍走 LLM 分类保留通道。"""
        from ai.agents import supervisor

        captured = {}

        def fake_classify(user_msg, emotion_context, api_key, api_base, recent_dialogue=""):
            captured["msg"] = user_msg
            return {"intent": "recall", "delegate_to": "memory"}

        monkeypatch.setattr(supervisor, "_classify_with_llm", fake_classify)
        Msg = type("msg", (), {})
        earlier_user = Msg()
        earlier_user.content = "吃饭了吗"
        earlier_ai = Msg()
        earlier_ai.content = "吃了"
        follow_up = Msg()
        follow_up.content = "迪士尼呢？"

        result = supervisor.supervisor_node({
            "messages": [earlier_user, earlier_ai, follow_up],
            "previous_intent": "chat",
        })

        assert result["intent"] == "recall"
        assert captured["msg"] == "迪士尼呢？"

    def test_route_chat_intent(self, api_key, api_base):
        from ai.agents.supervisor import supervisor_node
        Msg = type("msg", (), {"content": "你好呀"})
        state = {
            "messages": [Msg()],
            "intent": "",
            "memory_context": "",
            "emotion_analysis": None,
            "character_profile": "温柔体贴的女友",
            "semantic_facts": [],
        }
        result = supervisor_node(state, api_key=api_key, api_base=api_base)
        assert "intent" in result
        assert result["intent"] in ("chat", "recall", "emotional")

    def test_route_recall_intent(self, api_key, api_base):
        from ai.agents.supervisor import supervisor_node
        Msg = type("msg", (), {"content": "你还记得我们第一次见面吗"})
        state = {
            "messages": [Msg()],
            "intent": "", "memory_context": "", "emotion_analysis": None,
            "character_profile": "温柔体贴的女友", "semantic_facts": [],
        }
        result = supervisor_node(state, api_key=api_key, api_base=api_base)
        assert result["intent"] == "recall"


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
