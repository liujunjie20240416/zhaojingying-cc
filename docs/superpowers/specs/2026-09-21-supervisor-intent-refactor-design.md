# Supervisor 意图识别重构：从单标签关键词到多标签分类

日期：2026-09-21
状态：设计已确认，待评审

## 1. 背景

`ai/agents/supervisor.py` 目前用关键词表把用户消息压成 **5 个互斥标签之一**（chat / time / memory / recall / emotional），再映射到 3 个执行节点之一。关键词识别不出时才调用一次 LLM 兜底。

这个结构有两个结构性问题：

**问题一：意图本来就不是单选。** 「上次吵架我好难过」同时需要情绪回应和历史检索，但单标签分类器只能返回一个。现有代码用「情绪优先级最高」硬解（`supervisor.py:65-69` 把 EMOTIONAL_SIGNALS 放在最前面），代价是这条消息永远不会去检索历史。

**问题二：补丁与主表脱节，产生了永远不执行的代码。** `supervisor_graph.py:96-100` 在 memory 节点跑完后检查 `STRONG_EMOTION_SIGNALS`（17 个词），据此决定是否再去 emotion 节点。但这 17 个词**全部包含在** `supervisor.py:31-35` 的 `EMOTIONAL_SIGNALS`（19 个词）里，而后者在更早的步骤就被检查。

因此 `route_after_memory` 返回 `"emotion"` 需要同时满足：

- 消息含强情绪词（否则循环不命中）
- 消息不含任何情绪词（否则 supervisor 第一步就路由到 emotion 了，memory 根本不执行）

两个条件互斥，**这条边永远不触发**。`supervisor_graph.py:5-7` 的 docstring 描述的 `default → Memory → Emotion? → Conversation` 路径实际不存在。

同类问题：`matched_signal`、`classification_confidence`、`emotion_intensity_hint` 三个字段被写进返回值，但全项目没有任何地方读取。

## 2. 目标与非目标

**目标**

1. 意图分类从「单标签」改为「多标签」，情绪与记忆可以同时成立
2. emotion 与 memory 节点并行执行，而不是串行
3. 删除因上述两个问题而产生的死代码
4. 保留「闲聊不检索」的成本策略：无信息量的短消息不走任何 LLM

**非目标**

- 不引入轻量模型（三层的中间层）。理由见 §9
- 不改 `memory_agent` 内部的检索逻辑（三轮检索、去重、配额、重排、压缩全部保留）
- 不改 `conversation_agent`
- 不做工具调用（独立 spec）

## 3. 设计

### 3.1 分类器输出

一次 LLM 调用同时回答两个问题：

```json
{
  "has_emotion": true,
  "memory_kind": "recall",
  "confidence": 0.8
}
```

| 字段 | 取值 | 含义 |
|---|---|---|
| `has_emotion` | bool | 这条消息是否需要情绪回应 |
| `memory_kind` | `none` / `recall` / `fact` | `recall` = 问过去具体发生的事或说过的原话；`fact` = 问稳定的身份、偏好、习惯、关系；`none` = 不需要检索 |
| `confidence` | 0.0–1.0 | 分类器对自身判断的把握，仅用于观测，不参与路由 |

**`memory_kind` 为什么是三值而不是布尔**：`ai/agents/memory_agent.py` 有 3 处依赖 `state["intent"]` 区分检索策略——`L235`（recall 时强制搜原文）、`L246`（memory 时启用 QueryRewriter）、`L292`（memory 且语义记忆不可靠时回退到搜原文）。一个布尔值会丢掉这个区分，导致检索质量下降。三值是对现有行为的一对一映射。

**分类器提示词要素**：用户消息、最近 4 轮对话（`_recent_dialogue`，用于理解省略的指代）、前端 emoji 语义（`emotion_context`）。

### 3.2 快速通道

只有一条规则，命中即返回 `{has_emotion: false, memory_kind: "none", classification_source: "fast_path"}`：

