
# 赵晶莹 (Zhaojingying) — AI Character Chat Platform

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136+-009688.svg)](https://fastapi.tiangolo.com/)
[![Vue](https://img.shields.io/badge/Vue-3.x-4FC08D.svg)](https://vuejs.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

一个基于 AI 的角色聊天平台，通过导入真实微信聊天记录，让 AI 模拟特定人物的语言风格、记忆和对话习惯。

An AI-powered character chat platform that simulates a real person's conversational style by importing authentic WeChat chat history, powered by hybrid search and LLM agents.

## ✨ 功能特性 | Features

- **微信聊天导入** — 解析微信导出的聊天记录，过滤噪音，构建角色记忆库
- **混合搜索** — SQLite FTS5 全文搜索 + LanceDB 语义向量搜索，精准回忆过往对话
- **LangGraph Agent** — 对话代理自动决策：何时搜索记忆、何时直接回答
- **语音交互** — 支持语音识别 (ASR) 和语音合成 (TTS)
- **角色管理** — 创建、编辑、自定义 AI 角色的外观和声音
- **JWT 认证** — 用户注册/登录，安全访问控制
- **Django Admin** — 内置管理后台，方便数据管理

## 🏗️ 技术栈 | Tech Stack

| 层级 | 技术 |
|------|------|
| **后端框架** | FastAPI + Django ORM |
| **前端** | Vue 3 + Vite + DaisyUI |
| **AI/LLM** | DeepSeek V4 Pro + LangChain + LangGraph |
| **向量嵌入** | text-embedding-v4 |
| **向量数据库** | LanceDB |
| **全文搜索** | SQLite FTS5 |
| **语音** | 阿里云 DashScope (gummy-realtime-v1 ASR + cosyvoice-v3-flash TTS) |
| **认证** | django-rest-framework-simplejwt |
| **包管理** | uv (Python) + npm (前端) |

## 📁 项目结构 | Project Structure

```
zhaojingying-2024.4.16/
├── main.py                  # FastAPI 入口
├── django_settings.py       # Django ORM 配置
├── pyproject.toml           # Python 依赖
├── ai/                      # AI 核心
│   ├── chat_graph.py        # LangGraph 对话代理
│   ├── memory_graph.py      # 记忆注入
│   ├── custom_embeddings.py # 自定义向量嵌入
│   └── documents/           # LanceDB 存储（本地）
├── api/                     # FastAPI 路由
│   ├── auth.py              # 认证
│   ├── chat.py              # 对话（含 WebSocket）
│   ├── character.py         # 角色管理
│   ├── import_data.py       # 微信导入
│   └── ...
├── web/                     # Django 模型 + 迁移
│   ├── models/              # User, Character, Message...
│   └── migrations/
├── frontend/                # Vue 3 前端
│   └── src/
│       ├── components/      # 聊天界面、导航栏等
│       ├── views/           # 页面
│       └── stores/          # Pinia 状态管理
└── tools/
    └── wechat_parser.py     # 微信聊天记录解析器
```

## 🚀 快速开始 | Quick Start

### 环境要求

- Python >= 3.12
- Node.js >= 18
- uv (Python 包管理器)

### 1. 克隆项目

```bash
git clone https://github.com/liujunjie20240416/zhaojingying-2024.4.16.git
cd zhaojingying-2024.4.16
```

### 2. 后端配置

```bash
# 安装依赖
uv sync

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 API Key

# 运行数据库迁移
python manage.py migrate  # 或使用 Django manage.py

# 启动服务
uvicorn main:app --reload --port 8000
```

### 3. 前端配置

```bash
cd frontend
npm install
npm run dev        # 开发模式，默认 http://localhost:5173
npm run build      # 生产构建 → static/frontend/
```

### 4. 导入微信聊天记录

1. 将微信导出的 `.txt` 聊天记录放入项目
2. 在前端角色管理页面，点击「导入微信记录」
3. 系统会自动解析、过滤噪音、构建向量索引

## 🔧 环境变量 | Environment Variables

复制 `.env.example` 为 `.env` 并配置：

| 变量 | 说明 |
|------|------|
| `DJANGO_SECRET_KEY` | Django 密钥（用于 session / JWT 签名） |
| `API_KEY` | DashScope API 密钥 |
| `API_BASE` | API 端点地址 |
| `WSS_URL` | WebSocket 地址（语音） |
| `VOICE_URL` | TTS 服务地址 |

## 🤝 贡献 | Contributing

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

## 📄 许可 | License

MIT License

## 👤 作者 | Author

**Junjie Liu** — 上海师范大学

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
