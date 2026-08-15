from langchain_core.messages import AIMessage, HumanMessage


def test_text_conversation_uses_deepseek_configuration(monkeypatch):
    from ai.agents import conversation_agent as module

    captured = {}

    class FakeLLM:
        def invoke(self, messages):
            return AIMessage(content='{"bubbles":["好"]}')

    def fake_chat_openai(**kwargs):
        captured.update(kwargs)
        return FakeLLM()

    monkeypatch.setattr(module, "ChatOpenAI", fake_chat_openai)
    monkeypatch.setattr(module, "chat_model", lambda: "deepseek-v4-pro")
    monkeypatch.setattr(module, "chat_api_key", lambda: "deepseek-key")
    monkeypatch.setattr(module, "chat_api_base", lambda: "https://deepseek.example/v1")
    monkeypatch.setattr(module, "require_chat_config", lambda: None)

    module.conversation_agent_node({
        "messages": [HumanMessage(content="你好")],
        "base_system_prompt": "规则",
    })

    assert captured["model"] == "deepseek-v4-pro"
    assert captured["openai_api_key"] == "deepseek-key"
    assert captured["openai_api_base"] == "https://deepseek.example/v1"


def test_image_conversation_keeps_glm_vision_configuration(monkeypatch):
    from ai.agents import conversation_agent as module

    captured = {}

    class FakeLLM:
        def invoke(self, messages):
            return AIMessage(content='{"bubbles":["看到了"]}')

    def fake_chat_openai(**kwargs):
        captured.update(kwargs)
        return FakeLLM()

    monkeypatch.setattr(module, "ChatOpenAI", fake_chat_openai)
    monkeypatch.setattr(module, "vision_llm_model", lambda: "glm-5v-turbo")
    monkeypatch.setattr(module, "vision_llm_api_key", lambda: "glm-key")
    monkeypatch.setattr(module, "vision_llm_api_base", lambda: "https://glm.example/v4")
    monkeypatch.setattr(module, "require_llm_config", lambda: None)

    module.conversation_agent_node({
        "messages": [HumanMessage(content="看看这张图")],
        "base_system_prompt": "规则",
        "vision_attachments": [{"id": 1, "data_url": "data:image/png;base64,AA=="}],
    })

    assert captured["model"] == "glm-5v-turbo"
    assert captured["openai_api_key"] == "glm-key"
    assert captured["openai_api_base"] == "https://glm.example/v4"


def test_fixed_rules_precede_dynamic_context_for_prompt_cache(monkeypatch):
    from ai.agents import conversation_agent as module

    captured = {}

    class FakeLLM:
        def invoke(self, messages):
            captured["messages"] = messages
            return AIMessage(content='{"bubbles":["好"]}')

    monkeypatch.setattr(module, "ChatOpenAI", lambda **kwargs: FakeLLM())
    module.conversation_agent_node({
        "messages": [HumanMessage(content="今天怎么样")],
        "base_system_prompt": "稳定基础规则",
        "character_profile": "稳定角色设定",
        "style_profile": "稳定风格",
        "time_context": "动态当前时间",
        "conversation_summary": "动态较早摘要",
        "memory_context": "动态检索记忆",
        "emotion_analysis": {"intensity": 8, "suggested_tone": "gentle"},
    }, api_key="test", api_base="https://example.invalid")

    prompt = captured["messages"][0].content
    assert prompt.index("【重要规则】") < prompt.index("动态当前时间")
    assert prompt.index("动态较早摘要") < prompt.index("动态检索记忆")
    assert prompt.index("动态检索记忆") < prompt.index("动态当前时间")
    assert prompt.index("动态当前时间") < prompt.index("用户情绪强烈")


def test_simple_daily_question_requires_direct_answer(monkeypatch):
    from ai.agents import conversation_agent as module

    captured = {}

    class FakeLLM:
        def invoke(self, messages):
            captured["prompt"] = messages[0].content
            return AIMessage(content='{"bubbles":["就吃学校门口那家吧"]}')

    monkeypatch.setattr(module, "ChatOpenAI", lambda **kwargs: FakeLLM())
    module.conversation_agent_node({
        "messages": [HumanMessage(content="晚上吃什么")],
        "base_system_prompt": "规则",
    }, api_key="test", api_base="https://example.invalid")

    assert "第一气泡必须直接回答用户问的内容" in captured["prompt"]
    assert "每个不超过 60 个中文字符" in captured["prompt"]
    assert "不要先说想念、关心、工作、问候等无关内容" in captured["prompt"]
