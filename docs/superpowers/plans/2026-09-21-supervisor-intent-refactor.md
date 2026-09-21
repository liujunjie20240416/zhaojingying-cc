# Supervisor 多标签分类重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 supervisor 从「单标签关键词路由」改成「一次 LLM 调用输出多标签（has_emotion + memory_kind）」，让情绪与记忆可以同时命中并**并行**执行。

**Architecture:** supervisor 节点只做一次分类，返回 `has_emotion` / `memory_kind`；条件边函数返回节点名**列表**，LangGraph 在原超步内并行扇出到 emotion / memory，两者都走固定边汇入 conversation。图从有环变无环，删除全部防环标志与永不触发的死边。

**Tech Stack:** Python 3.12、LangGraph 1.2.0、LangChain Core、OpenAI SDK（DeepSeek）、Django 6、pytest。

**Spec:** `docs/superpowers/specs/2026-09-21-supervisor-intent-refactor-design.md`

**已验证的前置条件：** `.venv/bin/python` 下的 langgraph 1.2.0 中，条件边函数返回节点名列表会产生并行扇出，汇入节点只在两者都完成后执行一次。验证代码见 Task 0。

**关于评测集（spec §7）：本次不做，原因如下。**

评测集唯一的用途是回答「换掉关键词表之后判断变好了还是变差」。本机没有真实使用数据（`web_message` 只有 4 行），唯一的大语料是 `chat_message` 里导入的微信历史（23,261 行，人类侧 9,282 行）——那是**人与人的对话**，与 supervisor 实际分类的「人对 AI 伴侣说的一句话」是两个分布。在这种代理语料上测出的准确率看着权威但不迁移，比没有数字更危险。另外实测「事实类」样本在该语料里根本不存在（人类侧「生日」3 条、「喜欢」14 条、「关系」3 条，且都是字面意思），`memory_kind` 三值里只有 `none` / `recall` 可测。

所以本计划用**单元测试**验证结构正确性（单标签强制消失、死边消失、情绪与记忆能同时跑、`memory_kind` 驱动检索策略），不产出准确率数字。分类器提示词与快速通道白名单的调优，等有真实使用数据后另建 harness。

**关于执行顺序：** Task 1 → 2 → 3 顺次执行。Task 1 落地后，旧的关键词路由就不存在了，无法再回头采基线。

**已有测试基线（改造前，请勿打破）：**

```
$ .venv/bin/pytest tests/test_agents.py -k Supervisor -q
6 passed, 1 skipped, 3 deselected
```

---

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `ai/agents/supervisor.py` | 重写 | 分类器 + 快速通道 + 可观测性 |
| `ai/agents/supervisor_graph.py` | 改造 | 状态字段、并行扇出路由、删死边 |
| `ai/agents/memory_agent.py` | 改 3 行 | `state["intent"]` → `state["memory_kind"]` |
| `api/chat.py` | 改 3 处 | provenance 字段、删意图继承、初始 state |
| `tests/test_agents.py` | 改 | 重写 `TestSupervisor` / `TestSupervisorGraph` |
| `tests/test_memory_refactor.py` | 改 | 3 处断言同步 |

**不动的文件：** `ai/memory/intent.py`（`memory_agent.py:233` 仍在用它推导 `time_mode` / `target_subject` / `category_hint` / `needs_state_trajectory`）、`ai/agents/emotion_agent.py`、`ai/agents/conversation_agent.py`。

---

## Task 0: 验证并行扇出（已完成，留作复核）

**Files:** 无（一次性验证，不落盘）

- [x] **Step 1: 跑最小图验证列表返回产生并行扇出**

```bash
.venv/bin/python - <<'PY'
from typing import TypedDict, Annotated, Sequence
from langgraph.graph import StateGraph, START, END
from langgraph.graph import add_messages

class S(TypedDict):
    messages: Annotated[Sequence, add_messages]
    flag_emotion: bool
    flag_memory: bool

calls = []
def sup(state): calls.append("supervisor"); return {}
def emo(state): calls.append("emotion"); return {"emotion_marker": 1}
def mem(state): calls.append("memory"); return {"memory_marker": 1}
def conv(state): calls.append("conversation"); return {}

def route(state):
    t = []
    if state.get("flag_emotion"): t.append("emotion")
    if state.get("flag_memory"): t.append("memory")
    return t or ["conversation"]

g = StateGraph(S)
for n, f in [("supervisor",sup),("emotion",emo),("memory",mem),("conversation",conv)]:
    g.add_node(n, f)
g.add_edge(START, "supervisor")
g.add_conditional_edges("supervisor", route, {"emotion":"emotion","memory":"memory","conversation":"conversation"})
g.add_edge("emotion", "conversation")
g.add_edge("memory", "conversation")
g.add_edge("conversation", END)
app = g.compile()

for flags in [{"flag_emotion":True,"flag_memory":True},{"flag_emotion":True},{"flag_memory":True},{}]:
    calls.clear()
    app.invoke({"messages":[], **flags})
    print(flags, "->", calls)
PY
```

