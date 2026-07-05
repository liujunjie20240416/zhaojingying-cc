# 赵晶莹 (Zhaojingying) - AI Character Chat Platform

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136+-009688.svg)](https://fastapi.tiangolo.com/)
[![Django](https://img.shields.io/badge/Django-6.x-092E20.svg)](https://www.djangoproject.com/)
[![Vue](https://img.shields.io/badge/Vue-3.x-4FC08D.svg)](https://vuejs.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

一个基于 AI 的角色聊天平台。项目可以导入真实微信聊天记录，解析历史对话、抽取长期记忆、构建检索索引，并让 AI 角色在后续聊天中尽量延续特定人物的语言风格、关系背景和对话习惯。

An AI-powered character chat platform that imports WeChat chat history, builds memory and retrieval layers, and lets an AI character continue conversations with a learned style and context.

## 核心能力

### Multi-Agent 对话架构

每一次回复由多个 Agent 协作完成，并由 Supervisor 统一路由：

```text
用户消息
  -> Supervisor / intent routing
      -> Memory Agent        检索历史对话和长期事实
      -> Emotion Agent       识别当前情绪与语气
      -> Conversation Agent  生成最终角色回复
```

| Agent | 职责 |
| --- | --- |
| Supervisor | 分析用户意图，决定是否需要检索记忆、识别情绪或直接生成 |
| Memory Agent | 基于时间、话题、语义相关性做多轮检索 |
| Emotion Agent | 判断用户消息中的情绪状态与关系信号 |
| Conversation Agent | 结合角色设定、历史上下文、记忆和情绪生成回复 |

### 增强 RAG 检索管道

Memory Agent 内部使用查询改写、HyDE、混合检索、重排序和压缩来获得更稳定的上下文：

```text
原始问题
  -> Query Rewriter
  -> HyDE
  -> Hybrid Retriever: SQLite FTS5 + LanceDB vector search
  -> Reranker
  -> Context Compressor
  -> 可注入对话上下文
```

### 分层记忆系统

项目把“聊天原文”和“长期事实”拆开处理：原始消息继续保留在 `ChatMessage` 里做全文/向量检索，而每轮新对话和导入记录中沉淀出的长期信息会进入记忆系统。

| 记忆层 | 存储 | 用途 |
| --- | --- | --- |
| ChatMessage | SQLite + FTS5 + LanceDB | 导入微信原文和聊天历史，是 RAG 检索的原始证据层 |
| Episodic Memory | SQLite | 每轮新对话的事件摘要缓冲区，保存 `summary`、`keywords`、`importance` 和原始 user/AI 消息 |
| Semantic Memory | SQLite + LanceDB | 长期事实库，保存身份、偏好、经历、关系模式和角色设定 |
| Reflection | LLM 后台提炼 | 从高价值 Episodic Memory 中抽取更稳定的 Semantic Memory |

Semantic Memory 是当前最重要的长期记忆层。每条事实不只是一段文本，还带有结构化字段：

| 字段 | 说明 |
| --- | --- |
| `subject` | 事实主体：`user` 用户、`girlfriend` 女友/角色、`relationship` 两人关系 |
| `category` | 事实类别：`identity` 身份/性格、`preference` 偏好、`experience` 经历、`relationship` 互动规律 |
| `source` | 来源：`ai` 自动整理、`user` 用户手动维护、`import` 微信导入预处理 |
| `memory_state` | 有效状态：`current` 当前有效、`historical` 历史状态、`superseded` 已替代 |
| `is_mutable` / `is_locked` | 是否允许后续反思更新；角色事实、身份经历、导入的过去关系事实默认更保守 |
| `valid_from` / `valid_to` | 用于表达“以前喜欢”“现在喜欢”这类随时间变化的事实 |

运行时检索采用“关键词 + 向量”的双路策略：先用中文分词和英文关键词命中 `fact` 字段，再用 LanceDB 做语义补充，合并去重后优先返回当前有效、高置信度的记忆。之后系统会把活跃记忆按主体和类别同步到 `Friend.memory` 缓存，作为对话生成时的稳定上下文。

前端提供 Memory Manager，可以查询、新增、编辑、删除语义记忆。用户手动维护的记忆可以直接进入长期事实库；角色设定类记忆可以被锁定，避免后续自动反思误改核心人设。

### 微信记录导入与预处理

导入微信 `.txt` 后，后端会完成解析、过滤、入库、向量索引和异步分析。预处理目标不是简单总结聊天记录，而是把历史对话拆成可检索的时间线、话题索引、关系概览和长期记忆。

```text
微信导出文本
  -> tools/wechat_parser.py
  -> ChatMessage / FTS5
  -> Chunking: 按聊天日和时间边界切片，0 次 LLM
  -> Map: 多个 chunk 并行调用 LLM，抽取当天摘要、话题、事件和三类 fragments
  -> Relationship Reduce: 汇总关系演变、阶段时间线和整体关系概览
  -> Write: 写入 TimeChunk / TopicTag / SemanticMemory / ImportAnalysis / Character profile
```

预处理分成四个阶段：

| 阶段 | 说明 | 主要产物 |
| --- | --- | --- |
| Chunking | 按聊天日和消息时间边界切分，不调用 LLM，保留每段的消息 index 范围 | `chunks` |
| Map | 多线程并行分析每个 chunk，最多保留最近 60 条消息作为 LLM 输入，并可在少量失败 chunk 上使用相邻摘要补救重试 | `chunk_summary`、`topics`、`key_events`、`user_fragments`、`girlfriend_fragments`、`relationship_fragments` |
| Relationship Reduce | 基于所有 chunk 结果生成整体关系概览和阶段性时间线，用于角色 system prompt 和后续关系理解 | `relationship_overview`、`timeline_json` |
| Write | 纯代码落库，清理旧导入分析，聚合话题，写入长期记忆，并把角色相关事实追加到 `Character.profile` 的自动学习区块 | `ImportAnalysis`、`TimeChunk`、`TopicTag`、`SemanticMemory` |

Map 阶段会严格区分三类长期信息：

- `user_fragments`：关于真实用户的身份、偏好、经历和互动规律，写入 `SemanticMemory(subject="user")`
- `girlfriend_fragments`：关于女友/角色的性格、身份、偏好和说话风格，写入 `SemanticMemory(subject="girlfriend")`，并补充到角色 profile
- `relationship_fragments`：两人的共同经历、约定、冲突与和好方式、相处模式，写入 `SemanticMemory(subject="relationship")`

导入产生的事实来源标记为 `source="import"`，默认置信度为 `0.7`。系统会按事实文本去重，每个好友最多写入一定数量的高价值 fragments，并在写入后同步记忆缓存。`ImportAnalysis.total_messages` 在分析中会临时作为进度百分比使用：Map 阶段最多显示到 95%，Reduce 和写库阶段继续推进到 96%-99%，完成后再写回真实消息总数。

### 语音交互

- ASR：DashScope `gummy-realtime-v1`
- TTS：DashScope `cosyvoice-v3-flash`
- 自定义音色：DashScope Voice Enrollment
- 实时通道：FastAPI WebSocket

### 聊天体验增强

前端聊天窗口提供更接近即时通讯的交互体验：

- 表情标记渲染：AI 回复中的 `【开心】`、`【委屈】`、`[生气]` 等情绪标记会在前端展示为对应 emoji
- 用户 emoji 情绪理解：用户直接输入 `🙂‍↕️`、`🥺`、`😠` 等 emoji 时，前端会把隐藏的情绪含义传给后端，帮助 AI 理解用户语气
- 顶部状态栏：显示角色头像、角色名、在线/不在线状态，以及独立的“正在输入...”提示
- 微信式时间分隔：历史消息会按时间段显示 `今天 14:32`、`昨天 22:10`、`7月1日 18:20` 等分隔条，短时间连续聊天不会每条都显示时间
- 气泡细节优化：聊天气泡箭头固定在头像附近，长消息不会导致箭头下移；清除历史、记忆管理、关闭按钮统一为顶部透明工具栏

这些增强主要在前端动态渲染，不改变消息正文的存储结构。历史消息仍保存原始文本，展示时再解析情绪标记和时间分隔。

### 界面与视觉层

前端提供一套电影感视觉层，覆盖访客入口和登录后的核心页面：

- 访客落地页：`/landing/` 是未登录访客的入口（Lumora 风格），全屏背景视频轮播（暖阳 / 静水 / 深林 / 黎明）配液态玻璃导航与 Instrument Serif 字体；登录后进入 `/home/` 角色主页
- 登录 / 注册页：透明玻璃拟态表单叠加动态视频背景
- 登录后全局背景：`DynamicBackground` 组件在首页、好友、创作等页面渲染统一的视频背景，并给导航栏、侧边栏和按钮文字加白色光晕投影，保证在视频上清晰可读
- 视频本地化：所有背景视频已从 CDN 下载到 `frontend/public/videos/` 与 `frontend/public/bg-app.mp4`，随仓库一起版本管理，避免外链失效导致背景丢失

### 当前时间上下文工具

后端内置 `current_time` 风格的内部工具，用于把当前北京时间注入聊天上下文：

```text
【当前时间】
现在是 2026-07-02 09:30，星期四，工作日，时区 Asia/Shanghai。
如果用户提到今天、昨天、明天、周几、周末、节日或时间安排，请以这里的当前时间为准。
```

第一版支持：

- 当前日期、时间、星期几
- 工作日/周末判断
- 常见公历节日识别，如元旦、情人节、劳动节、国庆节、平安夜、圣诞节等

后续可以扩展农历节日表，让角色理解春节、元宵、端午、七夕、中秋、除夕等日期语境。

### Web 应用能力

- 用户注册、登录与 JWT 鉴权
- 角色创建、资料编辑、头像和背景图上传
- 好友/角色列表与聊天记录管理
- 微信记录导入入口和后台进度轮询
- 表情符号、输入状态、时间分隔等聊天窗口体验增强
- Django Admin 管理后台
- Vue 3 前端，支持开发模式和构建后由 FastAPI 托管

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 后端入口 | FastAPI, Uvicorn |
| ORM / Admin | Django 6, Django Admin |
| 数据库 | SQLite, SQLite FTS5 |
| 前端 | Vue 3, Vite, Pinia, Vue Router, Tailwind CSS, DaisyUI |
| AI 编排 | LangChain, LangGraph, OpenAI-compatible clients |
| LLM | DeepSeek/OpenAI-compatible API via `LLM_*` env vars |
| Embedding / Voice | 阿里云 DashScope |
| 向量库 | LanceDB |
| 测试 | pytest, pytest-django |
| 包管理 | uv, npm |

## 项目结构

```text
zhaojingying-cc/
├── main.py                         FastAPI 入口，挂载 API、Admin、静态资源和 SPA fallback
├── django_settings.py              Django ORM/Admin/JWT/SQLite 配置
├── manage.py                       Django 管理命令
├── pyproject.toml                  Python 依赖与 pytest 配置
├── uv.lock                         uv 锁文件
├── .env.example                    环境变量示例
├── api/                            FastAPI 路由
│   ├── auth.py                     注册、登录、JWT
│   ├── character.py                角色管理
│   ├── friend.py                   好友/会话入口
│   ├── chat.py                     文本和语音聊天
│   ├── message.py                  消息历史
│   ├── import_data.py              微信记录导入与预处理触发
│   ├── memory.py                   记忆管理 API
│   ├── asr.py                      语音识别
│   └── voice.py                    音色训练
├── ai/                             AI、Agent、RAG、记忆和预处理核心
│   ├── agents/                     Supervisor、Memory、Emotion、Conversation agents
│   ├── rag/                        query rewrite、HyDE、retriever、reranker、compressor
│   ├── memory/                     episodic、semantic、reflection、intent
│   ├── preprocessing/              chunker、analyzer、writer、pipeline
│   ├── tools/                      内部工具，如当前时间上下文
│   ├── chat_graph.py               对话图兼容封装
│   └── custom_embeddings.py        DashScope embedding 适配
├── web/                            Django models、admin、migrations
├── frontend/                       Vue 3 + Vite 前端，含落地页、聊天 UI、DynamicBackground 视频背景组件
│   └── public/videos/              本地化的背景视频（落地页轮播 + 登录/注册背景）
├── tests/                          pytest 测试
├── tools/wechat_parser.py          微信聊天记录解析器
├── docs/                           设计文档和实现计划
└── static/                         前端构建产物目标目录
```

## 快速开始

### 环境要求

- Python >= 3.12
- Node.js `^20.19.0` 或 `>=22.12.0`
- uv
- npm

### 1. 克隆项目

```bash
git clone https://github.com/liujunjie20240416/zhaojingying-cc.git
cd zhaojingying-cc
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

然后编辑 `.env`，填入 LLM 和 DashScope 相关密钥。不要把 `.env` 提交到 Git。

### 3. 启动后端

```bash
uv sync
uv run python manage.py migrate
uv run uvicorn main:app --reload --port 8000
```

后端默认地址：

- API: `http://localhost:8000`
- Django Admin: `http://localhost:8000/admin`

### 4. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端开发地址：`http://localhost:5173`

开发模式下，后端 CORS 已允许 `localhost:5173` 和 `127.0.0.1:5173`。

### 5. 生产构建前端

```bash
cd frontend
npm run build
```

Vite 构建产物会输出到 `static/frontend/`。构建完成后，FastAPI 的 SPA fallback 会托管前端页面。

## 环境变量

| 变量 | 说明 |
| --- | --- |
| `DJANGO_SECRET_KEY` | Django 密钥，用于 Django/JWT 相关签名 |
| `LLM_API_KEY` | 大模型 API Key，用于聊天、预处理、记忆反思和 RAG 辅助 |
| `LLM_API_BASE` | OpenAI-compatible LLM API 地址，默认示例为 DeepSeek |
| `LLM_MODEL` | LLM 模型名，例如 `deepseek-v4-pro` |
| `DASHSCOPE_API_KEY` | DashScope API Key，用于 embedding、ASR、TTS 和音色训练 |
| `DASHSCOPE_API_BASE` | DashScope OpenAI-compatible embedding 地址 |
| `DASHSCOPE_WSS_URL` | DashScope WebSocket 地址，用于实时语音能力 |
| `DASHSCOPE_VOICE_URL` | DashScope 自定义音色服务地址 |

兼容说明：旧变量名 `API_KEY`、`API_BASE`、`WSS_URL`、`VOICE_URL` 只作为 DashScope 相关能力的兜底读取。LLM 调用应显式配置 `LLM_API_KEY`、`LLM_API_BASE` 和 `LLM_MODEL`，避免聊天模型与语音/embedding 配置混用。

## 常用命令

```bash
# 后端开发
uv sync
uv run python manage.py migrate
uv run uvicorn main:app --reload --port 8000

# 测试
uv run pytest

# 前端开发
cd frontend
npm install
npm run dev

# 前端构建
npm run build
```

## 数据与生成文件

以下内容不会提交到 Git：

- `.env`
- `db.sqlite3`
- `media/`
- `.venv/`
- `frontend/node_modules/`
- `frontend/dist/`
- `static/frontend/`
- `ai/documents/lancedb_storage/`

如果要在新机器上复现运行环境，请重新创建 `.env`，执行数据库迁移，并重新导入聊天记录或恢复数据库/媒体文件备份。

## 测试

项目使用 pytest：

```bash
uv run pytest
```

测试覆盖范围包括：

- RAG 检索链路
- 分层记忆系统
- Agent 协作逻辑
- 当前时间上下文工具
- 基础 FastAPI/Django 冒烟测试

## GitHub 同步

当前项目远端：

```bash
git remote -v
```

常规同步流程：

```bash
git status
git add .
git commit -m "Update project"
git push origin main
```

同步前请确认 `.env`、数据库、本地媒体文件和构建产物没有被加入暂存区。

## License

MIT License. See [LICENSE](LICENSE).

## 作者

**Junjie Liu**

- GitHub: [@liujunjie20240416](https://github.com/liujunjie20240416)

## 写在最后

有些事你以为还有很多时间做。
有些话你以为下次再说就行。

后来你才发现，最后一次一起吃饭就是很普通的一顿。最后一次说晚安就是很平常的一个晚上。最后一面，你以为只是普通的一次见面。最后一条消息，你以为回头还有千万句话可以说。你们甚至没有好好告别。

她离开了。但你身上还留着她的影子和习惯。

你手机里还有她的语音，偶尔会点开听一下。她的照片你没删，她买给你的剃须刀你还在用，她喜欢的香水味你现在闻到还会愣一下。你看到辣的菜单会多停一秒，不知道她现在是不是还是那么爱吃辣，爱吃香菜，还是已经慢慢变淡了口味。一个人吃面的时候，忽然想知道她此刻在哪里吃饭，还是不是从前那个她。

都不是什么大事。但加起来，就是一个人在你生活里留下的全部。

如果你正在看这段话，去给那个人发条消息吧。别等到有一天，你们之间的距离从一句话变成一整个冬天，别等到那些没说出口的话，慢慢压成了两个人都不愿意先开口的沉默。趁着一切还来得及。