```
emotion_context 为空
且 (消息精确等于 LIGHTWEIGHT_ACKS 中的一项
     或 消息长度 ≤ 8 且每个字符都是标点或空白)
```

`LIGHTWEIGHT_ACKS` 初版（保守，刻意不含「哦」「呵呵」这类可能表示冷淡的词）：

```
嗯 嗯嗯 哦哦 好 好的 好哒 好呀 行 收到 哈哈 哈哈哈 嘿嘿 早 晚安 在吗
```

第二条规则用 Unicode 类别判定（`unicodedata.category(ch)[0] in {"P", "Z"}`），**不是**「不含汉字/字母/数字」。这个区别是本次实现阶段才发现的，见下方「为什么不能用字符白名单」。

**emotion_context 非空时不走快速通道**——emoji 带情绪含义的消息必须过分类器。

#### 为什么不能用字符白名单（实现阶段修正）

初版写的是「不含任何汉字/字母/数字」，用正则 `[一-鿿A-Za-z0-9]` 判定。这条规则有洞：

- **表外 emoji 会被当成无信息量消息吞掉。** `emotion_context` 由前端 `USER_EMOJI_MEANINGS` 那张**固定的 18 项表**生成（`frontend/src/js/utils/emotionEmoji.js:20`，`detectUserEmojiContext` 只做 `text.includes(emoji)` 匹配）。发一个裸的 💔 / 😔 / 😅 / 😂，表里没有 → `emotion_context` 为空 → 正则也匹配不到任何「内容字符」→ 走快速通道 → `has_emotion: false`，用户得不到任何情绪回应。
- **全角字母数字同样中招**：`ｈｅｌｌｏ`、`１２３` 都不含 `[A-Za-z0-9]`。

这个失败**是静默的**：`classification_source` 记的是 `fast_path`——看起来最健康的那个值，于是 §3.6 的「快速通道命中率」指标会上升，而产品在变差。

Unicode 类别判定同时堵住两个洞，且不需要维护任何字符清单：

| 输入 | 类别 | 结果 |
|---|---|---|
| `。。。` `！？` | `Po`（标点） | 快速通道 ✓ |
| （空白） | `Z*` | 快速通道 ✓ |
| 💔 😭 🙂 | `So`（符号） | 过分类器 ✓ |
| `ｈ` `Ａ` | `Lu`/`Ll` | 过分类器 ✓ |
| `１` | `Nd` | 过分类器 ✓ |

副作用：ASCII 的 `~` 和全角的 `～` 是 `Sm`，所以「~~」这类消息现在会过一次分类器——多发一次调用，无害。

**不修前端那张表**：补几个 emoji 进去只能覆盖「有人记得列出来的」那些，判定规则才是兜底。表本身该不该扩充（那是给 emotion agent 提供语义，不是给路由）、要不要让前端把未知 emoji 也传上来，属于另一件事。

**原设计中的「时间词快速通道」已移除**。理由：`time_context` 本来就无条件注入 prompt（`api/chat.py:414`），「几点」并不构成一个路由决策，它和普通闲聊一样去 conversation。单独开一条快速通道没有收益，却引入误判风险（「都几点了你还不回来」含「几点」但明显带情绪）。

### 3.3 路由与图

```python
def route_from_supervisor(state) -> list[str]:
    targets = []
    if state.get("has_emotion"):
        targets.append("emotion")
    if state.get("memory_kind", "none") != "none":
        targets.append("memory")
    return targets or ["conversation"]
```

条件边返回节点名列表即触发并行扇出（`pyproject.toml` 要求 `langgraph>=1.2.0`；实现第一步先写一个最小图验证列表返回确实产生并行扇出，再动主图）。

```
START → supervisor
          ├── 快速通道 ─────────────────────────────▶ conversation
          └── 分类器
                ├── 两者都要 ──┬──▶ emotion ──┐
                ├── 只要情绪 ────▶ emotion ────┤
                ├── 只要记忆 ────▶ memory ─────┼──▶ conversation → END
                └── 都不要 ────────────────────┘
```