Expected output（实测）：

```
{'flag_emotion': True, 'flag_memory': True} -> ['supervisor', 'emotion', 'memory', 'conversation']
{'flag_emotion': True} -> ['supervisor', 'emotion', 'conversation']
{'flag_memory': True} -> ['supervisor', 'memory', 'conversation']
{} -> ['supervisor', 'conversation']
```

**结论：** 双标签同超步并行执行，conversation 只在最后执行一次。同时确认了另一个事实：节点返回状态 schema 之外的键会被 LangGraph **静默丢弃**（`emotion_marker` 没出现在结果里），所以 `confidence` 不进状态字段、只进 trace。

---

## Task 1: 重写 `ai/agents/supervisor.py`

**Files:**
- Modify: `ai/agents/supervisor.py`（整文件重写）
- Test: `tests/test_agents.py`（`TestSupervisor`）

- [ ] **Step 1: 写失败测试**

把 `tests/test_agents.py` 的整个 `TestSupervisor` 类（第 4–128 行）替换为：

```python
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
        earlier_user.type = "human"
        earlier_user.content = "咱们哪一年认识的"
        earlier_ai = Msg()
        earlier_ai.type = "ai"
        earlier_ai.content = "我记不清了，我们去翻聊天记录吧"
        follow_up = Msg()
        follow_up.type = "human"
        follow_up.content = "你去翻，翻完跟我说"

        result = supervisor.supervisor_node({"messages": [earlier_user, earlier_ai, follow_up]})

        assert result["memory_kind"] == "recall"
        assert "咱们哪一年认识的" in captured["dialogue"]
        assert "翻聊天记录" in captured["dialogue"]
        assert captured["msg"] == "你去翻，翻完跟我说"

    def test_tool_message_is_not_user_input(self, monkeypatch):
        """ToolMessage 不能被当成用户消息。

        这是 I3 真正要防的那种输入。带 tool_calls 的 AIMessage 测不出来——
        它本来就带 tool_calls 属性，旧的 `hasattr(msg, "tool_calls")` 判定会
        **碰巧**跳过它，于是测试对着旧代码也是绿的（写错过一次，见下）。
        ToolMessage 两个属性都没有，旧实现会把它读成用户消息。
        """
        from ai.agents import supervisor
        from langchain_core.messages import AIMessage, ToolMessage

        captured = {}

        def fake_classify(user_msg, *a, **kw):
            captured["msg"] = user_msg
            return {"has_emotion": False, "memory_kind": "recall", "classification_source": "llm"}

        monkeypatch.setattr(supervisor, "_classify_with_llm", fake_classify)
        result = supervisor.supervisor_node({"messages": [
            HumanMessage(content="你还记得那次旅行吗"),
            AIMessage(content="我查一下", tool_calls=[
                {"name": "search", "args": {}, "id": "call_1"},
            ]),
            ToolMessage(content="搜索结果：2023年去过京都", tool_call_id="call_1"),
        ]})

        assert captured["msg"] == "你还记得那次旅行吗"
        assert result["memory_kind"] == "recall"

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
```

同时在文件顶部、`import pytest` 下面加一行：

```python
from langchain_core.messages import HumanMessage
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/pytest tests/test_agents.py -k "Supervisor" -q
```

Expected：FAIL —— `ImportError` / `AttributeError`（`has_emotion` 还不存在），且旧的 `TestSupervisor` 已被删除。

- [ ] **Step 3: 重写 `ai/agents/supervisor.py`**

用以下内容整文件替换 `ai/agents/supervisor.py`：

