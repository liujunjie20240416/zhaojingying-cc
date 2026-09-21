# Supervisor 多标签分类重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 supervisor 从「单标签关键词路由」改成「一次 LLM 调用输出多标签（has_emotion + memory_kind）」，让情绪与记忆可以同时命中并**并行**执行。

**Architecture:** supervisor 节点只做一次分类，返回 `has_emotion` / `memory_kind`；条件边函数返回节点名**列表**，LangGraph 在原超步内并行扇出到 emotion / memory，两者都走固定边汇入 conversation。图从有环变无环，删除全部防环标志与永不触发的死边。

**Tech Stack:** Python 3.12、LangGraph 1.2.0、LangChain Core、OpenAI SDK（DeepSeek）、Django 6（仅 eval 脚本读库）、pytest。

**Spec:** `docs/superpowers/specs/2026-09-21-supervisor-intent-refactor-design.md`

**已验证的前置条件：** `.venv/bin/python` 下的 langgraph 1.2.0 中，条件边函数返回节点名列表会产生并行扇出，汇入节点只在两者都完成后执行一次。验证代码见 Task 0。

**关于执行顺序：** Task 1–3 是评测基线（Task 2 需要人工标注），Task 4–7 是代码改造。两者可以任意先后——只要基线是在**改造落地前**的 commit 上跑的。若先做完了 Task 4–7，用 `git stash` 回到改造前再跑 Task 3。

**已有测试基线（改造前，请勿打破）：**

```
$ .venv/bin/pytest tests/test_agents.py -k Supervisor -q
6 passed, 1 skipped, 3 deselected
```

---

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `tools/extract_eval_samples.py` | 新建 | 从 `db.sqlite3` 只读分层抽取 150 条真实消息，产出待标注 JSONL |
| `tools/eval_supervisor.py` | 新建 | 读标注文件，跑分类器，输出准确率/精确率/召回率 + 快速通道命中率 + 降级率 |
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

## Task 1: 标注集抽取脚本

**Files:**
- Create: `tools/extract_eval_samples.py`
- 产出（不入 git）: `media/eval/samples.jsonl`

- [ ] **Step 1: 写脚本**

创建 `tools/extract_eval_samples.py`：

