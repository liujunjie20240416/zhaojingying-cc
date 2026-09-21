"""分层超时：请求路径上每个外部调用都要有界，整图还要有一个总 deadline。

这些断言单看很琐碎，但它们钉的是一笔**算术**：

    单点最坏 = timeout × (1 + max_retries)

openai SDK 的默认值是 read=600s 且 max_retries=2，于是单点最坏 ≈ 30 分钟，而且
httpx 的 read 是 inter-byte 超时——每收到一个字节就重置，连单次调用的总墙钟都不限。
谁把某个 client 的 timeout 或 max_retries 改回默认，那条路就悄悄变回无界，而除了
这里没有任何测试会变红。
"""
import importlib
import json

import pytest


@pytest.mark.parametrize(
    "module_path,class_name",
    [
        ("ai.rag.reranker", "Reranker"),
        ("ai.rag.query_rewriter", "QueryRewriter"),
        ("ai.rag.compressor", "ContextCompressor"),
    ],
)
def test_rag_clients_carry_sub_llm_timeout(monkeypatch, module_path, class_name):
    """重排 / 检索规划 / 压缩——三个图内辅助 LLM，走同一档超时。"""
    from ai.config import sub_llm_timeout

    module = importlib.import_module(module_path)
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(module, "OpenAI", FakeClient)
    getattr(module, class_name)(api_key="k", api_base="https://example.invalid")

    assert captured["timeout"] == sub_llm_timeout()
    assert captured["max_retries"] == 1


def test_custom_embeddings_carry_embedding_timeout(monkeypatch):
    """embedding 单独一档。

    它比 LLM 调用短得多，但单轮最多被调 9 次（3 query × semantic/imported/online），
    每次新起一个 client。沿用 LLM 的预算的话，光这一项的最坏值就能顶掉整轮 deadline。
    """
    from ai import custom_embeddings as module
    from ai.config import embedding_timeout

    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(module, "OpenAI", FakeClient)
    module.CustomEmbeddings()

    assert captured["timeout"] == embedding_timeout()
    assert captured["max_retries"] == 1


def test_emotion_agent_carries_sub_llm_timeout(monkeypatch):
    """情绪分析炸了本来就只降级成 neutral，没有理由让它拖住整轮。"""
    from langchain_core.messages import HumanMessage

    from ai.agents import emotion_agent as module
    from ai.config import sub_llm_timeout

    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            raise RuntimeError("这里只验构造参数")

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(module, "OpenAI", FakeClient)
    module.emotion_agent_node({"messages": [HumanMessage(content="好累")]}, api_key="k", api_base="u")

    assert captured["timeout"] == sub_llm_timeout()
    assert captured["max_retries"] == 1


def test_turn_deadline_bounds_the_whole_graph(monkeypatch):
    """轮级 deadline 必须在，而且要真的被传给 app.ainvoke 那一层。

    上面每个单点超时都只管「一处挂住」。多处依次挂住时总时长仍然无界——并行扇出
    的墙钟是 max 而不是 sum，但扇出之后的 conversation 节点还要再叠一次。deadline
    管的就是这个尾巴。
    """
    import asyncio

    from api import chat as module
    from ai.config import turn_deadline

    captured = {}

    class SlowApp:
        async def ainvoke(self, inputs, config=None):
            captured["invoked"] = True
            await asyncio.sleep(30)
            return {}

    monkeypatch.setattr(module, "turn_deadline", lambda: 0.01)

    class FakeMQ:
        def put_nowait(self, item):
            pass

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(
            module.tts_sender(SlowApp(), {}, FakeMQ(), object(), "task-1")
        )

    assert captured["invoked"], "deadline 要包住 app.ainvoke，不是绕过它"
    assert turn_deadline() > 0


def test_turn_deadline_error_reaches_the_client_through_work(monkeypatch):
    """超时要变成前端能看见的错误，而不是让流静静地断掉。

    超时和其它图失败对用户是同一件事「重试」，所以共用 work() 那条错误通道；
    差别只在服务端日志里。
    """
    from api import chat as module

    class BoomApp:
        async def ainvoke(self, inputs, config=None):
            raise TimeoutError("deadline")

    sent = []

    class FakeMQ:
        def put_nowait(self, item):
            sent.append(item)

    module.work(BoomApp(), {"friend_id": 1}, FakeMQ(), None)

    assert sent[-1] is None, "无论如何都要发终止哨兵，否则 SSE 流不会结束"
    assert any(
        item and item.get("error", {}).get("message") for item in sent if item
    ), "图失败必须给前端一条可见的错误"
