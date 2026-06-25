# Multi-Agent + 增强 RAG + 分层记忆 — 设计文档

> 日期: 2026-06-21 | 状态: Draft | 作者: Junjie Liu

## 1. 概述

在现有 LangGraph 单 Agent 对话系统基础上，分三个相互关联的模块升级：

| 模块 | 目标 | 核心技术 |
|------|------|---------|
| **Phase 1: 增强 RAG** | 让记忆检索更准更全 | Query Rewriting, HyDE, Cross-Encoder Reranking, Context Compression |
| **Phase 2: 分层记忆** | 从单字段覆盖式记忆升级为分层有反思的记忆系统 | Episodic Memory, Semantic Memory, Working Memory, Memory Reflection, Ebbinghaus Decay |
| **Phase 4: Multi-Agent** | 从单 Agent 升级为多 Agent 协作 | Supervisor 路由, Memory Agent, Emotion Agent, Conversation Agent 子图 |

Phase 3 (Plan-and-Execute) 和 Phase 5 (DeepAgents 迁移) 保留规划但不在本文档范围内。

## 2. 当前架构 (As-Is)

```
用户输入 → ChatGraph (单 Agent)
              ├── Tool: get_time
              ├── Tool: search_knowledge_base (LanceDB)
              └── Tool: search_wechat (FTS5 + LanceDB)
              
记忆: MemoryGraph (LLM 总结) → Friend.memory (Text 5000, 覆盖式更新)
语音: DashScope ASR + TTS (直接调用, 在 api/chat.py 中)
```

**关键文件:**
- `ai/chat_graph.py` — LangGraph agent, 3 tools, tool-calling 循环
- `ai/memory_graph.py` — 简单 LLM 调用做记忆总结
- `ai/memory_update.py` — 更新 `Friend.memory` 字段
- `ai/custom_embeddings.py` — 自定义 embedding 封装 (text-embedding-v4)
- `api/chat.py` — SSE 流式聊天 endpoint, 包含 pre-search + TTS 集成

**痛点:**
1. Agent 无规划能力，简单 tool-calling 循环
2. 记忆是单字段文本覆盖式更新，无分层、无反思、无衰减
3. 检索无 query 优化、无重排序
4. 单 Agent 处理所有任务，没有专业化分工
5. 无可观测性

## 3. 目标架构 (To-Be)

```
                         ┌──────────────────────────────┐
                         │     Supervisor Agent          │
                         │  轻量路由: intent → delegate  │
                         └──┬────────┬────────┬─────────┘
                   ┌────────▼──┐ ┌───▼────┐ ┌─▼──────────┐
                   │Memory     │ │Emotion │ │Conversation │
                   │Agent      │ │Agent   │ │Agent        │
                   │(检索+组织) │ │(情绪)  │ │(生成回复)    │
                   └──┬────────┘ └───┬────┘ └─┬──────────┘
                      │              │         │
              ┌───────▼──────────────▼─────────▼───────┐
              │          增强 RAG 检索层                 │
              │  Rewrite → HyDE → Search → Rerank →    │
              │  ContextExpand → Compress              │
              └──────────────┬─────────────────────────┘
                             │
              ┌──────────────▼─────────────────────────┐
              │           分层记忆系统                    │
              │  Working (Context)                      │
              │  Episodic (LanceDB + SQLite)            │
              │  Semantic (JSON + LanceDB)              │
              │  + Reflection (定时提炼)                 │
              └────────────────────────────────────────┘
```

## 4. 模块一：Multi-Agent 架构

### 4.1 Agent 定义

**Supervisor Agent (路由器)**
- 输入: 用户消息 + 角色信息
- 输出: `{"intent": "chat|recall|emotional", "delegate_to": "conversation|memory|emotion"}`
- 实现: 单次 LLM 调用 + 结构化输出 (JSON mode)，不做多步规划
- 轻量：~200 tokens 输出

**Memory Agent (记忆检索)**
- 职责: 调用增强 RAG 管道，从 Episodic + Semantic Memory 检索并组织上下文
- 输入: 用户消息 + 改写后的 queries + 角色 ID
- 输出: 结构化的记忆上下文 (供 Conversation Agent 使用)
- 工具: RAG 管道中的各个步骤作为内部函数调用