```python
# ai/agents/supervisor.py
"""Supervisor — 一次 LLM 调用把用户消息判成多标签。

输出 has_emotion 与 memory_kind 两个**互相独立**的判断，路由图据此决定
是并行进 emotion / memory，还是直接进 conversation。

为什么不看关键词：意图本来就不是单选。「上次吵架我好难过」同时需要情绪回应
和历史检索，任何单标签分类器都会丢掉一半。
"""

import json
import unicodedata

from openai import OpenAI

from ai.config import llm_api_base, llm_api_key, llm_model
from ai.tracing import record_trace


# 无信息量的确认语：既没有可检索的事实，也没有情绪信号。
# 刻意不含「哦」「呵呵」「随便」——这些词在不同语境下含义相反（冷淡 or 撒娇），
# 必须交给分类器判断。误判的代价比省下的那次调用高。
LIGHTWEIGHT_ACKS = frozenset({
    "嗯", "嗯嗯", "哦哦", "好", "好的", "好哒", "好呀", "行", "收到",
    "哈哈", "哈哈哈", "嘿嘿", "早", "晚安", "在吗",
})

# 「这条消息有没有可回应的内容」用 Unicode 类别判定：只由标点（P*）和
# 空白（Z*）组成就算没有。不能用「不含汉字/字母/数字」的字符白名单——
# 那样裸的 💔 会被当成无信息量消息吞掉，详见 _is_contentless 的注释。
_CONTENTLESS_CATEGORIES = frozenset("PZ")

# 分类器从「偶发调用」变成「每条消息都调用」，20s 的最坏情况不再可接受。
CLASSIFIER_TIMEOUT = 5

_VALID_MEMORY_KINDS = {"none", "recall", "fact"}


def _no_op_decision(source: str) -> dict:
    return {
        "has_emotion": False,
        "memory_kind": "none",
        "classification_source": source,
    }


def _last_user_msg(state: dict) -> str:
    """取最后一条用户消息。

    按 LangChain 的 `type` 判定，而不是「有没有 tool_calls 属性」：AIMessage
    与 ToolMessage 都不该被当成用户输入，而 `hasattr(msg, "tool_calls")` 只是
    一个恰好成立的巧合——工具调用落地后 ToolMessage 进入状态，这条会读错。
    """
    for msg in reversed(state.get("messages", []) or []):
        if isinstance(msg, dict):
            if msg.get("role") == "user":
                return msg.get("content", "") or ""
        elif getattr(msg, "type", "") == "human":
            return getattr(msg, "content", "") or ""
    return ""


def _is_contentless(user_msg: str) -> bool:
    """消息是否只有标点或空白——没有可回应的内容。

    用 Unicode 类别而不是字符白名单。字符白名单（`[一-鿿A-Za-z0-9]`）会漏掉
    表外 emoji：『💔』不含任何「内容字符」，于是被判成无信息量、走快速通道、
    has_emotion=False，用户得不到情绪回应。而且这个失败是静默的——
    classification_source 记的是 fast_path，看起来是最健康的那个值。
    """
    text = user_msg.strip()
    if text in LIGHTWEIGHT_ACKS:
        return True
    if len(text) > 8:
        return False
    return all(unicodedata.category(ch)[0] in _CONTENTLESS_CATEGORIES for ch in text)


def supervisor_node(state: dict, api_key: str = "", api_base: str = "") -> dict:
    """把这一轮判成 has_emotion + memory_kind，两个标签互相独立。"""
    user_msg = _last_user_msg(state)
    # 其余 agent 的 record_trace 都带 trace_metadata（friend_id / character_id /
    # entrypoint），supervisor 不带就会变成游离的、筛不出来的 span。
    trace_metadata = state.get("trace_metadata", {})
    emotion_context = state.get("emotion_context") or []

    if not user_msg:
        result = _no_op_decision("empty")
        record_trace("supervisor.route", {"user_msg": user_msg}, result, metadata=trace_metadata)
        return result

    # 快速通道只处理「确定没有信息量」的消息。emoji 语义是快速通道看不见的
    # 情绪信号，所以带 emotion_context 的消息一律过分类器。
    if not emotion_context and _is_contentless(user_msg):
        result = _no_op_decision("fast_path")
        record_trace("supervisor.route", {"user_msg": user_msg}, result, metadata=trace_metadata)
        return result

    recent_dialogue = _recent_dialogue(state.get("messages", []))
    result = _classify_with_llm(user_msg, emotion_context, api_key, api_base, recent_dialogue)
    trace_inputs = {
        "model": llm_model(),
        "user_msg": user_msg,
        "emotion_context": emotion_context,
        "recent_dialogue": recent_dialogue,
        "messages": [{"role": "user", "content": _build_classifier_prompt(
            user_msg, emotion_context, recent_dialogue)}],
    }
    # 降级那一轮最该被复盘——「降级率突然涨了」是唯一一个能自己冒出来的信号，
    # 但只看 classification_source 不知道涨在哪：超时？JSON 烂？鉴权挂了？
    if result.get("_error"):
        trace_inputs["error"] = result["_error"]
    record_trace(
        "supervisor.route",
        trace_inputs,
        result,
        run_type="llm",
        metadata=trace_metadata,
    )
    return result


def _recent_dialogue(messages: list, limit: int = 4) -> str:
    """Format preceding turns for resolving a short, context-dependent reply."""
    preceding = list(messages[:-1])[-limit:]
    lines = []
    for message in preceding:
        content = getattr(message, "content", "")
        if not content:
            continue
        message_type = getattr(message, "type", "")
        role = "AI" if message_type == "ai" else "用户"
        lines.append(f"{role}：{str(content)[:500]}")
    return "\n".join(lines)


def _build_classifier_prompt(user_msg: str, emotion_context: list, recent_dialogue: str) -> str:
    """单独成函数：supervisor_node 要把它原样记进 trace，供事后逐条复盘。"""
    return f"""判断这条伴侣聊天消息需要哪些处理，只输出 JSON。
最近对话（可能为空；用于理解省略的指代，不要把它当用户当前问题）：
{recent_dialogue or "（无）"}
用户消息：{user_msg}
前端识别到的 emoji 含义：{json.dumps(emotion_context, ensure_ascii=False)}

{{"has_emotion": false, "memory_kind": "none", "confidence": 0.0}}

has_emotion：是否需要情绪回应——用户在表达情绪、需要安慰或共情，或者 emoji 带有情绪含义。两个判断互相独立，可以同时为真。
memory_kind：
  recall = 询问过去具体发生过的事、说过的原话（"上次""那次""你还记得"）
  fact   = 询问稳定的身份、偏好、习惯或关系（"我喜欢什么""我们是什么关系"）
  none   = 不需要翻记忆
confidence：你对上述判断的把握，0 到 1。"""


def _classify_with_llm(
    user_msg: str,
    emotion_context: list,
    api_key: str,
    api_base: str,
    recent_dialogue: str = "",
) -> dict:
    """一次调用同时回答两个问题；失败时降级到「检索一次」。"""
    try:
        client = OpenAI(
            api_key=api_key or llm_api_key(),
            base_url=api_base or llm_api_base(),
            timeout=CLASSIFIER_TIMEOUT,
        )
        response = client.chat.completions.create(
            model=llm_model(),
            messages=[{"role": "user", "content": _build_classifier_prompt(
                user_msg, emotion_context, recent_dialogue)}],
            temperature=0,
            max_tokens=120,
            response_format={"type": "json_object"},
        )
        content = str(response.choices[0].message.content or "").strip()
        # 空回复必须当成失败。若让它 fall through 到 json.loads("{}")，
        # 会返回 {has_emotion: false, memory_kind: "none"} 且 source 记为
        # "llm"——一个看起来完全健康的「不需要记忆」，实际什么都没判。
        if not content:
            raise ValueError("classifier returned empty content")
        parsed = json.loads(content)
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError("classifier returned no fields")

        memory_kind = parsed.get("memory_kind", "none")
        if memory_kind not in _VALID_MEMORY_KINDS:
            memory_kind = "none"

        # 不能直接 bool()：模型把布尔写成字符串时 bool("false") 是 True。
        has_emotion = parsed.get("has_emotion", False)
        if not isinstance(has_emotion, bool):
            has_emotion = str(has_emotion).strip().lower() in {"true", "yes", "1"}

        try:
            confidence = float(parsed.get("confidence", 0) or 0)
        except (TypeError, ValueError):
            # 分类本身是成功的，只有 confidence 脏——不能因此把整个判断丢掉。
            confidence = 0.0

        return {
            "has_emotion": has_emotion,
            "memory_kind": memory_kind,
            "confidence": confidence,
            "classification_source": "llm",
        }
    except Exception as exc:
        # 分类失败不能让用户失去一次记忆：宁可按最宽的检索模式跑一次。
        # has_emotion 取 False——情绪分析本身也是一次 LLM 调用，分类器都连不上，
        # 它大概率也连不上，重试只是叠加延迟。
        return {
            "has_emotion": False,
            "memory_kind": "recall",
            "confidence": 0.0,
            "classification_source": "fallback",
            # 私有键，只给 supervisor_node 记 trace 用。LangGraph 会静默丢弃
            # 不在 MultiAgentState schema 里的键（实现阶段已验证），所以它不会
            # 泄漏进图状态。失败原因必须带出来：降级本身只是「没判」，
            # 知道了原因才知道该去修什么。
            "_error": f"{type(exc).__name__}: {exc}",
        }
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/pytest tests/test_agents.py -k "Supervisor" -q
```