```python
"""从 db.sqlite3 **只读**抽取分层评测样本，产出待人工标注的 JSONL。

输出到 media/eval/samples.jsonl（media/ 已在 .gitignore 中——这些是真实私聊内容，
绝不能进版本库）。
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from storage.models.friend import Message  # noqa: E402  （必须在 django.setup 之后）

OUT_PATH = Path("media/eval/samples.jsonl")

# 分层抽取的关键词表。刻意用最粗糙的字符串包含——这一步只负责「把样本捞出来」，
# 判断对错是人工标注和分类器的事。
BUCKETS = [
    ("emotional", ["难过", "伤心", "哭", "崩溃", "绝望", "害怕", "焦虑", "生气", "愤怒", "好累", "压力"], 20),
    ("recall", ["记得", "以前", "那次", "第一次", "上次", "说过", "聊过", "回忆"], 20),
    ("fact", ["我喜欢什么", "我讨厌什么", "我的生日", "我叫什么", "我们是什么关系", "我的习惯"], 20),
]

RANDOM_SEED = 20260921


def _fetch():
    """把全部 user_message 读进内存一次。只读，不写库。"""
    return list(
        Message.objects.order_by("-id")
        .values_list("id", "user_message")[:5000]
    )


def _pick(pool: list[tuple[int, str]], keywords: list[str], n: int, used: set[int]) -> list[tuple[int, str]]:
    picked = []
    for mid, text in pool:
        if mid in used or not text:
            continue
        if any(k in text for k in keywords):
            picked.append((mid, text))
            used.add(mid)
        if len(picked) >= n:
            break
    return picked


def _pick_short(pool: list[tuple[int, str]], n: int, used: set[int]) -> list[tuple[int, str]]:
    """短消息 / 纯 emoji：长度 ≤ 4 或没有任何汉字字母数字。"""
    picked = []
    for mid, text in pool:
        text = (text or "").strip()
        if mid in used or not text:
            continue
        has_content = any(c.isalnum() or "一" <= c <= "鿿" for c in text)
        if len(text) <= 4 or not has_content:
            picked.append((mid, text))
            used.add(mid)
        if len(picked) >= n:
            break
    return picked


def main() -> None:
    random.seed(RANDOM_SEED)
    pool = _fetch()
    # 打乱一次，避免「最新的消息被各桶重复挑走」导致样本全挤在同几天。
    random.shuffle(pool)
    used: set[int] = set()
    rows = []

    def add(bucket: str, items):
        for mid, text in items:
            rows.append({
                "id": mid,
                "text": text,
                # 以下三个字段是人工要填的答案，先留空
                "label_has_emotion": None,
                "label_memory_kind": None,
                "note": "",
                "_bucket": bucket,
            })

    for bucket, keywords, n in BUCKETS:
        add(bucket, _pick(pool, keywords, n, used))

    # 普通闲聊：不属于以上任何一桶的，长度 ≥ 5
    chat_pool = [
        (mid, text) for mid, text in pool
        if mid not in used and text and len(text.strip()) >= 5
        and not any(k in text for _, kws, _ in BUCKETS for k in kws)
    ]
    add("chat", chat_pool[:30])
    used.update(mid for mid, _ in chat_pool[:30])

    # 含糊词
    add("ambiguous", _pick(pool, ["算了", "没事", "呵呵", "随便", "你忙吧", "当我没说", "一言难尽"], 30, used))

    # 短消息 / 纯 emoji
    add("short", _pick_short(pool, 30, used))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["_bucket"]] = counts.get(row["_bucket"], 0) + 1
    print(f"写出 {len(rows)} 条 -> {OUT_PATH}")
    print("分桶：", counts)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑脚本**

```bash
.venv/bin/python tools/extract_eval_samples.py
```

Expected：`写出 150 条 -> media/eval/samples.jsonl`，分桶接近
`{'emotional': 20, 'recall': 20, 'fact': 20, 'chat': 30, 'ambiguous': 30, 'short': 30}`。
若某些桶不足（老库里可能没有 20 条「事实类」），**就接受实际数量**，把总数记下来，不要改脚本造假样本——评测的分母是真实样本数。

- [ ] **Step 3: 确认产出不进 git**

```bash
git status --short media/
```

Expected：**无输出**（`media/` 已被 `.gitignore:31` 覆盖）。若有输出，停下来先修 `.gitignore`。

- [ ] **Step 4: 提交脚本（不含样本）**

```bash
git add tools/extract_eval_samples.py
git commit -m "$(cat <<'EOF'
feat: add stratified eval sample extractor for supervisor

Reads db.sqlite3 read-only and writes media/eval/samples.jsonl for human
labelling. The sample file stays out of git: it is real private chat.

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 【人工】标注 150 条样本

**Files:**
- Modify: `media/eval/samples.jsonl`

> 这一步是**人工**的，agent 不能代替。做完 Task 1 后交给用户。

- [ ] **Step 1: 说明标注口径**

每条要填两个字段：

| 字段 | 取值 | 判断依据 |
|---|---|---|
| `label_has_emotion` | `true` / `false` | 用户在表达情绪、需要安慰或共情吗？「我好累」= true；「今天几号」= false |
| `label_memory_kind` | `"none"` / `"recall"` / `"fact"` | `recall` = 在问**过去具体发生过的事或说过的原话**；`fact` = 在问**稳定的身份、偏好、习惯、关系**；`none` = 不需要翻记忆 |

**两个字段互相独立**——「上次吵架我好难过」是 `has_emotion=true` + `memory_kind="recall"`，这正是本次改造要支持的组合。标注时**不要**因为「有情绪」就把 `memory_kind` 填成 `none`。

**只看这一条消息本身**，不要脑补上下文。「她」这种指代不明的，按字面能判就判，判不了填 `null` 并在 `note` 写「歧义」——评测脚本会跳过 `null`。

- [ ] **Step 2: 标注**

用户用编辑器直接改 `media/eval/samples.jsonl`。每行形如：

```json
{"id": 1234, "text": "上次吵架我好难过", "label_has_emotion": true, "label_memory_kind": "recall", "note": "", "_bucket": "emotional"}
```

- [ ] **Step 3: 检查标注完整性**

```bash
.venv/bin/python - <<'PY'
import json, pathlib
rows = [json.loads(l) for l in pathlib.Path("media/eval/samples.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
blank = [r["id"] for r in rows if r["label_has_emotion"] is None or r["label_memory_kind"] is None]
print(f"总 {len(rows)} 条，未标注 {len(blank)} 条")
if blank: print("未标注 id:", blank[:20])
PY
```

Expected：`未标注 0 条`。有剩的就回去补。

---

## Task 3: 采集基线（改造前的数字）