`memory → conversation` 与 `emotion → conversation` 都是固定边；conversation 在两个节点都完成后执行（LangGraph 同一步内的扇入语义）。

按逻辑边计（源 → 目标，同一源的多目标分别计数）：总边数从 9 条降到 7 条，其中条件边从 7 条降到 3 条，**图从有环变成无环**。

### 3.4 失败降级

分类器超时或异常时返回：

```python
{"has_emotion": False, "memory_kind": "recall", "classification_source": "fallback"}
```

`memory_kind` 取 `"recall"` 而非 `"none"`：宁可多检索一次，也不要答不上来——陪伴产品的核心承诺就是「她记得」。`recall` 是最宽的检索模式（触发原文搜索 + Query Rewriter）。

`has_emotion` 取 `False`：情绪分析本身也是一次 LLM 调用，分类器都失败时它大概率也失败，重试只是叠加延迟。

**超时从 20s 降到 5s。** 分类器从「偶发调用」变成「每条消息都调用」，20s 的最坏情况不再可接受。

**不加熔断器。** 曾考虑「连续失败 N 次后退回纯闲聊」，但该场景不成立：分类器连不上 LLM 时，conversation agent 同样连不上，整个回合本来就会失败。熔断器优化的是一个不存在的情况。

### 3.5 状态字段变化

**新增**

- `has_emotion: bool`
- `memory_kind: str`

**删除**

- `intent`（被上述两个字段取代）
- `delegate_to`（路由函数直接从上面两个字段算）
- `previous_intent`（意图继承机制整套移除）
- `memory_done`、`emotion_done`（图无环，防环标志失效）

**`reply_provenance` 变化**：`supervisor_intent` 字段改为 `supervisor_decision`，内容为 `{has_emotion, memory_kind, classification_source}`——由 `api/chat.py` 从图结果中**拼装**，不是从 state 里读一个同名的键（该键不存在，详见 §4 的 L54 条）。该字段目前只有 `api/chat.py` 内部读写，前端不使用（已确认 `frontend/src` 无引用）。

**`confidence` 不进状态**：实现阶段验证发现 LangGraph 会**静默丢弃**节点返回中不在状态 schema 里的键。`confidence` 不参与路由也不进 provenance，只通过 `record_trace("supervisor.route", …)` 落进 trace，不从图结果里读。

### 3.6 可观测性

`record_trace("supervisor.route", ...)` 记录：`has_emotion`、`memory_kind`、`confidence`、`classification_source`（`fast_path` / `llm` / `fallback`）。

上线后可统计：快速通道命中率（省下的调用比例）、`has_emotion` 与 `memory_kind` 的分布、降级率。

## 4. 改动文件清单

### `ai/agents/supervisor.py`（重写）

- **删除**：`INTENT_ROUTE_MAP`、`RECALL_SIGNALS`、`TIME_SIGNALS`、`MEMORY_SIGNALS`、`EMOTIONAL_SIGNALS`、`AMBIGUOUS_SIGNALS`、`REFERENTIAL_CUES`
- **保留**：`_recent_dialogue`
- **新增**：`LIGHTWEIGHT_ACKS`
- **重写 `supervisor_node`**：取 user_msg → emotion_context 检查 → 快速通道 → 分类器
- **重写 `_classify_with_llm`**：新提示词、新输出 schema、`timeout=5`；删除 `matched_signal` 与 `emotion_intensity_hint`

### `ai/agents/supervisor_graph.py`

- **删除**：`STRONG_EMOTION_SIGNALS`、`route_after_memory`、`route_after_emotion`、`from ai.memory.intent import detect_memory_intent`
- **重写 `route_from_supervisor`**：返回节点名列表
- **简化 `wrap_memory` / `wrap_emotion`**：不再附加完成标志，可直接使用节点函数
- **`MultiAgentState`**：增删 §3.5 所列字段
- **边**：`memory` 与 `emotion` 到 `conversation` 改为固定边