Expected：PASS，21 个测试全过。

数字用 `pytest --collect-only` 按类数出来，不要靠加总——这行先前连着写错两次（一次 12，
一次 17）。参考：FastPath 7 + Classifier 6 + Fallback 8。

同时 `tests/test_memory_refactor.py::test_supervisor_skips_memory_for_chat_and_time` 会失败。
这是**预期的**：它断言的是旧的 `delegate_to` / `intent` 契约，由 Task 3 负责重写。

### Task 1 完成记录（2026-09-21）

上面 Step 1 列出的测试**不是最终清单**——代码质量复审后又补了几个。最终 21 个：

| 类 | 数量 | 复审后新增 |
|---|---|---|
| `TestSupervisorFastPath` | 7 | `test_emoji_outside_frontend_table_still_reaches_classifier`、`test_fullwidth_alphanumerics_reach_classifier`、`test_whitespace_only_message_still_skips_llm` |
| `TestSupervisorClassifier` | 6 | `test_tool_message_is_not_user_input`、`test_supervisor_trace_carries_trace_metadata`、`test_llm_trace_records_what_was_actually_sent` |
| `TestSupervisorFallback` | 8 | `test_empty_completion_is_a_failure_not_a_confident_no_op`、`test_garbage_confidence_does_not_discard_the_classification`、`test_string_false_has_emotion_is_not_truthy`、`test_fallback_trace_records_why_it_fell_back`、`test_error_key_is_confined_to_the_fallback_path` |