**Emotion Agent (情绪分析)**
- 职责: 分析对话情绪，建议回复语调和策略
- 输入: 用户消息 + 最近 5 轮对话
- 输出: `{"emotion": "sad|happy|angry|neutral|anxious|...", "intensity": 0-10, "suggested_tone": "gentle|cheerful|calm|...", "should_comfort": true|false}`
- 激活条件: Supervisor 判定 intent=emotional，或用户消息包含明显情绪信号词
- 闲聊场景跳过，节省 token

**Conversation Agent (对话生成)**
- 职责: 使用角色设定 + 记忆上下文 + 情绪分析 → 生成流式回复
- 始终执行（所有意图最终都要回复）
- 不调用外部工具，纯文本生成

### 4.2 LangGraph 编排

```
START → Supervisor → [路由]
    ├── intent="chat"       → Emotion? → Conversation → END
    ├── intent="recall"     → Memory → Emotion? → Conversation → END
    └── intent="emotional"  → Emotion → Memory → Conversation → END
```

- Memory Agent 和 Emotion Agent 是**可选节点**，通过 conditional edge 控制
- Conversation Agent 是**必经节点**
- `Emotion?` 判断条件: intent=emotional 或用户消息含明显情绪词 → 执行 Emotion Agent；否则跳过

### 4.3 实现方案

每个 Agent 实现为独立子图 (`CompiledGraph`)：

```python
# ai/agents/supervisor_graph.py
from langgraph.graph import StateGraph, START, END
from ai.agents.memory_agent import memory_agent
from ai.agents.emotion_agent import emotion_agent
from ai.agents.conversation_agent import conversation_agent

graph = StateGraph(MultiAgentState)
graph.add_node("supervisor", supervisor_node)
graph.add_node("memory", memory_agent.compile())
graph.add_node("emotion", emotion_agent.compile())
graph.add_node("conversation", conversation_agent.compile())

graph.add_edge(START, "supervisor")
graph.add_conditional_edges("supervisor", route_intent, {
    "memory": "memory",
    "emotion": "emotion",
    "conversation": "conversation",
})
graph.add_conditional_edges("memory", check_emotion, {
    "emotion": "emotion",
    "conversation": "conversation",
})
graph.add_conditional_edges("emotion", check_memory, {
    "memory": "memory",
    "conversation": "conversation",
})
graph.add_edge("conversation", END)
```

### 4.4 Multi-Agent State

```python
class MultiAgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    intent: str                          # chat | recall | emotional
    memory_context: str                  # Memory Agent 检索结果
    emotion_analysis: dict | None        # Emotion Agent 输出
    character_profile: str               # 角色性格描述
    semantic_facts: list[str]            # Semantic Memory 提取的事实
```

### 4.5 文件结构

```
ai/
├── agents/
│   ├── __init__.py
│   ├── supervisor_graph.py    # 主编排图
│   ├── supervisor.py          # Supervisor 路由节点
│   ├── memory_agent.py        # Memory Agent 子图
│   ├── emotion_agent.py       # Emotion Agent 子图
│   └── conversation_agent.py  # Conversation Agent 子图
├── rag/                        # Phase 1: 增强 RAG
│   ├── __init__.py
│   ├── query_rewriter.py      # Query 多角度改写
│   ├── hyde.py                # HyDE 假设文档生成
│   ├── retriever.py           # FTS5 + LanceDB 并行检索
│   ├── reranker.py            # Cross-Encoder 重排序
│   └── compressor.py          # 上下文压缩
├── memory/                     # Phase 2: 分层记忆
│   ├── __init__.py
│   ├── episodic.py            # Episodic Memory 读写
│   ├── semantic.py            # Semantic Memory 读写
│   ├── decay.py               # 记忆衰减计算
│   └── reflection.py          # 记忆反思提炼
├── chat_graph.py               # [保留兼容] 重定向到 agents/
├── memory_graph.py             # [保留兼容]
├── memory_update.py            # [保留兼容]
└── custom_embeddings.py        # [保留]
```

## 5. 模块二：增强 RAG 检索管道

### 5.1 检索流程

