# 虚拟女友产品与 Agent 架构路线图

## 产品原则

1. 用户只感知一个稳定角色；内部 Module、Adapter 或子 Agent 不应破坏人格一致性。
2. 原始聊天是证据，Semantic Memory 是派生事实，两者都可追溯、可删除、可导出。
3. 普通聊天走低延迟确定性工作流；复杂任务才进入规划、工具或子 Agent。
4. 新能力先有权限、回放、评测和失败恢复，再开放自动执行。

## 当前模块图

```text
Vue Chat / Memory / Import UI
        |
FastAPI chat/import/memory routes
        |
LangGraph custom workflow
  |- Supervisor router
  |- Memory Agent
  |    |- Semantic Memory Search
  |    `- Conversation History Search
  |         |- ImportedChat Adapter
  |         `- OnlineChat Adapter
  |- Emotion Agent
  `- Conversation Agent -> Structured Bubble
        |
SQLite source data + LanceDB vector projections
        |
Preprocessing / Reflection Job / Style Reduce
```

## Phase 0：已完成的正确性基础

- Imported Chat 与 Online Chat 原文统一检索，底层继续隔离。
- Semantic Memory 与原始消息建立证据关联。
- Reflection 改为持久化聊天日任务，避免并发重复。
- Structured Bubble 取代按换行猜气泡。
- Style Profile 每次成功重新导入后，基于新的完整 Imported Chat 重新生成。

## Phase 1：可靠性与上下文 Harness（最高优先级）

### 1. Context Assembler Module

提供一个统一 Interface，输入角色、当前消息、最近对话、检索结果、工具结果和模型预算，输出最终 Context Plan。

- 按 token 而不是固定 10 轮裁剪最近消息。
- 增加滚动 Conversation Summary，连接最近消息与长期记忆。
- Persona、Style、Recent、Memory、Evidence、Tool/Skill 分桶预算。
- 保留输出 token 预算，防止输入占满窗口。
- 记录 context manifest：每段来源、token、为何被选中。

### 2. Agent Harness

- 固定回放数据集：普通闲聊、回忆、冲突事实、情绪、长对话、气泡风格。
- 指标：路由准确率、原文召回率、事实引用率、人格一致性、平均延迟、每轮成本。
- Fake LLM / Fake Embedding / Fake Tool Adapter，单元测试不访问外网。
- Trace 默认脱敏；测试、开发、生产分级。
- Prompt、Style、Memory schema 和检索策略版本化。

### 3. Durable Import / Index Jobs

- ImportRun 保存版本、输入摘要、状态和错误。
- Map 结果分片持久化，失败可断点续跑。
- Online Chat 向量索引改为持久 IndexJob/Outbox，不依赖 daemon thread。
- 新版本全部验证后一次切换，失败保留旧版本。

### 4. Provider Adapter Seam

- ChatModel Adapter
- Embedding Adapter
- TTS / ASR Adapter
- Trace Adapter

当前 DeepSeek/DashScope 是第一组 Adapter；测试 fake 与未来其他供应商构成真实 Seam。

## Phase 2：虚拟女友体验增强

### 记忆与关系

- 记忆证据 UI：查看原话、日期、来源、当前/历史状态。
- 用户确认、纠错、合并重复记忆。
- 关系里程碑：初识、纪念日、共同约定、重要事件。
- 滚动近况摘要：最近几天在忙什么、情绪延续、未完成话题。
- “为什么这样回答”私密调试视图，不在角色回复中暴露检索过程。

### 对话体验

- 回复阶段状态：正在回忆、正在组织、正在生成语音。
- 失败重试、停止生成、重新生成、编辑后重发。
- 消息搜索、日期跳转、媒体消息和语音转写。
- 多模态：图片理解、相册回忆、贴纸、语音消息。
- 主动但可控：早晚问候、纪念日、用户授权的日程提醒；支持频率和免打扰设置。

### 安全与信任

- 数据导出、保留期限、按来源删除、彻底删除账号。
- 情绪危机与依赖风险策略；不操纵用户、不假装真人在线状态。
- 工具写操作必须审批；日历、消息、支付等分级权限。

## Phase 3：MCP 与 Skills

### MCP（优先引入）

将应用作为 MCP Host/Client，按需连接：

- Calendar：读取日程、经批准创建提醒。
- Weather：让时间与生活对话更自然。
- Music / Maps / Notes：推荐歌曲、地点和保存共同清单。
- 项目自己的 Memory MCP Server 可后置，只暴露最小权限的 search/add/correct。

MCP 工具结果必须经过权限、大小限制、内容清洗和 Context Assembler，不允许把全部工具描述永久塞进系统提示词。

### Skills（适合中期）

按需加载可复用流程和知识：

- conflict-repair：争执后的安慰与沟通流程。
- anniversary-planner：纪念日计划。
- memory-curator：记忆审查、合并和证据检查。
- travel-companion：旅行规划风格和步骤。
- bedtime-companion：睡前聊天节奏，但不得覆盖核心人格。

先使用项目内审核过的只读 Skills；以后再考虑用户安装和脚本执行。

## Phase 4：Multi-Agent 与 Deep Agents Task Mode

普通伴侣聊天继续使用当前低延迟自定义 LangGraph 工作流。

只在复杂任务开启 Task Mode：

- 整理一年聊天并生成回忆册。
- 规划多日旅行并协调日历、天气、预算和地点。
- 整理相册、语音和共同事件。
- 长期目标陪伴与阶段复盘。

建议结构：

```text
Companion Orchestrator（唯一对用户说话）
  |- Memory Research subagent
  |- Planning subagent
  |- Calendar/Tool subagent
  `- Creative subagent
```