复审结论：0 Critical / 0 Important。三条 Minor 已修（I4 的 trace 断言补全、降级原因进 trace、
`_error` 键的作用域测试）。

**两条留给后续的观察**（不是本次的缺陷）：

1. `_recent_dialogue` 把所有非 `"ai"` 的消息都标成 `用户`。工具调用落地后，分类器提示词里
   会出现 `用户：搜索结果：…` 这样的行。I3 的修复理由是「工具消息马上要进这个 state」，
   同一条理由也适用于这里——归到工具调用那份 spec 里一起处理。
2. 模型返回带 markdown 围栏的 JSON 时仍会降级（分类器没有像 `emotion_agent.py:49-53`
   那样剥 ```）。`response_format={"type": "json_object"}` 之下基本不会发生，先记着。
**注意**：`confidence` 不在 `MultiAgentState` schema 里，会被 LangGraph 丢弃——它只用于 `record_trace` 观测，不参与路由。这是刻意的。

- [ ] **Step 5: 提交**

```bash
git add ai/agents/supervisor.py tests/test_agents.py
git commit -m "$(cat <<'EOF'
refactor: replace supervisor keyword table with a multi-label classifier

One LLM call now answers has_emotion and memory_kind independently, so a
message that needs both comfort and recall ("last time we argued I felt
awful") is no longer forced into a single label. Keyword tables and the
intent-inheritance fast path are gone; the only remaining fast path is a
whitelist of contentless acknowledgements, and it is bypassed whenever the
frontend supplied an emoji meaning. Classifier timeout drops 20s -> 5s
because it now runs on every message.

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 改造 `ai/agents/supervisor_graph.py` 为并行扇出

**Files:**
- Modify: `ai/agents/supervisor_graph.py`
- Test: `tests/test_agents.py`（`TestSupervisorGraph`）

- [ ] **Step 1: 写失败测试**

把 `tests/test_agents.py` 的 `TestSupervisorGraph` 类整体替换为：

```python
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

    def test_graph_has_no_cycles(self):
        """图必须无环——防环标志已随本次改造删除。"""
        from ai.agents.supervisor_graph import create_supervisor_app

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


class TestSupervisorGraphParallelDatabaseAccess:
    """spec §6 的风险项：emotion 与 memory 首次在同一超步内执行。

    emotion 只调 LLM 不写库，memory 读库。LangGraph 把并行分支放在线程池里跑，
    而 Django 的数据库连接是线程绑定的——这个测试确认两个分支都能正常读写。
    """

    @pytest.mark.django_db
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

        monkeypatch.setattr(mem, "search_semantic", lambda *a, **kw: [])
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

        # 真的跑了 memory 节点（而不是被跳过）
        assert "memory_context" in result
        assert result["emotion_analysis"]["intensity"] == 7
        assert result["messages"][-1].content == "我记得"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/pytest tests/test_agents.py -k "SupervisorGraph" -q
```

Expected：FAIL —— `TypeError`（`route_from_supervisor` 返回 str 不是 list）/ 边集合不符。

- [ ] **Step 3: 重写 `ai/agents/supervisor_graph.py`**

用以下内容整文件替换 `ai/agents/supervisor_graph.py`：

```python
# ai/agents/supervisor_graph.py
"""Supervisor Graph — Multi-Agent 主编排图。

编排流程:
    START → Supervisor → 分类（has_emotion / memory_kind）
        ├── 两者都命中 → emotion 与 memory 并行 ─┐
        ├── 只命中情绪 → emotion ───────────────┼──▶ Conversation → END
        ├── 只命中记忆 → memory ────────────────┤
        └── 都没命中 ───────────────────────────┘

图是无环的：并行扇出把「先情绪后补记忆」的串行补丁换成了同一步内的两支，
所以不需要 memory_done / emotion_done 之类的防环标志。
"""
from typing import TypedDict, Annotated, Sequence, NotRequired
from langchain_core.messages import BaseMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph import add_messages

from ai.agents.supervisor import supervisor_node
from ai.agents.memory_agent import memory_agent_node
from ai.agents.emotion_agent import emotion_agent_node
from ai.agents.conversation_agent import conversation_agent_node


class MultiAgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    # Supervisor 的两个独立判断，取代了原来的单标签 intent / delegate_to。
    has_emotion: NotRequired[bool]
    memory_kind: NotRequired[str]
    classification_source: NotRequired[str]
    memory_context: str
    memory_sections: NotRequired[list[dict]]
    memory_intent: NotRequired[dict]
    retrieval_plan: NotRequired[dict]
    reply_provenance: NotRequired[dict]
    emotion_analysis: dict | None
    character_profile: str
    style_profile: str
    base_system_prompt: str
    time_context: str
    conversation_summary: str
    character_name: str
    chat_sender_name: str
    semantic_facts: list[str]
    core_memory_context: NotRequired[str]
    last_provider_input_tokens: NotRequired[int]
    friend_id: int
    character_id: int | None
    trace_metadata: NotRequired[dict]
    emotion_context: NotRequired[list]
    vision_attachments: NotRequired[list]


def route_from_supervisor(state: dict) -> list[str]:
    """返回节点名**列表**即触发并行扇出（LangGraph 同超步执行）。

    返回空列表会走不通，所以两个标签都没命中时显式落到 conversation。
    """
    targets = []
    if state.get("has_emotion"):
        targets.append("emotion")
    if state.get("memory_kind", "none") != "none":
        targets.append("memory")
    return targets or ["conversation"]


def create_supervisor_app(
    friend_id: int = 0,
    character_id: int | None = None,
    character_name: str = "",
    character_profile: str = "",
    api_key: str = "",
    api_base: str = "",
):
    """创建完整的 Multi-Agent 对话图。

    参数保留是为了兼容既有调用点；节点实际从 state 读取这些值。
    """
    graph = StateGraph(MultiAgentState)

    graph.add_node("supervisor", supervisor_node)
    graph.add_node("memory", memory_agent_node)
    graph.add_node("emotion", emotion_agent_node)
    graph.add_node("conversation", conversation_agent_node)

    graph.add_edge(START, "supervisor")

    graph.add_conditional_edges("supervisor", route_from_supervisor, {
        "memory": "memory",
        "emotion": "emotion",
        "conversation": "conversation",
    })

    # 固定边：conversation 在 emotion / memory 都完成后执行（同超步扇入）。
    graph.add_edge("memory", "conversation")
    graph.add_edge("emotion", "conversation")
    graph.add_edge("conversation", END)

    return graph.compile()
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/pytest tests/test_agents.py -k "Supervisor" -q
```

Expected：PASS，17 个测试全过。

- [ ] **Step 5: 提交**

```bash
git add ai/agents/supervisor_graph.py tests/test_agents.py
git commit -m "$(cat <<'EOF'
refactor: fan out emotion and memory in parallel instead of chaining them

route_from_supervisor now returns a list of node names, so a message that
needs both runs emotion and memory in the same superstep and fans into
conversation once. Deletes STRONG_EMOTION_SIGNALS, route_after_memory and
route_after_emotion: route_after_memory's strong-emotion branch was
unreachable, because its signal list was a strict subset of the keyword
table the supervisor already checked first. The graph is now acyclic, so
memory_done / emotion_done and the previous_intent inheritance channel go
with it.

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 同步 `memory_agent.py` 与 `api/chat.py`

**Files:**
- Modify: `ai/agents/memory_agent.py:235,246,292`
- Modify: `api/chat.py:51-54,395-406`
- Test: `tests/test_memory_refactor.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_memory_refactor.py` 里，把 `test_supervisor_skips_memory_for_chat_and_time`（第 362–368 行）替换为：

```python
def test_supervisor_labels_independent_of_keywords(monkeypatch):
    """分类结果来自 LLM，不再由关键词表决定。"""
    from ai.agents import supervisor as module

    monkeypatch.setattr(module, "_classify_with_llm", lambda *a, **kw: {
        "has_emotion": False, "memory_kind": "none", "classification_source": "llm",
    })
    plain = module.supervisor_node({"messages": [HumanMessage(content="你好呀")]})
    assert plain["has_emotion"] is False
    assert plain["memory_kind"] == "none"

    monkeypatch.setattr(module, "_classify_with_llm", lambda *a, **kw: {
        "has_emotion": False, "memory_kind": "recall", "classification_source": "llm",
    })
    recall = module.supervisor_node({"messages": [HumanMessage(content="还记得第一次见面吗")]})
    assert recall["memory_kind"] == "recall"
```

把 `test_emoji_context_uses_llm_supervisor`（第 616–627 行）替换为：

```python
def test_emoji_context_uses_llm_supervisor(monkeypatch):
    from ai.agents import supervisor as module

    monkeypatch.setattr(module, "_classify_with_llm", lambda *a, **kw: {
        "has_emotion": True, "memory_kind": "none", "classification_source": "llm",
    })
    result = module.supervisor_node({
        "messages": [HumanMessage(content="🙂‍↕️")],
        "emotion_context": [{"emoji": "🙂‍↕️", "meaning": "不满、别扭"}],
    })
    assert result["has_emotion"] is True
    assert result["classification_source"] == "llm"
```

删除 `test_emotion_and_memory_each_run_at_most_once`（第 643–657 行）——它断言的两个标志已经不存在了。用下面这个替代：

```python
@pytest.mark.django_db
def test_memory_kind_drives_retrieval_strategy(monkeypatch):
    """memory_kind 的三个取值对应原来的 recall / memory / chat 检索策略。

    只改 memory_kind 一个变量，消息文本和其余 state 都相同——这样才能证明
    检索策略真的是由 memory_kind 决定的。
    """
    from django.contrib.auth.models import User
    from ai.agents import memory_agent as module
    from storage.models.character import Character
    from storage.models.friend import Friend
    from storage.models.user import UserProfile

    profile = UserProfile.objects.create(user=User.objects.create_user(username="memory-kind"))
    character = Character.objects.create(author=profile, name="女友", profile="温柔")
    friend = Friend.objects.create(me=profile, character=character)

    searched = []
    monkeypatch.setattr(module, "search_semantic", lambda *a, **kw: [])
    monkeypatch.setattr(module.Reranker, "rerank", lambda self, query, docs, top_k: docs)
    monkeypatch.setattr(
        module.ConversationHistorySearch, "search",
        lambda self, queries, **kw: searched.append(queries) or [],
    )
    monkeypatch.setattr(
        module.QueryRewriter, "plan",
        lambda self, *a, **kw: {"queries": ["今天天气不错"], "temporal_anchor": "unknown"},
    )

    base = {
        "messages": [HumanMessage(content="今天天气不错")],
        "friend_id": friend.id,
        "character_id": character.id,
        "semantic_facts": [],
    }

    module.memory_agent_node({**base, "memory_kind": "none"}, api_key="t", api_base="u")
    assert searched == [], "闲聊不该翻原文"

    module.memory_agent_node({**base, "memory_kind": "recall"}, api_key="t", api_base="u")
    assert len(searched) == 1, "recall 必须翻原文"
```

把 `test_supervisor_graph_does_not_loop_for_emotional_recall`（第 660 行起）替换为：

```python
def test_supervisor_graph_runs_emotion_and_memory_in_parallel(monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage
    from ai.agents import supervisor_graph as module

    calls = []
    monkeypatch.setattr(module, "supervisor_node", lambda state: {
        "has_emotion": True, "memory_kind": "recall", "classification_source": "llm",
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

    assert sorted(calls) == ["conversation", "emotion", "memory"]
    assert result["messages"][-1].content == "我记得"
```

最后把第 776 行的 `"intent": "recall"` 改成 `"memory_kind": "recall"`。

（改动后的测试函数自带 `from langchain_core.messages import AIMessage, HumanMessage` 局部导入，与文件既有惯例一致，不需要动文件顶部。）

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/pytest tests/test_memory_refactor.py -k "supervisor or memory_kind" -q
```

Expected：FAIL —— `test_memory_kind_drives_retrieval_strategy` 的第二个断言失败（memory_agent 还在读 `intent`，`memory_kind="recall"` 被忽略，原文搜索不触发）。`test_supervisor_labels_independent_of_keywords` 也会因缺 `has_emotion` 失败。

- [ ] **Step 3: 改 `ai/agents/memory_agent.py` 的三行**

第 235 行，`should_search_raw` 的构成：

```python
    should_search_raw = (
        state.get("memory_kind") == "recall"
        or memory_intent.get("needs_raw_chat", False)
        or memory_intent.get("needs_lightweight_recall", False)
    )
```

第 246 行：

```python
    if should_search_raw or state.get("memory_kind") == "fact":
```

第 292 行：

```python
    if state.get("memory_kind") == "fact" and not semantic_reliable:
```

- [ ] **Step 4: 改 `api/chat.py`**

第 51–54 行附近，`tts_sender` 里的 provenance 写入：

```python
        provenance["supervisor_decision"] = {
            "has_emotion": result.get("has_emotion", False),
            "memory_kind": result.get("memory_kind", "none"),
            "classification_source": result.get("classification_source", ""),
        }
```

（原来的 `provenance["supervisor_intent"] = result.get("intent", "chat")` 及其上方那句
「Persist the classified intent so the next short follow-up can inherit it」注释一并删除——
意图继承机制已移除。）

第 394–406 行，删掉整段意图继承读取，并把初始 state 的两个字段换掉：

```python
    # 意图继承已移除：每一轮都由 supervisor 重新分类，短消息由快速通道兜底。

    inputs = {
        "messages": messages,
        "has_emotion": False,
        "memory_kind": "none",
        "memory_context": "",
```

删除的行：

```python
    last_row = message_raw[-1] if message_raw else None
    previous_intent = (
        (last_row.reply_provenance or {}).get("supervisor_intent", "chat")
        if last_row
        else "chat"
    )
```

以及 `inputs` 里的 `"intent": ""`、`"delegate_to": ""`、`"previous_intent": previous_intent`。
（后面的 `"emotion_analysis": None`、`"emotion_context": emotion_context` 等保持不动。）

- [ ] **Step 5: 跑测试确认通过**

```bash
.venv/bin/pytest tests/test_memory_refactor.py tests/test_agents.py -q
```

Expected：PASS。若有 `test_memory_refactor.py` 之外的失败，先在下一步整体回归里看。

- [ ] **Step 6: 全量回归**

```bash
.venv/bin/pytest -q
```

Expected：全绿（`llm_integration` 标记的测试自动跳过）。
如果 `test_agents.py` 里还有残留的 `previous_intent` / `intent` 断言没改干净，这里会暴露出来。

再确认没有残留引用：

```bash
grep -rn "previous_intent\|supervisor_intent\|delegate_to\|memory_done\|emotion_done\|matched_signal\|emotion_intensity_hint\|INTENT_ROUTE_MAP\|EMOTIONAL_SIGNALS\|STRONG_EMOTION_SIGNALS" --include="*.py" . | grep -v "^./.venv"
```

Expected：无输出。

- [ ] **Step 7: 提交**

```bash
git add ai/agents/memory_agent.py api/chat.py tests/test_memory_refactor.py
git commit -m "$(cat <<'EOF'
refactor: read memory_kind in memory_agent and drop intent inheritance

memory_agent's three retrieval branches now key off memory_kind's recall /
fact values, preserving the exact split the old intent labels expressed.
api/chat.py persists supervisor_decision (both labels plus the source)
instead of supervisor_intent, and no longer threads a previous turn's
intent into the next one: every turn is classified on its own, with the
fast path covering contentless short messages.

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---