```
用户Query
    │
    ▼
[1] Query 多角度改写 (LLM)
    原始 query → 2-3 个变体
    "上次那事" → ["最近一次见面", "上次聊天的内容", "最后一次对话"]
    │
    ▼
[2] HyDE 假设文档 (LLM)
    query → 假设性回答
    "我喜欢吃什么" → "根据聊天记录，用户喜欢吃火锅、麻辣烫..."
    用假设文档做向量检索（解决 query-document 语义 gap）
    │
    ▼
[3] 并行混合检索 (同步)
    ├── FTS5 关键词搜索 (原始 query 的分词关键词)
    │     优先精确匹配
    ├── LanceDB 语义搜索 (HyDE 假设文档 + 改写 queries)
    │     每个 query 向量检索 top-10
    └── 合并去重 → 候选集 top-20
    │
    ▼
[4] Cross-Encoder Re-ranking (bge-reranker-v2-m3)
    对 top-20 逐对打分 → 重排序 → 取 top-3 ~ top-5
    │
    ▼
[5] 上下文窗口展开
    根据匹配消息的 msg_index，拉取前后各 5 条 → 对话片段
    复用现有 _build_context_snippets() 逻辑
    │
    ▼
[6] 上下文压缩 (LLM, 可选)
    如上下文 > 1000 字 → LLM 压缩提炼关键信息
    避免 token 浪费
```

### 5.2 实现细节

**Query Rewriter** (`ai/rag/query_rewriter.py`):
- 单次 LLM 调用，prompt 要求生成 2-3 个不同角度的查询
- 用于口语化和模糊查询的扩展
- 非独立工具，是 retriever 的前置步骤

**HyDE** (`ai/rag/hyde.py`):
- 仅对语义搜索路径使用（FTS5 关键词不需要）
- LLM 生成 100-200 字的假设文档
- 假设文档做 embedding → LanceDB 向量检索

**Retriever** (`ai/rag/retriever.py`):
- 封装 FTS5 (关键词) + LanceDB (语义) 并行检索
- 支持多个查询向量并行搜索后结果合并
- `asyncio.gather` 并行执行以降低延迟

**Reranker** (`ai/rag/reranker.py`):
- 使用 `BAAI/bge-reranker-v2-m3` 本地模型 (Free, 中文友好)
- 备选: Cohere Rerank API (如果已有 API key)
- 输入: (query, doc) 对列表
- 输出: 重排序后的文档 (保留 top-3)

**Compressor** (`ai/rag/compressor.py`):
- 触发条件: context_length > 1000 chars
- LLM 提炼，输出 300-500 字的精炼摘要
- 强调不丢失事实细节

### 5.3 新增依赖

```toml
# pyproject.toml
"FlagEmbedding>=1.3.0",   # bge-reranker-v2-m3
"torch>=2.0.0",            # FlagEmbedding 依赖 (如果没有)
```

如果使用 Cohere Rerank API 则可免去 FlagEmbedding + torch。

## 6. 模块三：分层记忆系统

### 6.1 三层记忆模型

| 层级 | 内容 | 生命周期 | 存储 |
|------|------|---------|------|
| Working Memory | 当前会话最近 N 轮对话 + 检索到的记忆上下文 | 单次会话 | LangGraph State (内存) |
| Episodic Memory | 具体对话事件: `{summary, keywords, importance, raw_msg}` | 永久，有衰减 | SQLite + LanceDB |
| Semantic Memory | 提炼的长期事实: `{fact, category, confidence, evidence}` | 长期，冲突时更新 | SQLite (JSON) + LanceDB |

### 6.2 新增数据模型

```python
# web/models/memory.py

class EpisodicMemory(models.Model):
    """情景记忆 — 每轮对话抽象为一个事件"""
    friend = models.ForeignKey(Friend, on_delete=models.CASCADE)
    summary = models.CharField(max_length=200)         # "用户说喜欢吃火锅"
    keywords = models.CharField(max_length=200)        # 分词关键词，空格分隔
    importance = models.FloatField(default=0.5)        # 重要性 0-1
    raw_messages = models.TextField()                  # 原始对话 JSON
    msg_count = models.IntegerField(default=1)         # 涉及轮数
    created_at = models.DateTimeField(default=now)

    class Meta:
        indexes = [
            models.Index(fields=["friend", "-created_at"]),
        ]


class SemanticMemory(models.Model):
    """语义记忆 — 提炼的长期事实和偏好"""
    friend = models.ForeignKey(Friend, on_delete=models.CASCADE)
    fact = models.CharField(max_length=500)            # "用户喜欢吃麻辣火锅"
    category = models.CharField(max_length=50)         # preference|experience|personality|relationship
    confidence = models.FloatField(default=0.5)        # 置信度 0-1
    evidence = models.TextField()                      # 支撑事实的原始对话摘要
    is_active = models.BooleanField(default=True)      # 是否仍有效
    replaced_by = models.ForeignKey(
        'self', null=True, blank=True,
        on_delete=models.SET_NULL
    )                                                  # 被新事实替代
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(default=now)

    class Meta:
        indexes = [
            models.Index(fields=["friend", "is_active", "category"]),
            models.Index(fields=["friend", "-confidence"]),
        ]
```