### `ai/agents/memory_agent.py`（3 行）

| 行 | 现在 | 改为 |
|---|---|---|
| L235 | `state.get("intent") == "recall"` | `state.get("memory_kind") == "recall"` |
| L246 | `state.get("intent") == "memory"` | `state.get("memory_kind") == "fact"` |
| L292 | `state.get("intent") == "memory"` | `state.get("memory_kind") == "fact"` |

### `api/chat.py`

- **L54**：`provenance["supervisor_intent"] = result.get("intent", "chat")` 改为**从图结果里拼装** `supervisor_decision`：

  ```python
  provenance["supervisor_decision"] = {
      "has_emotion": result.get("has_emotion", False),
      "memory_kind": result.get("memory_kind", "none"),
      "classification_source": result.get("classification_source", ""),
  }
  ```

  **不能写成 `result.get("supervisor_decision", {})`**——那是在读一个从不存在的键，永远是 `{}`。
  `supervisor_decision` 是 provenance 里的一个**字段名**，不是 state 里的键；它的三个内容来自
  §3.5 新增的三个 state 字段。初版 spec 在这里写错了，实现阶段才发现。
- **L396-406**：删除 `previous_intent` 的读取与注入
- **初始 state（L403-406 附近）**：`"intent": ""` / `"delegate_to": ""` → `"has_emotion": False` / `"memory_kind": "none"`

### 测试

| 文件 | 处理 |
|---|---|
| `tests/test_agents.py` | 删除 4 个意图继承 / 短消息短路测试；重写 `test_route_chat_intent`、`test_route_recall_intent` 断言新字段 |
| `tests/test_memory_refactor.py:366-368` | 重写 `delegate_to` / `intent` 断言 |
| `tests/test_memory_refactor.py:616-627` | `test_emoji_context_uses_llm_supervisor` 改为断言 `classification_source == "llm"` |
| `tests/test_memory_refactor.py:643-657` | `test_emotion_and_memory_each_run_at_most_once` 删除（标志已移除） |
| `tests/test_memory_refactor.py:660+` | `test_supervisor_graph_does_not_loop_for_emotional_recall` 改为验证并行扇出：两个节点都执行 |
| `tests/test_context_budget.py`、`tests/test_memory.py` | 不受影响（直接调用 `detect_memory_intent`，该模块保留） |

`ai/memory/intent.py` **保留**——`memory_agent.py:233` 仍在用它推导 `time_mode`、`target_subject`、`category_hint`、`needs_state_trajectory` 等检索参数。移除的只是它在 supervisor 路由中的两处用途。

## 5. 一并清理的死代码

1. `route_after_memory` 中永不命中的强情绪词循环
2. `memory → emotion` 边（随上一条一起消失）
3. 意图继承机制（`previous_intent` + `api/chat.py` 的读写）
4. `matched_signal`、`emotion_intensity_hint` 两个只写不读的字段

`classification_confidence` 不删——改造后以 `confidence` 之名保留，用于观测与将来的阈值调优。

## 6. 回归风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| **`needs_lightweight_recall` 兜底消失** | 现在 `supervisor_graph.py:88` 用关键词函数兜底「那个作业后来怎么样了」这类没有显式回忆词的消息。改造后完全依赖分类器 | 标注集中必须包含这类样本；若召回下降，在分类器提示词中补充示例 |
| **每条消息多一次 LLM 调用** | 从「偶发」变成「每条」 | 快速通道兜住高频短消息；延迟与成本用标注集量化 |
| **快速通道误判** | 「呵呵」「哦」这类词在不同语境下含义相反，初版白名单已刻意排除；但仍有残留风险 | 白名单保守起步，用标注集测量误判率后再调 |
| **表外 emoji 无情绪回应** | 前端 `USER_EMOJI_MEANINGS` 是固定 18 项，💔/😔/😂 不在表里，到不了「emotion_context 非空」那条豁免。初版判定规则用字符白名单，会把裸的 💔 当成无信息量消息吞掉（详见 §3.2） | 判定改用 Unicode 类别（`P`/`Z`），已加回归测试 |
| **并行执行的并发** | emotion 与 memory 首次在同一超步内执行 | emotion 只调 LLM 不写库；memory 只读库（`Friend` 查询、`search_semantic`）。SQLite 读并发无冲突，但需在测试中确认 |
| **`reply_provenance` 合并** | memory_agent 用 `{**state.get("reply_provenance"), ...}` 合并写回 | emotion 不写 provenance，无竞争 |

