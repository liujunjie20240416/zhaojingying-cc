import pytest


class TestSupervisor:
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
    def test_detect_emotion(self, api_key, api_base):
        from ai.agents.emotion_agent import emotion_agent_node
        Msg = type("msg", (), {"content": "我今天好难过，工作好累"})
        state = {"messages": [Msg()]}
        result = emotion_agent_node(state, api_key=api_key, api_base=api_base)
        assert "emotion_analysis" in result
        assert "emotion" in result["emotion_analysis"]


class TestConversationAgent:
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