**Friend 模型改动:**
```python
# 在现有 Friend 模型新增
last_reflection_time = models.DateTimeField(default=now)
```

### 6.3 记忆生命周期

#### 6.3.1 写入 (对话结束时)

每轮对话 `event_stream` 保存 Message 后触发：

```python
# ai/memory/episodic.py
def write_episodic(friend, user_msg, ai_response):
    # LLM 提取: summary + keywords + importance
    extract_prompt = f"""分析对话，输出 JSON:
    - summary: 一句话摘要 (≤50字)
    - keywords: 3-5个关键词 (空格分隔)
    - importance: 0-1
      * 0.8+: 个人信息/偏好/承诺/重要事件
      * 0.5: 日常聊天
      * 0.2: 问候/客套"""

    result = llm_extract(extract_prompt)

    ep = EpisodicMemory.objects.create(
        friend=friend,
        summary=result["summary"],
        keywords=result["keywords"],
        importance=result["importance"],
        raw_messages=json.dumps([{"user": user_msg, "ai": ai_response}]),
    )

    # 同步向量化到 LanceDB (表: episodic_{friend_id})
    lancedb_add(ep.summary, friend_id=friend.id)
```

#### 6.3.2 衰减

```python
# ai/memory/decay.py
def get_decayed_importance(memory: EpisodicMemory) -> float:
    age_hours = (now() - memory.created_at).total_seconds() / 3600

    if age_hours < 1:
        decay = 1.0
    elif age_hours < 24:
        decay = 0.8 - 0.3 * (age_hours / 24)          # 1天内: 1.0 → 0.5
    elif age_hours < 168:                               # 7天
        decay = 0.5 - 0.3 * ((age_hours - 24) / 144)   # 7天内: 0.5 → 0.2
    else:
        decay = 0.2 - 0.15 * min((age_hours - 168)/720, 1)  # → 0.05

    # 高重要性记忆衰减更慢
    return memory.importance * (decay + memory.importance * 0.3)
```

#### 6.3.3 反思提炼

```python
# ai/memory/reflection.py
def reflect_memories(friend, force=False):
    """从 Episodic 提炼 Semantic"""
    recent = EpisodicMemory.objects.filter(
        friend=friend,
        created_at__gt=friend.last_reflection_time
    )

    if recent.count() < 10 and not force:
        return

    # 收集已有事实
    existing = list(SemanticMemory.objects.filter(
        friend=friend, is_active=True
    ).values_list("fact", flat=True))

    # LLM 批量分析 → 提取新事实 + 冲突检测
    prompt = f"""分析以下对话摘要。已有事实: {existing}

    对每条新发现输出:
    - fact: 一句话事实
    - category: preference|experience|personality|relationship
    - confidence: 0-1
    - conflicts_with: 如有冲突的已有事实 (否则 null)
    """

    results = llm_extract(prompt)

    for r in results:
        if r.get("conflicts_with"):
            # 冲突: 标记旧事实为 inactive
            SemanticMemory.objects.filter(
                friend=friend, fact=r["conflicts_with"]
            ).update(is_active=False)
        SemanticMemory.objects.create(...)

    friend.last_reflection_time = now()
    friend.save(update_fields=["last_reflection_time"])
```

#### 6.3.4 检索 (对话时)

```python
# ai/memory/ 中供 Memory Agent 调用的统一检索

def retrieve_all(friend, query) -> dict:
    return {
        # Semantic: 最优先，精炼事实
        "semantic": [
            s.fact for s in SemanticMemory.objects.filter(
                friend=friend, is_active=True
            ).order_by("-confidence")[:10]
        ],
        # Episodic: 通过增强 RAG 管道检索
        "episodic": enhanced_rag_search(friend, query),
        # Working: 已在 LangGraph State 中
    }
```