子 Agent 返回结构化结果，不直接模仿女友口吻。最终由 Companion Orchestrator 使用同一个 Persona/Style 生成回复。

Deep Agents 适合这一层，因为它提供规划、文件系统上下文、子 Agent、长任务和自动摘要；不适合每句普通聊天。

## Phase 5：PostgreSQL/pgvector 与 A2A 平台化

### PostgreSQL

在多用户、并发 Worker 或正式部署前迁移：

- SQLite -> PostgreSQL
- FTS5 -> PostgreSQL FTS/GIN（中文使用应用分词或中文扩展）
- LanceDB -> pgvector
- ReflectionJob/ImportRun/IndexJob 使用 `select for update skip locked`

Imported Chat 与 Online Chat 都进入同一 PostgreSQL 集群，但仍保持不同表和权限语义。

### A2A

仅在出现独立远程 Agent 后引入：

- 第三方旅行 Agent
- 健康或学习 Agent
- 用户自己的私有 Agent
- 不同团队/框架维护的 Agent 平台

内部 Memory/Emotion/Conversation 节点不需要 A2A；它们共享同一应用和状态。A2A 用于跨进程、跨组织的 Agent 发现、任务状态和结果交换。

## 前端路线

### P0

- Memory Evidence 折叠面板与原话跳转。
- 导入版本、替换范围、Style 更新结果和失败重试。
- 统一错误提示；移除空 `catch`。
- 聊天发送状态、停止、重试和连接恢复。

### P1

- 原文搜索与日期时间线。
- 记忆冲突/历史状态可视化。
- Context 调试抽屉，仅主人可见。
- 移动端响应式布局、无障碍、虚拟列表。

### P2

- 日历/MCP 权限管理。
- 主动消息和免打扰设置。
- 多模态相册、语音、贴纸与回忆册 Task Mode。

## 技术采用决策

| 技术 | 决策 | 适用位置 |
| --- | --- | --- |
| MCP | 近期引入 | 外部工具和数据连接 |
| Skills | 中期引入 | 按需流程/知识，减少常驻提示词 |
| Multi-Agent | 有条件增强 | 复杂任务的上下文隔离与并行 |
| Deep Agents | 独立 Task Mode | 长任务、规划、文件和子 Agent |
| A2A | 暂缓 | 跨应用/跨组织远程 Agent |
| Harness | 立即建设 | 评测、权限、回放、观测、可靠执行 |

## 推荐执行顺序

1. Context Assembler + token budget。
2. 记忆/路由/风格评测 Harness。
3. ImportRun 与持久 IndexJob。
4. Memory Evidence 和导入版本前端。
5. MCP Calendar/Weather/Reminder 只读试点，再加审批写入。
6. Skills 加载器与 3 个内部审核 Skill。
7. Deep Agents Task Mode 原型。
8. PostgreSQL + pgvector。
9. 有真实外部 Agent 合作需求后再实现 A2A。

## 官方参考

- [Model Context Protocol architecture](https://modelcontextprotocol.io/docs/learn/architecture)
- [LangChain multi-agent patterns](https://docs.langchain.com/oss/python/langchain/multi-agent/index)
- [LangChain Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview)
- [A2A Protocol](https://a2a-protocol.org/)
- [Agent Skills specification](https://agentskills.io/specification)
- [OpenAI Harness Engineering](https://openai.com/index/harness-engineering/)
