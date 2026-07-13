# Domain Context

- **Imported Chat** — 原始导入聊天；按 Character 保存为 `ChatMessage`，可重新导入和重建派生记忆。
- **Online Chat** — 用户与某个 Friend 后续发生的原始 AI 对话；保存为 `Message`，不会因 Reflection 被删除。
- **Semantic Memory** — 从 Imported Chat、Online Chat 或用户维护中提炼的长期事实；原始聊天的派生数据。
- **Reflection Job** — 对一个 Friend 的一个已结束聊天日执行 Reflection 的持久任务；同一 Friend/聊天日全局唯一，可重试和恢复。
- **Structured Bubble** — AI 一次回复中的一个独立可视气泡；由 `bubbles[]` 明确分隔，气泡内部换行不产生新气泡。
- **Conversation History Search** — 同时查询 Imported Chat 与 Online Chat 的统一原文检索 Module；底层存储保持隔离，结果使用同一 Interface。
- **Style Profile** — 仅依据 Imported Chat 学习的角色说话风格；每次成功重新导入都会根据完整的新记录重新生成，也可显式重建。
- **Conversation Working Summary** — Online Chat 的持久滚动摘要；原始 `Message` 永久保留，模型使用“较早摘要 + 最近 10～15 轮原文”的投影视图延续当前会话，不等同于 Semantic Memory。