### 6.4 触发时机总结

```
每轮对话完成 (event_stream 保存 Message 后):
  ├── 1. 写入 EpisodicMemory (异步非阻塞)
  └── 2. 检查: 新 Episodic >= 10 条 AND 距上次 > 1h?
         └── 是 → 异步触发 Reflection

Reflection (ai/memory/reflection.py):
  ├── LLM 批量分析近期 Episodic
  ├── 提取新 Semantic
  ├── 冲突检测 → 旧事实 is_active=False
  └── 更新 friend.last_reflection_time
```

## 7. 完整对话流程

```
用户输入 "你还记得我喜欢什么吗"
    │
    ▼
Supervisor Agent: intent="recall" → delegate memory
    │
    ▼
Memory Agent:
  1. Query Rewrite → ["用户的喜好", "用户爱吃的", "用户兴趣爱好"]
  2. HyDE 生成    → "根据聊天记录，用户喜欢..."
  3. 并行搜索     → FTS5 + LanceDB (Episodic + Semantic)
  4. Re-rank      → top-3 最相关
  5. 上下文展开   → 对话片段
  6. 压缩         → 精炼上下文
    │
    ▼
Emotion Agent: (intent=recall, 无强烈情绪 → 跳过)
    │
    ▼
Conversation Agent: "当然记得！你最喜欢吃麻辣火锅了..."
    │ (流式输出到 SSE + TTS)
    ▼
保存 Message → 写入 Episodic → 检查 Reflection
```

## 8. 数据库迁移

### 8.1 新增表
- `episodic_memory` — EpisodicMemory 模型
- `semantic_memory` — SemanticMemory 模型

### 8.2 现有表改动
- `friend` 表: 新增 `last_reflection_time` 字段 (DATETIME, default=now)

### 8.3 LanceDB 新增表
- `episodic_{friend_id}` — Episodic Memory 向量索引 (基于 summary)
- `semantic_{friend_id}` — Semantic Memory 向量索引 (基于 fact)
- 与现有 `wechat_{character_id}` 表共存于同一 LanceDB 存储目录

迁移通过 Django `makemigrations` + `migrate` 执行，无数据丢失风险（纯新增）。

## 9. 向后兼容

- **`ai/chat_graph.py`** — 保留，内部重定向到 `agents/supervisor_graph.py`
- **`ai/memory_graph.py`** — 保留，兼容旧的 memory_update 调用
- **`Friend.memory` 字段** — 保留，改由 Semantic Memory 定期自动同步摘要到此字段作为缓存
- **`api/chat.py`** — 保持 SSE 流式接口不变，内部调用升级后的 Agent graph
- **现有 API 路由** — 所有 endpoint 接口保持不变

## 9. 渐进式实现顺序

```
Step 1: RAG 管道 (ai/rag/*)
  ├── 可独立开发和测试，不影响现有对话
  └── 验证: 检索质量对比 (有无 reranker)

Step 2: 分层记忆 (ai/memory/* + web/models/memory.py)
  ├── 新增 EpisodicMemory + SemanticMemory 模型
  ├── 在 event_stream 后增加写入逻辑
  └── 验证: 手动触发 reflection 查看提炼质量

Step 3: Multi-Agent (ai/agents/*)
  ├── 将 RAG 管道的调用封装到 Memory Agent
  ├── 实现 Supervisor 路由 + Conversation Agent
  └── 最后加入 Emotion Agent（可选路径，不影响核心流程）

Step 4: 集成
  ├── api/chat.py 切换到新的 supervisor_graph
  └── 端到端测试 + 行为对比
```

## 10. 风险与缓解

| 风险 | 缓解 |
|------|------|
| Multi-Agent 延迟增加 | 并行执行 Memory + Emotion；非必要的 Agent 跳过 |
| HyDE + Rewrite 增加 token 消耗 | HyDE 仅 semantic 路径使用；可配置开关 |
| bge-reranker 内存占用 | 备选: Cohere Rerank API；或轻量模型 bge-reranker-base |
| 记忆写入阻塞对话 | 异步写入，不阻塞 SSE 流 |
| 模型迁移成本 | 保持现有接口不变，逐层替换 |
