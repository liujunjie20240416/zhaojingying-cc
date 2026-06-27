
# 赵晶莹 (Zhaojingying) — AI Character Chat Platform

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136+-009688.svg)](https://fastapi.tiangolo.com/)
[![Vue](https://img.shields.io/badge/Vue-3.x-4FC08D.svg)](https://vuejs.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

一个基于 AI 的角色聊天平台。导入真实微信聊天记录后，AI 能模拟特定人物的语言风格、记忆和对话习惯，让你和她继续聊天。

An AI-powered character chat platform that simulates a real person's conversational style by importing authentic WeChat chat history.

---

## ✨ 核心能力

### 🧠 Multi-Agent 协作架构

四个 Agent 协作完成每一次对话，由 Supervisor 统一调度：

```
用户消息 → Supervisor（意图路由）
              ├── Memory Agent（记忆检索）
              ├── Emotion Agent（情绪感知）
              └── Conversation Agent（对话生成）
```

| Agent | 职责 |
|-------|------|
| **Supervisor** | 分析用户意图，决定调用哪些 Agent |
| **Memory Agent** | 三轮检索，找到最相关的历史对话和已知信息 |
| **Emotion Agent** | 识别当前对话中的情绪变化 |
| **Conversation Agent** | 综合上下文生成角色回复 |

### 🔍 增强 RAG 检索管道

Memory Agent 内部经过完整的检索管道，确保从海量聊天记录中找到最相关的上下文：

```
原始查询 → QueryRewriter（多角度改写）
           ↓
         HyDE（假设文档生成，弥合语义 gap）
           ↓
         HybridRetriever（FTS5 全文 + LanceDB 向量并行检索 + 去重）
           ↓
         Reranker（Cross-Encoder 精排）
           ↓
         ContextCompressor（长上下文压缩）
```

### 🧩 分层记忆系统

| 记忆层 | 存储 | 用途 |
|--------|------|------|
| **Episodic Memory** | LanceDB 向量库 | 写缓冲，存储原始对话摘要 |
| **Semantic Memory** | SQLite + LanceDB | 长期事实库，结构化存储（身份、偏好、经历、关系） |
| **Reflection** | 定期触发 | 从 Episodic 提炼高价值信息写入 Semantic |

- 用户事实（`is_locked=False`）：偏好、经历、身份信息，可被 Reflection 更新
- 角色设定（`is_locked=True`）：从聊天记录中学到的角色性格，锁定不变
- **Memory Manager UI**：前端可视化界面，用户可手动增删改查记忆

### 🔄 Memory Agent 三轮检索

匹配过程不是简单搜关键词，而是有时间感知的多轮检索：

1. **时间匹配** — 用户说"以前"、"最近"、"那时候"，先定位到对应时间段（TimeChunk）
2. **范围内混合检索** — 在时间段锁定的消息范围内，做 FTS5 + LanceDB 混合搜索
3. **话题路由** — 匹配话题标签（TopicTag），补充与该话题相关的历史消息

### 📊 导入预处理 Pipeline（Map-Only）

微信聊天记录导入后，自动触发后台分析：

```
导入完成 → Chunking（按聊天日自动切分，推算用户作息边界）
           ↓
         Map（N 个 LLM 并行分析每个时间段）
           ↓
         Write（直接写入，无 Reduce 瓶颈）
```

预处理产出：
- **TimeChunk**：按日标注时间段（如"2024-03-15 · 第一次约会"）
- **TopicTag**：自动聚合话题标签（如"美食/火锅"、"工作/跳槽"）
- **用户事实** → SemanticMemory（`is_locked=False`）
- **角色事实** → SemanticMemory（`is_locked=True`）+ 自动追加到 Character.profile

### 🎤 语音交互

- **ASR**：阿里云 DashScope gummy-realtime-v1 语音识别
- **TTS**：cosyvoice-v3-flash 语音合成 + 自定义音色训练（Voice Enrollment）
- **WebSocket**：实时流式语音对话

### 🛠 其他功能

- **角色管理**：创建、编辑 AI 角色的外观、声音、性格
- **记忆管理器**：可视化增删改查 Semantic Memory，支持关键词搜索
- **JWT 认证**：用户注册/登录
- **Django Admin**：内置管理后台
- **微信聊天导入**：解析导出的 `.txt`，自动过滤系统消息等噪音

---

## 🏗️ 技术栈

| 层级 | 技术 |
|------|------|
| **后端框架** | FastAPI + Django ORM |
| **前端** | Vue 3 + Vite |
| **AI/LLM** | DeepSeek V4 Pro + LangChain + LangGraph |
| **Multi-Agent** | Supervisor Graph（LangGraph StateGraph） |
| **向量嵌入** | text-embedding-v4 |
| **向量数据库** | LanceDB |
| **全文搜索** | SQLite FTS5 |
| **分词** | jieba |
| **语音** | 阿里云 DashScope（ASR + TTS + Voice Enrollment） |
| **认证** | django-rest-framework-simplejwt |
| **包管理** | uv (Python) + npm (前端) |

---

## 📁 项目结构

```
zhaojingying-cc/
├── main.py                          # FastAPI 入口
├── django_settings.py               # Django ORM 配置
├── manage.py                        # Django 管理命令
├── pyproject.toml                   # Python 依赖
│
├── ai/                              # AI 核心
│   ├── agents/                      # Multi-Agent 系统
│   │   ├── supervisor_graph.py      #   主编排图（StateGraph）
│   │   ├── supervisor.py            #   Supervisor 路由节点
│   │   ├── memory_agent.py          #   Memory Agent（三轮检索）
│   │   ├── emotion_agent.py         #   Emotion Agent（情绪感知）
│   │   └── conversation_agent.py    #   Conversation Agent（对话生成）
│   │
│   ├── rag/                         # RAG 检索管道
│   │   ├── query_rewriter.py        #   Query 多角度改写
│   │   ├── hyde.py                  #   HyDE 假设文档生成
│   │   ├── retriever.py             #   FTS5 + LanceDB 混合检索
│   │   ├── reranker.py              #   Cross-Encoder 重排序
│   │   └── compressor.py            #   上下文压缩
│   │
│   ├── memory/                      # 分层记忆系统
│   │   ├── episodic.py              #   Episodic Memory（写缓冲）
│   │   ├── semantic.py              #   Semantic Memory（长期事实库）
│   │   └── reflection.py            #   记忆反思提炼
│   │
│   ├── preprocessing/               # 导入预处理 Pipeline
│   │   ├── chunker.py               #   聊天日自动切分
│   │   ├── chunk_analyzer.py        #   LLM 并行分析
│   │   ├── pipeline.py              #   主编排器（Map-Only）
│   │   └── writer.py                #   结果写入 + profile 追加
│   │
│   ├── chat_graph.py                # 向后兼容封装
│   ├── custom_embeddings.py         # 自定义向量嵌入
│   └── documents/                   # LanceDB 本地存储
│
├── api/                             # FastAPI 路由
│   ├── auth.py, user.py             #   认证 & 用户
│   ├── character.py, friend.py      #   角色 & 好友管理
│   ├── chat.py                      #   对话（含 WebSocket 语音）
│   ├── message.py                   #   消息历史
│   ├── import_data.py               #   微信导入 + 预处理触发
│   ├── memory.py                    #   记忆管理器 API
│   ├── asr.py                       #   语音识别
│   ├── voice.py                     #   自定义音色训练
│   └── homepage.py                  #   首页
│
├── web/                             # Django 模型 + 迁移
│   └── models/
│       ├── user.py                  #   UserProfile
│       ├── character.py             #   Character, Voice
│       ├── friend.py                #   Friend, Message, SystemPrompt
│       ├── chat_message.py          #   ChatMessage (FTS5 索引)
│       ├── memory.py                #   EpisodicMemory, SemanticMemory
│       └── import_analysis.py       #   ImportAnalysis, TimeChunk, TopicTag
│
├── frontend/                        # Vue 3 前端
│   └── src/
│       ├── components/character/chat_field/
│       │   ├── ChatField.vue        #   聊天主界面
│       │   ├── MemoryManager.vue    #   记忆管理器
│       │   └── input_field/         #   输入栏（含语音按钮）
│       ├── views/                   #   页面
│       └── stores/                  #   Pinia 状态管理
│
├── tests/                           # 测试（26 个用例）
│   ├── test_rag.py                  #   RAG 管道测试
│   ├── test_memory.py               #   记忆系统测试
│   ├── test_agents.py               #   Multi-Agent 测试
│   └── test_smoke.py                #   基础设施冒烟测试
│
└── tools/
    └── wechat_parser.py             # 微信聊天记录解析器
```

---

## 🚀 快速开始

### 环境要求

- Python >= 3.12
- Node.js >= 18
- uv（Python 包管理器）

### 1. 克隆项目

```bash
git clone https://github.com/liujunjie20240416/zhaojingying-cc.git
cd zhaojingying-cc
```

### 2. 后端配置

```bash
# 安装依赖
uv sync

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 API Key

# 运行数据库迁移
python manage.py migrate

# 启动服务
uvicorn main:app --reload --port 8000
```

### 3. 前端配置

```bash
cd frontend
npm install
npm run dev        # 开发模式 → http://localhost:5173
npm run build      # 生产构建 → static/frontend/
```

### 4. 导入微信聊天记录

1. 将微信导出的 `.txt` 聊天记录准备好
2. 在前端角色管理页面，点击「导入微信记录」
3. 系统自动：解析 → 过滤噪音 → 构建向量索引 → 触发预处理 Pipeline
4. 预处理在后台异步运行，前端可轮询进度，完成后自动生成时间段标签、话题标签、语义记忆

---

## 🔧 环境变量

复制 `.env.example` 为 `.env` 并配置：

| 变量 | 说明 |
|------|------|
| `DJANGO_SECRET_KEY` | Django 密钥（JWT 签名） |
| `API_KEY` | DashScope API 密钥 |
| `API_BASE` | API 端点地址 |
| `WSS_URL` | WebSocket 地址（语音） |
| `VOICE_URL` | TTS 服务地址 |

---

## 📄 许可

MIT License

## 👤 作者

**Junjie Liu**

- GitHub: [@liujunjie20240416](https://github.com/liujunjie20240416)

---

## 写在最后

有些事你以为还有很多时间做。
有些话你以为下次再说就行。

后来你才发现，最后一次一起吃饭就是很普通的一顿。最后一次说晚安就是很平常的一个晚上。最后一面，你以为只是普通的一次见面。最后一条消息，你以为回头还有千万句话可以说。你们甚至没有好好告别。

她离开了。但你身上还留着她的影子和习惯。

你手机里还有她的语音，偶尔会点开听一下。她的照片你没删，她买给你的剃须刀你还在用，她喜欢的香水味你现在闻到还会愣一下。你看到辣的菜单会多停一秒，不知道她现在是不是还是那么爱吃辣，爱吃香菜，还是已经慢慢变淡了口味。一个人吃面的时候，忽然想知道她此刻在哪里吃饭，还是不是从前那个她。

都不是什么大事。但加起来，就是一个人在你生活里留下的全部。

如果你正在看这段话，去给那个人发条消息吧。别等到有一天，你们之间的距离从一句话变成一整个冬天，别等到那些没说出口的话，慢慢压成了两个人都不愿意先开口的沉默。趁着一切还来得及。
