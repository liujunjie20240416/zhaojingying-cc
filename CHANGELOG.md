# Changelog

## Unreleased (lumora-landing 分支)

- 新增 Lumora 风格全屏电影感落地页(`/landing/`):4 段可切换背景视频 + 液态玻璃 UI + Instrument Serif 字体
- 落地页导航映射到真实页面(首页/创作/好友),按钮跳登录页
- App.vue 支持 `fullscreen` meta 标记,落地页跳出 NavBar 壳全屏渲染
- 新增依赖 lucide-vue-next

## v0.1.1 (2026-05-20)

- jieba 分词替换 n-gram 关键词提取，去掉无效短词（如"去校"），FTS5 匹配更精准
- 记忆检索重构：匹配消息自动拉取前后上下文窗口，AI 看到完整对话场景而非孤立句子
- 聊天头像顶部对齐修复

## v0.1.0 (2026-05-20)

- 初始发布：AI 角色聊天平台
- 微信聊天记录导入与噪音过滤
- LangGraph 对话代理 + DeepSeek V4 Pro
- 混合搜索：SQLite FTS5 + LanceDB 语义搜索
- 语音交互：ASR + TTS（DashScope）
- Vue 3 + DaisyUI 前端
- JWT 认证 + Django Admin 后台
