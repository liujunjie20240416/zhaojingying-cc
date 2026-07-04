# Changelog

## Unreleased (lumora-landing 分支)

- 新增 Lumora 风格全屏电影感落地页(`/landing/`):4 段可切换背景视频 + 液态玻璃 UI + Instrument Serif 字体
- 落地页导航映射到真实页面(首页/创作/好友),按钮跳登录页
- App.vue 支持 `fullscreen` meta 标记,落地页跳出 NavBar 壳全屏渲染
- 新增依赖 lucide-vue-next
- 落地页接入访客动线:根路径 `/` 改为落地页门面,角色网格首页移至 `/home/`(路由名 homepage-index 不变,所有链接自动兼容),`/landing/` 保留为别名
- 登录/注册页改为全屏动态背景:循环视频 + 白色粒子 + 鼠标视差 + 毛玻璃卡片(新增可复用组件 DynamicBackground.vue),表单逻辑零改动

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