**Files:**
- Create: `tools/eval_supervisor.py`
- 产出（不入 git）: `media/eval/baseline.jsonl`、`media/eval/baseline-report.txt`

- [ ] **Step 1: 确认仍在改造前**

```bash
git log --oneline -1
git status --short
```

Expected：HEAD 是 Task 1 的提交（或更早），工作区干净。
**如果 Task 4–7 已经做完了**，先 `git stash` 并 `git log` 确认回到改造前的 commit；本 Task 结束时再 `git stash pop`。

- [ ] **Step 2: 写评测脚本**

创建 `tools/eval_supervisor.py`：

```python
"""跑一遍 supervisor 分类器，对 media/eval/samples.jsonl 输出准确率报告。

用法：
    .venv/bin/python tools/eval_supervisor.py --out baseline

只读标注文件，不写 db。报告写到 media/eval/<out>-report.txt（不入 git）。
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from langchain_core.messages import HumanMessage  # noqa: E402

from ai.agents import supervisor as sup  # noqa: E402

SAMPLES = Path("media/eval/samples.jsonl")
EVAL_DIR = Path("media/eval")

MEMORY_KINDS = ["none", "recall", "fact"]


def _normalize(result: dict) -> dict:
    """旧版 supervisor 只返回单标签 intent；新版直接返回两个标签。两者都能评测。"""
    if "has_emotion" in result or "memory_kind" in result:
        return result
    intent = result.get("intent", "chat")
    return {
        "has_emotion": intent == "emotional",
        "memory_kind": {"recall": "recall", "memory": "fact"}.get(intent, "none"),
        "classification_source": result.get("classification_source", "keyword"),
    }


def _predict(text: str) -> tuple[dict, float]:
    started = time.perf_counter()
    result = _normalize(sup.supervisor_node({"messages": [HumanMessage(content=text)]}))
    return result, time.perf_counter() - started


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="run", help="报告文件名前缀")
    args = parser.parse_args()

    rows = [
        json.loads(line)
        for line in SAMPLES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows = [r for r in rows if r.get("label_has_emotion") is not None and r.get("label_memory_kind")]

    predictions = []
    for row in rows:
        result, elapsed = _predict(row["text"])
        predictions.append({
            "id": row["id"],
            "text": row["text"],
            "_bucket": row.get("_bucket", ""),
            "gold_has_emotion": row["label_has_emotion"],
            "gold_memory_kind": row["label_memory_kind"],
            "pred_has_emotion": result.get("has_emotion", False),
            "pred_memory_kind": result.get("memory_kind", "none"),
            "source": result.get("classification_source", ""),
            "seconds": round(elapsed, 3),
        })

    out_jsonl = EVAL_DIR / f"{args.out}.jsonl"
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for item in predictions:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    n = len(predictions)
    lines = [f"样本数 {n}", f"输出 {out_jsonl}", ""]

    # has_emotion 二分类
    tp = sum(1 for p in predictions if p["gold_has_emotion"] and p["pred_has_emotion"])
    fp = sum(1 for p in predictions if not p["gold_has_emotion"] and p["pred_has_emotion"])
    fn = sum(1 for p in predictions if p["gold_has_emotion"] and not p["pred_has_emotion"])
    tn = n - tp - fp - fn
    acc = (tp + tn) / n if n else 0.0
    precision, recall, f1 = _prf(tp, fp, fn)
    lines += [
        f"has_emotion  准确率 {acc:.3f}  (tp={tp} fp={fp} fn={fn} tn={tn})",
        f"has_emotion  精确率 {precision:.3f}  召回率 {recall:.3f}  F1 {f1:.3f}",
        "",
    ]

    # memory_kind 三分类 macro-F1
    kind_lines = []
    for kind in MEMORY_KINDS:
        ktp = sum(1 for p in predictions if p["gold_memory_kind"] == kind and p["pred_memory_kind"] == kind)
        kfp = sum(1 for p in predictions if p["gold_memory_kind"] != kind and p["pred_memory_kind"] == kind)
        kfn = sum(1 for p in predictions if p["gold_memory_kind"] == kind and p["pred_memory_kind"] != kind)
        kp, kr, kf = _prf(ktp, kfp, kfn)
        kind_lines.append(f"memory_kind={kind:<6} 精确率 {kp:.3f}  召回率 {kr:.3f}  F1 {kf:.3f}  (支持度 {ktp + kfn})")
    exact = sum(1 for p in predictions if p["gold_memory_kind"] == p["pred_memory_kind"]) / n if n else 0.0
    lines += [f"memory_kind  完全准确率 {exact:.3f}", *kind_lines, ""]

    # 来源分布 + 耗时
    for source in ["fast_path", "llm", "fallback", ""]:
        count = sum(1 for p in predictions if p["source"] == source or (source == "" and not p["source"]))
        lines.append(f"classification_source={source or '<空>':<10} {count} 条  ({count / n:.1%})" if n else "")
    total_seconds = sum(p["seconds"] for p in predictions)
    llm_count = sum(1 for p in predictions if p["source"] in {"llm", "fallback"})
    lines += [
        f"总耗时 {total_seconds:.1f}s",
        f"分类器调用 {llm_count} 次，平均 {total_seconds / llm_count:.2f}s/次" if llm_count else "分类器调用 0 次",
        "",
        "分桶明细（gold → pred 不一致的）",
    ]
    for p in predictions:
        if p["gold_has_emotion"] != p["pred_has_emotion"] or p["gold_memory_kind"] != p["pred_memory_kind"]:
            lines.append(
                f"  [{p['_bucket']}] {p['text'][:40]!r} "
                f"gold=({p['gold_has_emotion']},{p['gold_memory_kind']}) "
                f"pred=({p['pred_has_emotion']},{p['pred_memory_kind']}) src={p['source']}"
            )

    report = "\n".join(lines) + "\n"
    (EVAL_DIR / f"{args.out}-report.txt").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 跑基线**

```bash
.venv/bin/python tools/eval_supervisor.py --out baseline
```

Expected：打印一份报告；`media/eval/baseline-report.txt` 落盘。

脚本里的 `_normalize` 是给基线用的：旧代码返回的是单标签 `intent`，`has_emotion` / `memory_kind` 都是从它派生（`recall`→`recall`、`memory`→`fact`、其余→`none`）。改造后 `supervisor_node` 直接返回这两个字段，`_normalize` 原样放行——所以同一份脚本能跑两版，数字才可比。

- [ ] **Step 4: 确认产出不进 git**

```bash
git status --short media/
```

Expected：无输出。

- [ ] **Step 5: 提交脚本**

```bash
git add tools/eval_supervisor.py
git commit -m "$(cat <<'EOF'
feat: add supervisor classifier evaluation harness