## 7. 验证方案

> **状态：本次不做，推迟到有真实使用数据之后。**
>
> 本机 `web_message` 只有 4 行，没有真实使用数据。唯一的大语料是 `chat_message` 里导入的微信历史（23,261 行，人类侧 9,282 行），但那是**人与人的对话**，与实际被分类的「人对 AI 伴侣说的一句话」是两个分布；在其上测出的准确率不迁移，反而会带来假信心。实测该语料里「事实类」样本几乎不存在（人类侧「生日」3 条、「喜欢」14 条、「关系」3 条，且均为字面意思），`memory_kind` 三值里只有 `none` / `recall` 可测。
>
> 本次改用**单元测试**验证结构正确性，见实施计划的 Task 1–3。下面的方案保留，等有真实数据时按此建 harness。

1. 从 `db.sqlite3` **只读**抽取 150 条真实消息，分层取样：
   - 含情绪词（20）
   - 明显回忆类（20）
   - 明显事实类（20）
   - 普通闲聊（30）
   - 含糊词（「算了」「没事」「你忙吧」，30）
   - 短消息 / 纯 emoji（30）
2. 人工标注每条的真实 `has_emotion` 与 `memory_kind`
3. 新旧两版分别跑，统计：
   - `has_emotion` 与 `memory_kind` 的准确率 / 精确率 / 召回率
   - 快速通道命中率
   - 降级率
   - 每条消息的分类耗时与 token 消耗
4. **标注集不入 git**（含真实私聊内容）。存放于 `media/eval/`——`media/` 已在 `.gitignore` 中。

该标注集同时是后续「工具调用」方案评测的基础。

## 8. 实施顺序

1. 写标注集抽取脚本，产出 150 条待标注样本（先于代码改动，用于建立基线）
2. 重写 `supervisor.py`（分类器 + 快速通道）
3. 改造 `supervisor_graph.py`（并行扇出、删标志与死边）
4. 同步 `memory_agent.py` 的 3 行与 `api/chat.py` 的 3 处
5. 更新测试
6. 跑新旧对比，产出数字

第 1 步先行，这样任何一步之后都能立刻量化「变好了还是变坏了」。

## 9. 为什么不做三层意图识别

三层（关键词 → 轻量模型 + confidence 阈值 → 大模型兜底）是智能客服领域的成熟模式，本项目暂不采用，三个具体原因：

1. **没有轻量模型可用**：`ai/config.py` 只配置了一个文本模型 `deepseek-v4-pro`（另有视觉模型 `glm-5v-turbo`，仅用于看图）。加中间层意味着新增一个模型配置与一个 provider，是新依赖。
2. **confidence 阈值无法校准**：项目当前没有标注集，阈值只能拍脑袋定，无法回答「为什么是 0.7」。
3. **最坏情况两次 LLM 串行**：本次改造的动机之一是减少不必要的串行等待，三层会让最坏情况更慢。

**轻量模型真正该用的地方不是 supervisor**：当前所有 LLM 调用——情绪分析、query 改写、重排、压缩、摘要——都用 `deepseek-v4-pro`。把这些小任务换成小模型比在 supervisor 前再叠一层收益更大。等标注集建成、有了可量化的准确率与成本数据之后，再决定分类器是否换成小模型、是否需要大模型兜底。

## 10. 后续

- 工具调用（模型自主选择工具）——独立 spec，在本改造完成后进行
- 分类器换轻量模型——依赖 §7 的标注集