Scores has_emotion and memory_kind against media/eval/samples.jsonl and
normalizes the old single-label intent output so a pre-refactor baseline
and the new multi-label classifier are directly comparable.

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

如果 Step 1 时 stash 过，现在 `git stash pop` 把改造放回来。

---

## Task 4: 重写 `ai/agents/supervisor.py`

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

    def test_emoji_context_forces_classifier(self, monkeypatch):
        """带 emoji 语义的消息必须过分类器——快速通道看不见 emoji。"""
        from ai.agents import supervisor

        called = {}
        monkeypatch.setattr(supervisor, "_classify_with_llm", lambda *a, **kw: (
            called.setdefault("yes", True) or
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
import re

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

# 判断「消息是否只有符号」用：含汉字/字母/数字就算有内容。
_CONTENT_CHAR = re.compile(r"[一-鿿A-Za-z0-9]")

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

    AIMessage 带 tool_calls 属性（即使为空），据此跳过；HumanMessage 没有。
    """
    for msg in reversed(state.get("messages", []) or []):
        if hasattr(msg, "content") and not hasattr(msg, "tool_calls"):
            return getattr(msg, "content", "") or ""
        if isinstance(msg, dict) and msg.get("role") == "user":
            return msg.get("content", "") or ""
    return ""


def _is_lightweight_ack(user_msg: str) -> bool:
    text = user_msg.strip()
    if text in LIGHTWEIGHT_ACKS:
        return True
    return len(text) <= 8 and not _CONTENT_CHAR.search(text)


def supervisor_node(state: dict, api_key: str = "", api_base: str = "") -> dict:
    """把这一轮判成 has_emotion + memory_kind，两个标签互相独立。"""
    user_msg = _last_user_msg(state)

    if not user_msg:
        result = _no_op_decision("empty")
        record_trace("supervisor.route", {"user_msg": user_msg}, result)
        return result

    emotion_context = state.get("emotion_context") or []

    # 快速通道只处理「确定没有信息量」的消息。emoji 语义是快速通道看不见的
    # 情绪信号，所以带 emotion_context 的消息一律过分类器。
    if not emotion_context and _is_lightweight_ack(user_msg):
        result = _no_op_decision("fast_path")
        record_trace("supervisor.route", {"user_msg": user_msg}, result)
        return result

    recent_dialogue = _recent_dialogue(state.get("messages", []))
    result = _classify_with_llm(user_msg, emotion_context, api_key, api_base, recent_dialogue)
    record_trace(
        "supervisor.route",
        {"user_msg": user_msg, "emotion_context": emotion_context},
        result,
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
        prompt = f"""判断这条伴侣聊天消息需要哪些处理，只输出 JSON。
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
        response = client.chat.completions.create(
            model=llm_model(),
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=120,
            response_format={"type": "json_object"},
        )
        parsed = json.loads((response.choices[0].message.content or "{}").strip())
        memory_kind = parsed.get("memory_kind", "none")
        if memory_kind not in _VALID_MEMORY_KINDS:
            memory_kind = "none"
        return {
            "has_emotion": bool(parsed.get("has_emotion", False)),
            "memory_kind": memory_kind,
            "confidence": float(parsed.get("confidence", 0) or 0),
            "classification_source": "llm",
        }
    except Exception:
        # 分类失败不能让用户失去一次记忆：宁可按最宽的检索模式跑一次。
        # has_emotion 取 False——情绪分析本身也是一次 LLM 调用，分类器都连不上，
        # 它大概率也连不上，重试只是叠加延迟。
        return {
            "has_emotion": False,
            "memory_kind": "recall",
            "confidence": 0.0,
            "classification_source": "fallback",
        }
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/pytest tests/test_agents.py -k "Supervisor" -q
```

Expected：PASS，12 个测试（FastPath 4 + Classifier 3 + Fallback 3）全过。
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

## Task 5: 改造 `ai/agents/supervisor_graph.py` 为并行扇出

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

## Task 6: 同步 `memory_agent.py` 与 `api/chat.py`

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

## Task 7: 新旧对比

**Files:**
- 产出（不入 git）: `media/eval/after-report.txt`

- [ ] **Step 1: 跑改造后的评测**

```bash
.venv/bin/python tools/eval_supervisor.py --out after
```

Expected：打印报告；`media/eval/after-report.txt` 落盘。
**注意**：这一步会真的调用 150 次 DeepSeek API（需要 `DEEPSEEK_API_KEY`），大约 3–8 分钟。

- [ ] **Step 2: 并排看两份报告**

```bash
diff -y --width=170 media/eval/baseline-report.txt media/eval/after-report.txt | head -40
```

- [ ] **Step 3: 记录结论**

把三行数字抄进 spec 的 §7 验证方案末尾（准确率、快速通道命中率、平均耗时），并说明是否达标：

| 指标 | 基线 | 改造后 | 是否可接受 |
|---|---|---|---|
| `has_emotion` 召回率 | | | 目标 ≥ 0.85——漏掉情绪的代价是用户觉得「她不懂我」 |
| `has_emotion` 精确率 | | | 目标 ≥ 0.75——多跑一次情绪分析只是多花钱 |
| `memory_kind` 完全准确率 | | | 目标 ≥ 0.80 |
| `fast_path` 占比 | — | | 目标 ≥ 15%——低于这个数说明白名单太保守 |
| `fallback` 占比 | — | | 目标 ≤ 1%——高于这个数说明 5s 超时太紧 |
| 每条平均分类耗时 | | | 记下来，用于决定是否换小模型 |

- [ ] **Step 4: 提交结论**

```bash
git add docs/superpowers/specs/2026-09-21-supervisor-intent-refactor-design.md
git commit -m "$(cat <<'EOF'
docs: record supervisor refactor eval numbers against the labelled set

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

## 附录：如果评测结果不达标

按 spec §6 的风险表对症下药，**不要**退回到关键词表：

| 症状 | 处理 |
|---|---|
| `recall` 漏检（「那个作业后来怎么样了」） | 在分类器提示词里补这类例子，不改结构 |
| `fast_path` 占比过低 | 往 `LIGHTWEIGHT_ACKS` 加词——**先看标注里哪些被误判**再决定加什么 |
| `fallback` 占比过高 | 把 `CLASSIFIER_TIMEOUT` 从 5 调到 8；同时看是不是网络问题 |
| `has_emotion` 精确率过低 | 提示词里明确「吐槽天气、陈述事实不算情绪」 |
| 分类耗时太高 | 这才是考虑换轻量模型的时机——现在有数据支撑了 |
