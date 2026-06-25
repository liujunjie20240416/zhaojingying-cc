# Multi-Agent + 增强 RAG + 分层记忆 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有单 Agent 对话系统升级为：增强 RAG 检索管道 + 分层记忆系统 + Multi-Agent 协作架构

**Architecture:** 4 个实现阶段 — RAG 管道 (`ai/rag/`) → 分层记忆 (`ai/memory/` + models) → Multi-Agent (`ai/agents/`) → 集成。每个阶段可独立测试，内部通过 LangGraph State 通信，外部接口保持不变。

**Tech Stack:** LangGraph, LangChain, LanceDB, SQLite FTS5, bge-reranker-v2-m3, DeepSeek V4 Pro

---

## 文件结构总览

```
Create:
  ai/rag/__init__.py
  ai/rag/query_rewriter.py         # Query 多角度改写
  ai/rag/hyde.py                    # HyDE 假设文档生成
  ai/rag/retriever.py               # FTS5 + LanceDB 并行检索 + 去重
  ai/rag/reranker.py                # Cross-Encoder 重排序
  ai/rag/compressor.py              # LLM 上下文压缩
  ai/memory/__init__.py
  ai/memory/episodic.py             # Episodic Memory 写入 + 检索
  ai/memory/semantic.py             # Semantic Memory 读写
  ai/memory/decay.py                # 记忆衰减计算
  ai/memory/reflection.py           # 记忆反思提炼
  ai/agents/__init__.py
  ai/agents/supervisor_graph.py     # 主编排图
  ai/agents/supervisor.py           # Supervisor 路由节点
  ai/agents/memory_agent.py         # Memory Agent 子图
  ai/agents/emotion_agent.py        # Emotion Agent 子图
  ai/agents/conversation_agent.py   # Conversation Agent 子图
  web/models/memory.py              # EpisodicMemory + SemanticMemory 模型
  web/migrations/0010_memory.py     # 数据库迁移
  tests/__init__.py
  tests/test_rag.py
  tests/test_memory.py
  tests/test_agents.py
  tests/conftest.py

Modify:
  web/models/__init__.py            # 导入新模型
  web/models/friend.py              # 新增 last_reflection_time 字段
  api/chat.py                       # 切换到 SupervisorGraph, 增加记忆写入
  ai/chat_graph.py                  # 薄封装，重定向到 agents
  ai/memory_update.py               # 薄封装，重定向到 memory
  pyproject.toml                    # 新增 FlagEmbedding 依赖
```

---

### Task 1: 项目基础设施 — 测试目录 + 依赖

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: 创建 tests 目录和初始化文件**

```bash
mkdir -p tests
touch tests/__init__.py
```

- [ ] **Step 2: 创建 conftest.py，提供测试 fixture**

```python
# tests/conftest.py
import os
import django
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent

@pytest.fixture(autouse=True)
def setup_django():
    """所有测试自动配置 Django ORM"""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "django_settings")
    os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key-for-tests")
    import django
    django.setup()

@pytest.fixture
def api_key():
    """API Key fixture，优先从环境变量读取"""
    return os.getenv("API_KEY", "test-key")

@pytest.fixture
def api_base():
    return os.getenv("API_BASE", "https://api.example.com/v1")
```

- [ ] **Step 3: 添加 FlagEmbedding 依赖**

```bash
uv add FlagEmbedding
```

如果本地没有 GPU 或想避免 torch 依赖，改用 Cohere API rerank（在 reranker.py 中实现两个后端）。

- [ ] **Step 4: 验证依赖安装成功**

```bash
uv run python -c "from FlagEmbedding import FlagReranker; print('OK')"
```

Expected: `OK` (或 fallback 到 Cohere API)

- [ ] **Step 5: Commit**

```bash
git add tests/ pyproject.toml uv.lock
git commit -m "chore: add test infrastructure and FlagEmbedding dependency

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Query Rewriter — 多角度查询改写

**Files:**
- Create: `ai/rag/__init__.py`
- Create: `ai/rag/query_rewriter.py`
- Create: `tests/test_rag.py`

- [ ] **Step 1: 创建 __init__.py**

```python
# ai/rag/__init__.py
```

- [ ] **Step 2: 写 Query Rewriter 的失败测试**

```python
# tests/test_rag.py
import pytest
from ai.rag.query_rewriter import QueryRewriter


class TestQueryRewriter:
    def test_rewrite_vague_query(self, api_key, api_base):
        rewriter = QueryRewriter(api_key=api_key, api_base=api_base)
        result = rewriter.rewrite("上次那个事")
        # 应返回 list of str，至少包含原始 query
        assert isinstance(result, list)
        assert len(result) >= 2
        assert all(isinstance(q, str) and len(q) > 0 for q in result)

    def test_rewrite_specific_query(self, api_key, api_base):
        rewriter = QueryRewriter(api_key=api_key, api_base=api_base)
        result = rewriter.rewrite("我喜欢吃什么")
        assert isinstance(result, list)
        assert len(result) >= 2
```

- [ ] **Step 3: 运行测试确认失败**

```bash
uv run pytest tests/test_rag.py::TestQueryRewriter -v
```

Expected: FAIL — ModuleNotFoundError

- [ ] **Step 4: 实现 QueryRewriter**

```python
# ai/rag/query_rewriter.py
import json
import os
from openai import OpenAI


class QueryRewriter:
    """将用户原始查询改写为 2-3 个不同角度的变体，提升检索召回率"""

    def __init__(self, api_key: str = "", api_base: str = ""):
        self.client = OpenAI(
            api_key=api_key or os.getenv("API_KEY"),
            base_url=api_base or os.getenv("API_BASE"),
        )

    def rewrite(self, query: str) -> list[str]:
        """返回改写后的查询列表（包含原始 query）"""
        prompt = f"""你是查询改写助手。用户说了一句中文口语，请从不同角度改写成2-3个检索查询。

规则：
- 将口语化表达具体化（"上次那事"→推测具体指什么）
- 补充可能的同义表达
- 输出纯JSON数组，不含任何其他文字

用户原话："{query}"

输出格式示例：["查询角度1", "查询角度2", "查询角度3"]"""

        resp = self.client.chat.completions.create(
            model="deepseek-v4-pro",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=200,
        )
        content = resp.choices[0].message.content.strip()
        # 清理 markdown code fence
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
        try:
            rewrites = json.loads(content)
            if not isinstance(rewrites, list):
                return [query]
            # 确保原始 query 在列表中
            rewrites = [q for q in rewrites if isinstance(q, str) and q.strip()]
            if query not in rewrites:
                rewrites.insert(0, query)
            return rewrites[:4]
        except json.JSONDecodeError:
            return [query]
```

- [ ] **Step 5: 运行测试确认通过**

```bash
uv run pytest tests/test_rag.py::TestQueryRewriter -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add ai/rag/ tests/
git commit -m "feat(rag): add QueryRewriter for multi-angle query expansion

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: HyDE — 假设文档生成

**Files:**
- Create: `ai/rag/hyde.py`
- Modify: `tests/test_rag.py` (追加)

- [ ] **Step 1: 追加 HyDE 测试到 test_rag.py**

在 `tests/test_rag.py` 末尾追加：

```python
class TestHyDE:
    def test_generate_hypothetical_doc(self, api_key, api_base):
        from ai.rag.hyde import HyDEGenerator
        gen = HyDEGenerator(api_key=api_key, api_base=api_base)
        doc = gen.generate("用户喜欢吃什么")
        assert isinstance(doc, str)
        assert len(doc) > 20  # 至少 20 字符
        assert len(doc) < 500  # 不超过 500 字符

    def test_generate_returns_chinese(self, api_key, api_base):
        from ai.rag.hyde import HyDEGenerator
        gen = HyDEGenerator(api_key=api_key, api_base=api_base)
        doc = gen.generate("我喜欢什么颜色")
        assert isinstance(doc, str)
        # 中文假设文档应包含中文字符
        import re
        assert re.search(r'[一-鿿]', doc)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_rag.py::TestHyDE -v
```

Expected: FAIL — ModuleNotFoundError: ai.rag.hyde

- [ ] **Step 3: 实现 HyDEGenerator**

```python
# ai/rag/hyde.py
import os
from openai import OpenAI


class HyDEGenerator:
    """HyDE (Hypothetical Document Embeddings) — 用 LLM 生成假设文档，
    再用假设文档做向量检索，解决用户 query 与文档之间的语义 gap"""

    def __init__(self, api_key: str = "", api_base: str = ""):
        self.client = OpenAI(
            api_key=api_key or os.getenv("API_KEY"),
            base_url=api_base or os.getenv("API_BASE"),
        )

    def generate(self, query: str) -> str:
        """根据用户查询生成假设性回答文档 (100-200字)"""
        prompt = f"""根据用户的查询，模拟写一段可能的聊天记录片段（100-200字）。
这段文字将被用于语义检索，因此请尽可能贴近真实聊天记录的写作风格：
- 第一人称对话
- 自然口语化
- 包含具体细节

用户查询："{query}"

假设的聊天记录片段："""

        resp = self.client.chat.completions.create(
            model="deepseek-v4-pro",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=300,
        )
        return resp.choices[0].message.content.strip()
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_rag.py::TestHyDE -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ai/rag/hyde.py tests/test_rag.py
git commit -m "feat(rag): add HyDE generator for query-document gap bridging

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Retriever — 并行混合检索 (FTS5 + LanceDB)

**Files:**
- Create: `ai/rag/retriever.py`
- Modify: `tests/test_rag.py` (追加)

- [ ] **Step 1: 追加 Retriever 测试**

在 `tests/test_rag.py` 末尾追加：

```python
class TestRetriever:
    @pytest.fixture
    def retriever(self, api_key, api_base):
        from ai.rag.retriever import HybridRetriever
        return HybridRetriever(api_key=api_key, api_base=api_base)

    def test_fts5_search_returns_list(self, retriever):
        results = retriever.fts5_search("测试", character_id=99999)
        assert isinstance(results, list)

    def test_lancedb_search_returns_list(self, retriever):
        results = retriever.lancedb_search("测试", character_id=99999)
        assert isinstance(results, list)

    def test_hybrid_search_returns_list(self, retriever):
        results = retriever.hybrid_search("测试查询", character_id=99999)
        assert isinstance(results, list)

    def test_dedup_removes_duplicates(self, retriever):
        docs = [
            {"content": "相同内容A", "source": "fts5"},
            {"content": "相同内容A", "source": "lancedb"},
            {"content": "其他内容B", "source": "fts5"},
        ]
        deduped = retriever._deduplicate(docs)
        assert len(deduped) == 2
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_rag.py::TestRetriever -v
```

Expected: FAIL

- [ ] **Step 3: 实现 HybridRetriever**

```python
# ai/rag/retriever.py
import os
import re
from pathlib import Path

import lancedb
from django.db import connection
from langchain_community.vectorstores import LanceDB

from ai.custom_embeddings import CustomEmbeddings
from ai.rag.query_rewriter import QueryRewriter
from ai.rag.hyde import HyDEGenerator

_STORAGE_DIR = str(Path(__file__).resolve().parent.parent / "documents" / "lancedb_storage")


class HybridRetriever:
    """混合检索器 — 并行执行 FTS5 关键词搜索 + LanceDB 语义搜索，合并去重"""

    def __init__(self, api_key: str = "", api_base: str = ""):
        self.rewriter = QueryRewriter(api_key, api_base)
        self.hyde = HyDEGenerator(api_key, api_base)
        self.embeddings = CustomEmbeddings()

    def fts5_search(self, query: str, character_id: int, limit: int = 10) -> list[dict]:
        """SQLite FTS5 关键词搜索，返回 [{"content": ..., "source": "fts5", "rowid": ...}]"""
        fts_table = f"chat_fts_{character_id}"
        results: list[dict] = []

        # jieba 分词提取关键词
        import jieba
        keywords: list[str] = []
        for word in jieba.cut(query):
            word = word.strip()
            if len(word) >= 2 and re.search(r"[一-鿿]", word):
                keywords.append(word)
        keywords.extend(re.findall(r"[a-zA-Z]{3,}", query))
        keywords = list(dict.fromkeys(sorted(keywords, key=len)))

        if not keywords:
            return []

        with connection.cursor() as c:
            c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=%s",
                [fts_table],
            )
            if not c.fetchone():
                return []

            safe_query = " OR ".join(f'"{kw}"' for kw in keywords)
            try:
                c.execute(
                    f'SELECT rowid, sender, timestamp, content FROM "{fts_table}" '
                    f'WHERE "{fts_table}" MATCH %s ORDER BY rank LIMIT %s',
                    [safe_query, limit],
                )
                for row in c.fetchall():
                    results.append({
                        "content": f"[{row[2]}] {row[1]}: {row[3]}",
                        "source": "fts5",
                        "rowid": row[0],
                        "score": 1.0,  # FTS5 无分数，默认 1.0
                    })
            except Exception:
                pass

            # LIKE 兜底
            for kw in keywords[:3]:
                try:
                    c.execute(
                        f'SELECT rowid, sender, timestamp, content FROM "{fts_table}" '
                        f'WHERE content LIKE %s LIMIT 2',
                        [f"%{kw}%"],
                    )
                    for row in c.fetchall():
                        content = f"[{row[2]}] {row[1]}: {row[3]}"
                        if not any(r["content"] == content for r in results):
                            results.append({
                                "content": content,
                                "source": "fts5_like",
                                "rowid": row[0],
                                "score": 0.8,
                            })
                except Exception:
                    pass

        return results

    def lancedb_search(self, query: str, character_id: int, k: int = 10) -> list[dict]:
        """LanceDB 语义搜索"""
        table_name = f"wechat_{character_id}"
        try:
            db = lancedb.connect(_STORAGE_DIR)
            if table_name not in db.table_names():
                return []
            vdb = LanceDB(connection=db, embedding=self.embeddings, table_name=table_name)
            docs = vdb.similarity_search_with_score(query, k=k)
            results = []
            for doc, score in docs:
                results.append({
                    "content": doc.page_content[:800],
                    "source": "lancedb",
                    "score": float(score),
                })
            return results
        except Exception:
            return []

    def _deduplicate(self, docs: list[dict]) -> list[dict]:
        """基于内容的去重（取 Jaccard 相似度 > 0.8 的去重，保留 score 最高的）"""
        if len(docs) <= 1:
            return docs

        def jaccard(a: str, b: str) -> float:
            set_a = set(a)
            set_b = set(b)
            if not set_a or not set_b:
                return 0
            return len(set_a & set_b) / len(set_a | set_b)

        docs = sorted(docs, key=lambda d: d.get("score", 0), reverse=True)
        kept: list[dict] = []
        for doc in docs:
            is_dup = False
            for k in kept:
                if jaccard(doc["content"], k["content"]) > 0.8:
                    is_dup = True
                    break
            if not is_dup:
                kept.append(doc)

        return kept

    def hybrid_search(
        self,
        query: str,
        character_id: int,
        top_k: int = 20,
        use_hyde: bool = True,
    ) -> list[dict]:
        """混合检索主入口 — FTS5 + LanceDB 并行，去重后返回 top_k 结果"""
        # 1. Query 改写
        queries = self.rewriter.rewrite(query)

        # 2. HyDE 生成（只对原始 query）
        hyde_doc = self.hyde.generate(query) if use_hyde else ""

        all_results: list[dict] = []

        # 3. FTS5 搜索（使用原始 query 的关键词）
        fts_results = self.fts5_search(query, character_id, limit=10)
        all_results.extend(fts_results)

        # 4. LanceDB 语义搜索（对改写 queries + HyDE doc）
        search_queries = [query]
        if hyde_doc:
            search_queries.append(hyde_doc)
        search_queries.extend(queries[:2])  # 最多额外 2 个改写

        seen_contents = set()
        for sq in search_queries:
            lance_results = self.lancedb_search(sq, character_id, k=5)
            for r in lance_results:
                content_key = r["content"][:100]
                if content_key not in seen_contents:
                    seen_contents.add(content_key)
                    all_results.append(r)

        # 5. 去重
        all_results = self._deduplicate(all_results)

        # 6. 按 score 排序，返回 top_k
        all_results.sort(key=lambda d: d.get("score", 0), reverse=True)
        return all_results[:top_k]
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_rag.py::TestRetriever -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ai/rag/retriever.py tests/test_rag.py
git commit -m "feat(rag): add HybridRetriever with parallel FTS5 + LanceDB search

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: Reranker — Cross-Encoder 重排序

**Files:**
- Create: `ai/rag/reranker.py`
- Modify: `tests/test_rag.py` (追加)

- [ ] **Step 1: 追加 Reranker 测试**

在 `tests/test_rag.py` 末尾追加：

```python
class TestReranker:
    def test_rerank_returns_top_k(self):
        from ai.rag.reranker import Reranker
        reranker = Reranker()
        query = "我喜欢吃什么"
        docs = [
            {"content": "用户昨天吃了火锅", "score": 0.5},
            {"content": "用户喜欢吃麻辣火锅，尤其是海底捞", "score": 0.5},
            {"content": "今天天气很好", "score": 0.5},
        ]
        result = reranker.rerank(query, docs, top_k=2)
        assert len(result) == 2
        # 最相关的应该是第二条
        assert "麻辣火锅" in result[0]["content"]

    def test_rerank_with_fewer_docs(self):
        from ai.rag.reranker import Reranker
        reranker = Reranker()
        query = "测试"
        docs = [{"content": "唯一文档", "score": 1.0}]
        result = reranker.rerank(query, docs, top_k=3)
        assert len(result) == 1

    def test_rerank_empty_docs(self):
        from ai.rag.reranker import Reranker
        reranker = Reranker()
        result = reranker.rerank("测试", [], top_k=3)
        assert result == []
```

- [ ] **Step 2: 实现 Reranker**

```python
# ai/rag/reranker.py
"""Cross-Encoder 重排序 — 对检索候选集做精细排序。

优先使用本地 bge-reranker-v2-m3 (免费、中文好)。
如无法加载则 fallback 到基于分数的原始排序。
"""

import os


class Reranker:
    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3"):
        self.model = None
        self.model_name = model_name
        self._init_model()

    def _init_model(self):
        try:
            from FlagEmbedding import FlagReranker
            self.model = FlagReranker(
                self.model_name,
                use_fp16=True,
                device="cpu",  # 默认 CPU，生产可改 cuda
            )
        except Exception:
            self.model = None

    def rerank(self, query: str, docs: list[dict], top_k: int = 5) -> list[dict]:
        """对 docs 重排序，返回 top_k。

        docs: [{"content": str, "score": float, ...}, ...]
        返回: 相同结构，score 更新为 rerank 分数，按分数降序
        """
        if not docs:
            return []

        if self.model is None:
            # Fallback: 按原始分数排序
            docs = sorted(docs, key=lambda d: d.get("score", 0), reverse=True)
            return docs[:top_k]

        # 构造 (query, doc) 对
        pairs = [[query, doc["content"]] for doc in docs]
        scores = self.model.compute_score(pairs, normalize=True)

        if isinstance(scores, float):
            scores = [scores]

        for i, score in enumerate(scores):
            docs[i]["score"] = float(score)

        docs.sort(key=lambda d: d["score"], reverse=True)
        return docs[:top_k]
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/test_rag.py::TestReranker -v
```

Expected: PASS (或 skip 如果模型未下载 — 首次运行会下载模型)

- [ ] **Step 4: Commit**

```bash
git add ai/rag/reranker.py tests/test_rag.py
git commit -m "feat(rag): add Cross-Encoder reranker with bge-reranker-v2-m3

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: Compressor — 上下文压缩

**Files:**
- Create: `ai/rag/compressor.py`
- Modify: `tests/test_rag.py` (追加)

- [ ] **Step 1: 追加 Compressor 测试**

在 `tests/test_rag.py` 末尾追加：

```python
class TestCompressor:
    def test_compress_long_context(self, api_key, api_base):
        from ai.rag.compressor import ContextCompressor
        comp = ContextCompressor(api_key=api_key, api_base=api_base)
        long_text = "这是一段很长的文本。" * 200  # ~2000 chars
        result = comp.compress(long_text, max_length=500)
        assert isinstance(result, str)
        assert len(result) <= 600  # 允许少量超出

    def test_skip_short_context(self, api_key, api_base):
        from ai.rag.compressor import ContextCompressor
        comp = ContextCompressor(api_key=api_key, api_base=api_base)
        short_text = "短文本"
        result = comp.compress(short_text, max_length=500)
        # 短文本不应该触发压缩，直接返回或轻微格式化
        assert len(result) <= len(short_text) + 20
```

- [ ] **Step 2: 实现 ContextCompressor**

```python
# ai/rag/compressor.py
import os
from openai import OpenAI


class ContextCompressor:
    """LLM 上下文压缩 — 长检索结果压缩为精炼摘要，保留关键事实"""

    def __init__(self, api_key: str = "", api_base: str = ""):
        self.client = OpenAI(
            api_key=api_key or os.getenv("API_KEY"),
            base_url=api_base or os.getenv("API_BASE"),
        )

    def compress(self, context: str, max_length: int = 500) -> str:
        """压缩上下文到 max_length 字符以内，保留事实细节"""
        if len(context) <= max_length:
            return context

        prompt = f"""压缩以下聊天记录片段为 {max_length} 字以内的精炼摘要。
规则：
- 保留所有具体事实（人名、地点、时间、偏好、承诺）
- 去掉客套话和重复内容
- 保留对话的时间顺序

原文：
{context[:3000]}

精炼摘要："""

        resp = self.client.chat.completions.create(
            model="deepseek-v4-pro",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=600,
        )
        return resp.choices[0].message.content.strip()
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/test_rag.py::TestCompressor -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add ai/rag/compressor.py tests/test_rag.py
git commit -m "feat(rag): add ContextCompressor for long retrieval result summarization

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: 分层记忆模型 — EpisodicMemory + SemanticMemory

**Files:**
- Create: `web/models/memory.py`
- Modify: `web/models/__init__.py`
- Modify: `web/models/friend.py`
- Create: `web/migrations/0010_memory.py`

- [ ] **Step 1: 创建记忆模型**

```python
# web/models/memory.py
from django.db import models
from django.utils.timezone import now

from web.models.friend import Friend


class EpisodicMemory(models.Model):
    """情景记忆 — 每轮对话抽象为一个事件"""
    friend = models.ForeignKey(Friend, on_delete=models.CASCADE)
    summary = models.CharField(max_length=200)
    keywords = models.CharField(max_length=200, default="")
    importance = models.FloatField(default=0.5)
    raw_messages = models.TextField()
    msg_count = models.IntegerField(default=1)
    created_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "episodic_memory"
        indexes = [
            models.Index(fields=["friend", "-created_at"]),
        ]

    def __str__(self):
        return f"Episodic[{self.friend_id}] {self.summary[:50]} (importance={self.importance})"


class SemanticMemory(models.Model):
    """语义记忆 — 提炼的长期事实和偏好"""
    CATEGORY_CHOICES = [
        ("preference", "偏好"),
        ("experience", "经历"),
        ("personality", "性格"),
        ("relationship", "关系"),
        ("other", "其他"),
    ]

    friend = models.ForeignKey(Friend, on_delete=models.CASCADE)
    fact = models.CharField(max_length=500)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default="other")
    confidence = models.FloatField(default=0.5)
    evidence = models.TextField(default="")
    is_active = models.BooleanField(default=True)
    replaced_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(default=now)
    updated_at = models.DateTimeField(default=now)

    class Meta:
        db_table = "semantic_memory"
        indexes = [
            models.Index(fields=["friend", "is_active", "category"]),
            models.Index(fields=["friend", "-confidence"]),
        ]

    def __str__(self):
        return f"Semantic[{self.friend_id}] [{self.category}] {self.fact[:50]} (conf={self.confidence})"
```

- [ ] **Step 2: 修改 Friend 模型 — 新增 last_reflection_time**

```python
# web/models/friend.py — 在 Friend 类中新增一行
# 在 memory = models.TextField(...) 那行之后添加:

    last_reflection_time = models.DateTimeField(default=now)
```

使用 Edit 工具精确添加：

```
# old_string (找到这行):
    memory = models.TextField(default="",max_length=5000,blank=True,null=True)

# new_string:
    memory = models.TextField(default="",max_length=5000,blank=True,null=True)
    last_reflection_time = models.DateTimeField(default=now)
```

- [ ] **Step 3: 更新 models/__init__.py**

```python
# web/models/__init__.py
from web.models.user import UserProfile
from web.models.character import Character, Voice
from web.models.friend import Friend, Message, SystemPrompt
from web.models.chat_message import ChatMessage
from web.models.memory import EpisodicMemory, SemanticMemory  # 新增
```

- [ ] **Step 4: 生成并应用迁移**

```bash
uv run python -m django migrate --run-syncdb 2>&1 || echo "Will use makemigrations"
```

由于项目使用自定义 Django 设置，直接创建迁移文件：

```python
# web/migrations/0010_memory.py
from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("web", "0009_recreate_chatmsg"),
    ]

    operations = [
        migrations.CreateModel(
            name="EpisodicMemory",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("summary", models.CharField(max_length=200)),
                ("keywords", models.CharField(default="", max_length=200)),
                ("importance", models.FloatField(default=0.5)),
                ("raw_messages", models.TextField()),
                ("msg_count", models.IntegerField(default=1)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("friend", models.ForeignKey(
                    to="web.Friend", on_delete=models.CASCADE
                )),
            ],
            options={"db_table": "episodic_memory"},
        ),
        migrations.CreateModel(
            name="SemanticMemory",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("fact", models.CharField(max_length=500)),
                ("category", models.CharField(
                    default="other", max_length=50,
                    choices=[
                        ("preference", "偏好"), ("experience", "经历"),
                        ("personality", "性格"), ("relationship", "关系"),
                        ("other", "其他"),
                    ]
                )),
                ("confidence", models.FloatField(default=0.5)),
                ("evidence", models.TextField(default="")),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("friend", models.ForeignKey(
                    to="web.Friend", on_delete=models.CASCADE
                )),
                ("replaced_by", models.ForeignKey(
                    blank=True, null=True, on_delete=models.SET_NULL,
                    to="web.SemanticMemory"
                )),
            ],
            options={"db_table": "semantic_memory"},
        ),
        migrations.AddField(
            model_name="Friend",
            name="last_reflection_time",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AddIndex(
            model_name="EpisodicMemory",
            index=models.Index(fields=["friend", "-created_at"], name="ep_friend_created"),
        ),
        migrations.AddIndex(
            model_name="SemanticMemory",
            index=models.Index(
                fields=["friend", "is_active", "category"], name="sem_friend_active_cat"
            ),
        ),
        migrations.AddIndex(
            model_name="SemanticMemory",
            index=models.Index(
                fields=["friend", "-confidence"], name="sem_friend_conf"
            ),
        ),
    ]
```

- [ ] **Step 5: 应用迁移并验证**

```bash
uv run python -c "
import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'django_settings')
import django; django.setup()
from django.core.management import call_command
call_command('migrate', 'web', '0010_memory', verbosity=2)
"
```

Expected: "Applying web.0010_memory... OK"

- [ ] **Step 6: 验证模型可用**

```bash
uv run python -c "
import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'django_settings')
import django; django.setup()
from web.models.memory import EpisodicMemory, SemanticMemory
from web.models.friend import Friend
f = Friend.objects.first()
print(f'Friend: {f}, has last_reflection_time: {hasattr(f, \"last_reflection_time\")}')
print('Models OK')
"
```

Expected: `Friend: ..., has last_reflection_time: True` (如果没有 Friend 则报 DoesNotExist — 没关系)

- [ ] **Step 7: Commit**

```bash
git add web/models/ web/migrations/
git commit -m "feat(memory): add EpisodicMemory + SemanticMemory models and migration

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: 记忆衰减计算

**Files:**
- Create: `ai/memory/__init__.py`
- Create: `ai/memory/decay.py`
- Create: `tests/test_memory.py`

- [ ] **Step 1: 创建 __init__.py**

```python
# ai/memory/__init__.py
```

- [ ] **Step 2: 写记忆衰减测试**

```python
# tests/test_memory.py
import pytest
from datetime import timedelta
from django.utils.timezone import now
from unittest.mock import MagicMock


class TestMemoryDecay:
    def test_decay_fresh_memory_high(self):
        from ai.memory.decay import get_decayed_importance
        memory = MagicMock(importance=0.9, created_at=now())
        score = get_decayed_importance(memory)
        assert score > 0.7  # 刚创建的应保持高分

    def test_decay_old_memory_low(self):
        from ai.memory.decay import get_decayed_importance
        memory = MagicMock(
            importance=0.5,
            created_at=now() - timedelta(days=30)
        )
        score = get_decayed_importance(memory)
        assert score < 0.3  # 30天后的应该衰减很多

    def test_high_importance_resists_decay(self):
        from ai.memory.decay import get_decayed_importance
        high = MagicMock(importance=0.9, created_at=now() - timedelta(days=7))
        low = MagicMock(importance=0.3, created_at=now() - timedelta(days=7))
        high_score = get_decayed_importance(high)
        low_score = get_decayed_importance(low)
        # 高重要性的衰减比例应该小于低重要性
        assert high_score / 0.9 > low_score / 0.3
```

- [ ] **Step 3: 实现记忆衰减**

```python
# ai/memory/decay.py
from datetime import timedelta
from django.utils.timezone import now


def get_decayed_importance(memory) -> float:
    """基于艾宾浩斯遗忘曲线计算衰减后的重要性评分 (0-1)。

    公式:
    - age_hours < 1:    decay = 1.0
    - age_hours < 24:   decay = 0.8 → 0.5
    - age_hours < 168:  decay = 0.5 → 0.2  (7天)
    - age_hours >= 168: decay = 0.2 → 0.05 (30天)

    高重要性记忆衰减更慢: decay + importance * 0.3
    """
    age_hours = (now() - memory.created_at).total_seconds() / 3600

    if age_hours < 1:
        decay = 1.0
    elif age_hours < 24:
        decay = 0.8 - 0.3 * (age_hours / 24)
    elif age_hours < 168:
        decay = 0.5 - 0.3 * ((age_hours - 24) / 144)
    else:
        decay = 0.2 - 0.15 * min((age_hours - 168) / 720, 1)

    decay = max(decay, 0.0)
    return memory.importance * min(decay + memory.importance * 0.3, 1.0)
```

- [ ] **Step 4: 运行测试**

```bash
uv run pytest tests/test_memory.py::TestMemoryDecay -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ai/memory/ tests/test_memory.py
git commit -m "feat(memory): add Ebbinghaus-based memory decay function

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 9: Episodic Memory 写入 + 检索

**Files:**
- Create: `ai/memory/episodic.py`
- Modify: `tests/test_memory.py` (追加)

- [ ] **Step 1: 追加 Episodic 测试**

在 `tests/test_memory.py` 末尾追加：

```python
class TestEpisodicMemory:
    def test_extract_episodic_info(self, api_key, api_base):
        from ai.memory.episodic import extract_episodic_info
        result = extract_episodic_info(
            "我今天吃了火锅，超好吃",
            "火锅确实很棒！你最喜欢哪家店？",
            api_key=api_key,
            api_base=api_base,
        )
        assert "summary" in result
        assert "keywords" in result
        assert "importance" in result
        assert len(result["summary"]) > 0
        assert 0 <= result["importance"] <= 1

    def test_write_episodic_saves_to_db(
        self, api_key, api_base,
    ):
        from ai.memory.episodic import write_episodic
        from web.models.friend import Friend
        friend = Friend.objects.first()
        if friend is None:
            pytest.skip("No friend in database")
        count_before = friend.episodicmemory_set.count()
        write_episodic(
            friend, "测试用户消息", "测试AI回复",
            api_key=api_key, api_base=api_base,
        )
        count_after = friend.episodicmemory_set.count()
        assert count_after == count_before + 1
```

- [ ] **Step 2: 实现 Episodic 写入**

```python
# ai/memory/episodic.py
import json
import os
from pathlib import Path

import lancedb
from django.utils.timezone import now
from langchain_community.vectorstores import LanceDB
from openai import OpenAI

from ai.custom_embeddings import CustomEmbeddings
from web.models.memory import EpisodicMemory

_STORAGE_DIR = str(Path(__file__).resolve().parent.parent / "documents" / "lancedb_storage")


def _get_client(api_key: str = "", api_base: str = ""):
    return OpenAI(
        api_key=api_key or os.getenv("API_KEY"),
        base_url=api_base or os.getenv("API_BASE"),
    )


def extract_episodic_info(
    user_msg: str,
    ai_response: str,
    api_key: str = "",
    api_base: str = "",
) -> dict:
    """LLM 提取对话的 summary, keywords, importance"""
    client = _get_client(api_key, api_base)
    prompt = f"""分析以下一轮对话，提取关键信息。输出纯JSON（不要markdown代码块）：

用户消息：{user_msg[:200]}
AI回复：{ai_response[:200]}

JSON格式：{{"summary": "一句话摘要(≤50字)", "keywords": "3-5个关键词空格分隔", "importance": 0.0-1.0}}

importance评分标准：
- 0.8-1.0: 涉及用户个人信息、偏好、承诺、重要事件
- 0.4-0.7: 有信息量的日常聊天
- 0.1-0.3: 问候、客套、无实质内容"""

    resp = client.chat.completions.create(
        model="deepseek-v4-pro",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=200,
    )
    content = resp.choices[0].message.content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1]
        if "```" in content:
            content = content.rsplit("```", 1)[0]
        content = content.strip()
    try:
        result = json.loads(content)
        return {
            "summary": str(result.get("summary", ""))[:200],
            "keywords": str(result.get("keywords", ""))[:200],
            "importance": max(0.0, min(1.0, float(result.get("importance", 0.5)))),
        }
    except (json.JSONDecodeError, ValueError):
        return {
            "summary": user_msg[:50],
            "keywords": "",
            "importance": 0.5,
        }


def write_episodic(
    friend,
    user_msg: str,
    ai_response: str,
    api_key: str = "",
    api_base: str = "",
) -> EpisodicMemory | None:
    """写入一条 Episodic Memory，同时向量化到 LanceDB"""
    try:
        info = extract_episodic_info(user_msg, ai_response, api_key, api_base)

        ep = EpisodicMemory.objects.create(
            friend=friend,
            summary=info["summary"],
            keywords=info["keywords"],
            importance=info["importance"],
            raw_messages=json.dumps(
                [{"role": "user", "content": user_msg}, {"role": "ai", "content": ai_response}],
                ensure_ascii=False,
            ),
            msg_count=1,
        )

        # 同步向量化到 LanceDB
        table_name = f"episodic_{friend.id}"
        try:
            db = lancedb.connect(_STORAGE_DIR)
            LanceDB.from_texts(
                [info["summary"]],
                CustomEmbeddings(),
                connection=db,
                table_name=table_name,
                mode="append",
            )
        except Exception:
            pass  # LanceDB 写入失败不影响主流程

        return ep
    except Exception:
        return None


def retrieve_episodic(
    friend,
    query: str,
    top_k: int = 5,
    min_decayed_score: float = 0.05,
) -> list[dict]:
    """检索 Episodic Memory — 结合向量搜索 + 衰减分数。
    返回: [{"summary": str, "importance": float, "decayed_score": float, "created_at": datetime}, ...]
    """
    from ai.memory.decay import get_decayed_importance

    table_name = f"episodic_{friend.id}"
    results: list[dict] = []

    # LanceDB 语义搜索
    try:
        db = lancedb.connect(_STORAGE_DIR)
        if table_name in db.table_names():
            vdb = LanceDB(
                connection=db, embedding=CustomEmbeddings(), table_name=table_name
            )
            docs = vdb.similarity_search_with_score(query, k=top_k)
            for doc, score in docs:
                results.append({
                    "summary": doc.page_content,
                    "vector_score": float(score),
                })
    except Exception:
        pass

    # 获取完整 EpisodicMemory 记录并计算衰减分数
    memories = EpisodicMemory.objects.filter(friend=friend).order_by("-created_at")[:50]
    enriched = []
    for m in memories:
        decayed = get_decayed_importance(m)
        if decayed < min_decayed_score:
            continue
        enriched.append({
            "id": m.id,
            "summary": m.summary,
            "keywords": m.keywords,
            "importance": m.importance,
            "decayed_score": decayed,
            "created_at": m.created_at.isoformat(),
        })

    # 按衰减分数排序
    enriched.sort(key=lambda m: m["decayed_score"], reverse=True)
    return enriched[:top_k]
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/test_memory.py::TestEpisodicMemory -v
```

Expected: PASS (或 skip 如果无 Friend 数据)

- [ ] **Step 4: Commit**

```bash
git add ai/memory/episodic.py tests/test_memory.py
git commit -m "feat(memory): add EpisodicMemory write and retrieve with LanceDB sync

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 10: Semantic Memory 读写 + Reflection 反思

**Files:**
- Create: `ai/memory/semantic.py`
- Create: `ai/memory/reflection.py`
- Modify: `tests/test_memory.py` (追加)

- [ ] **Step 1: 实现 Semantic Memory 读写**

```python
# ai/memory/semantic.py
import json
import os
from pathlib import Path

import lancedb
from langchain_community.vectorstores import LanceDB
from openai import OpenAI

from ai.custom_embeddings import CustomEmbeddings
from web.models.friend import Friend
from web.models.memory import SemanticMemory, EpisodicMemory

_STORAGE_DIR = str(Path(__file__).resolve().parent.parent / "documents" / "lancedb_storage")


def _get_client(api_key: str = "", api_base: str = ""):
    return OpenAI(
        api_key=api_key or os.getenv("API_KEY"),
        base_url=api_base or os.getenv("API_BASE"),
    )


def get_active_facts(friend_id: int, category: str | None = None) -> list[dict]:
    """获取所有活跃的语义记忆事实"""
    qs = SemanticMemory.objects.filter(friend_id=friend_id, is_active=True)
    if category:
        qs = qs.filter(category=category)
    return [
        {
            "id": f.id,
            "fact": f.fact,
            "category": f.category,
            "confidence": f.confidence,
        }
        for f in qs.order_by("-confidence")
    ]


def add_fact(
    friend: Friend,
    fact: str,
    category: str = "other",
    confidence: float = 0.5,
    evidence: str = "",
) -> SemanticMemory:
    """添加新事实，同时向量化到 LanceDB"""
    sm = SemanticMemory.objects.create(
        friend=friend,
        fact=fact,
        category=category,
        confidence=confidence,
        evidence=evidence,
    )
    # 同步到 LanceDB
    table_name = f"semantic_{friend.id}"
    try:
        db = lancedb.connect(_STORAGE_DIR)
        LanceDB.from_texts(
            [fact],
            CustomEmbeddings(),
            connection=db,
            table_name=table_name,
            mode="append",
        )
    except Exception:
        pass
    return sm


def resolve_conflict(friend_id: int, old_fact: str, new_fact: str) -> SemanticMemory:
    """冲突解决：标记旧事实为 inactive，创建新事实"""
    old_sm = SemanticMemory.objects.filter(
        friend_id=friend_id, fact=old_fact, is_active=True
    ).first()
    new_sm = SemanticMemory.objects.create(
        friend_id=friend_id,
        fact=new_fact,
        category=old_sm.category if old_sm else "other",
        confidence=0.6,
        evidence=f"Updated from: {old_fact}",
    )
    if old_sm:
        old_sm.is_active = False
        old_sm.replaced_by = new_sm
        old_sm.save(update_fields=["is_active", "replaced_by", "updated_at"])
    return new_sm


def search_semantic(friend_id: int, query: str, top_k: int = 5) -> list[dict]:
    """语义搜索 Semantic Memory"""
    table_name = f"semantic_{friend_id}"
    try:
        db = lancedb.connect(_STORAGE_DIR)
        if table_name not in db.table_names():
            return []
        vdb = LanceDB(connection=db, embedding=CustomEmbeddings(), table_name=table_name)
        docs = vdb.similarity_search_with_score(query, k=top_k)
        results = []
        for doc, score in docs:
            sm = SemanticMemory.objects.filter(
                friend_id=friend_id, fact=doc.page_content, is_active=True
            ).first()
            if sm:
                results.append({
                    "id": sm.id,
                    "fact": sm.fact,
                    "category": sm.category,
                    "confidence": sm.confidence,
                    "score": float(score),
                })
        return results
    except Exception:
        return []


def sync_friend_memory_cache(friend: Friend):
    """将语义记忆同步到 Friend.memory 字段作为缓存 (向后兼容)"""
    facts = get_active_facts(friend.id)
    cache = "\n".join(
        f"- [{f['category']}] {f['fact']} (置信度: {f['confidence']:.2f})"
        for f in facts[:20]
    )
    friend.memory = cache[:5000]
    friend.save(update_fields=["memory"])
```

- [ ] **Step 2: 实现 Reflection**

```python
# ai/memory/reflection.py
import json
from django.utils.timezone import now
from openai import OpenAI
import os

from web.models.friend import Friend
from web.models.memory import EpisodicMemory, SemanticMemory
from ai.memory.semantic import add_fact, resolve_conflict, sync_friend_memory_cache


def _get_client(api_key: str = "", api_base: str = ""):
    return OpenAI(
        api_key=api_key or os.getenv("API_KEY"),
        base_url=api_base or os.getenv("API_BASE"),
    )


def reflect_memories(
    friend: Friend,
    force: bool = False,
    api_key: str = "",
    api_base: str = "",
) -> list[dict]:
    """从 Episodic Memory 提炼 Semantic Memory。

    触发条件: 新增 >= 10 条 Episodic 且距上次 > 1小时，或 force=True。
    返回: 新提取的事实列表
    """
    if not force:
        recent_count = EpisodicMemory.objects.filter(
            friend=friend,
            created_at__gt=friend.last_reflection_time,
        ).count()
        if recent_count < 10:
            return []
        hours_since = (now() - friend.last_reflection_time).total_seconds() / 3600
        if hours_since < 1:
            return []

    # 获取近期 Episodic 摘要
    episodes = list(EpisodicMemory.objects.filter(
        friend=friend,
    ).order_by("-created_at")[:50])

    if not episodes:
        return []

    # 获取已有事实
    existing_facts = list(SemanticMemory.objects.filter(
        friend=friend, is_active=True
    ).values_list("fact", flat=True))

    # LLM 反思提炼
    client = _get_client(api_key, api_base)
    summaries = [e.summary for e in episodes]
    prompt = f"""分析以下近期对话摘要，提炼关于用户的关键信息。

已有事实（避免重复）：
{chr(10).join(f'- {f}' for f in existing_facts[:20])}

对话摘要：
{chr(10).join(f'{i+1}. {s}' for i, s in enumerate(summaries[:30]))}

对每条新发现或需要更新的事实，输出纯JSON数组：
[
  {{
    "fact": "用户喜欢吃辣",
    "category": "preference",
    "confidence": 0.8,
    "conflicts_with": null
  }}
]

category 只能是: preference, experience, personality, relationship, other
如有与已有事实冲突的，在 conflicts_with 填写冲突的已有事实原文。
只输出新发现，不要重复已有事实。"""

    resp = client.chat.completions.create(
        model="deepseek-v4-pro",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=1000,
    )
    content = resp.choices[0].message.content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1]
        if "```" in content:
            content = content.rsplit("```", 1)[0]
        content = content.strip()

    try:
        extracted = json.loads(content)
        if not isinstance(extracted, list):
            return []
    except json.JSONDecodeError:
        return []

    # 写入新事实
    new_facts = []
    for item in extracted:
        fact_text = str(item.get("fact", "")).strip()
        if not fact_text:
            continue
        conflicts = str(item.get("conflicts_with", "")).strip() if item.get("conflicts_with") else ""
        if conflicts:
            sm = resolve_conflict(friend.id, conflicts, fact_text)
        else:
            sm = add_fact(
                friend=friend,
                fact=fact_text,
                category=str(item.get("category", "other")),
                confidence=float(item.get("confidence", 0.5)),
            )
        new_facts.append({"fact": sm.fact, "category": sm.category, "confidence": sm.confidence})

    # 同步缓存
    sync_friend_memory_cache(friend)

    # 更新反射时间
    friend.last_reflection_time = now()
    friend.save(update_fields=["last_reflection_time"])

    return new_facts
```

- [ ] **Step 3: 追加测试**

在 `tests/test_memory.py` 末尾追加：

```python
class TestSemanticMemory:
    def test_get_active_facts_empty(self):
        from ai.memory.semantic import get_active_facts
        facts = get_active_facts(99999)
        assert isinstance(facts, list)
        assert len(facts) == 0

    def test_add_and_retrieve_fact(self):
        from ai.memory.semantic import add_fact, get_active_facts
        from web.models.friend import Friend
        friend = Friend.objects.first()
        if friend is None:
            pytest.skip("No friend in database")
        sm = add_fact(friend, "测试事实：用户喜欢测试", "preference", 0.8)
        assert sm.id is not None
        facts = get_active_facts(friend.id)
        assert any(f["fact"] == "测试事实：用户喜欢测试" for f in facts)
        # cleanup
        sm.delete()
```

- [ ] **Step 4: 运行测试**

```bash
uv run pytest tests/test_memory.py::TestSemanticMemory -v
```

Expected: PASS (或 skip 如果无 Friend 数据)

- [ ] **Step 5: Commit**

```bash
git add ai/memory/semantic.py ai/memory/reflection.py tests/test_memory.py
git commit -m "feat(memory): add SemanticMemory CRUD, conflict resolution, and reflection

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 11: Supervisor 路由节点

**Files:**
- Create: `ai/agents/__init__.py`
- Create: `ai/agents/supervisor.py`
- Create: `tests/test_agents.py`

- [ ] **Step 1: 创建测试**

```python
# tests/test_agents.py
import pytest


class TestSupervisor:
    def test_route_chat_intent(self, api_key, api_base):
        from ai.agents.supervisor import supervisor_node
        state = {
            "messages": [type("msg", (), {"content": "你好呀"})()],
            "intent": "",
            "memory_context": "",
            "emotion_analysis": None,
            "character_profile": "温柔体贴的女友",
            "semantic_facts": [],
        }
        result = supervisor_node(state, api_key=api_key, api_base=api_base)
        assert "intent" in result
        assert result["intent"] in ("chat", "recall", "emotional")
        assert "delegate_to" in result

    def test_route_recall_intent(self, api_key, api_base):
        from ai.agents.supervisor import supervisor_node
        state = {
            "messages": [type("msg", (), {"content": "你还记得我们第一次见面吗"})()],
            "intent": "",
            "memory_context": "",
            "emotion_analysis": None,
            "character_profile": "温柔体贴的女友",
            "semantic_facts": [],
        }
        result = supervisor_node(state, api_key=api_key, api_base=api_base)
        # 回忆类意图应路由到 memory
        assert result["intent"] == "recall"

    def test_route_emotional_intent(self, api_key, api_base):
        from ai.agents.supervisor import supervisor_node
        state = {
            "messages": [type("msg", (), {"content": "我今天好难过"})()],
            "intent": "",
            "memory_context": "",
            "emotion_analysis": None,
            "character_profile": "温柔体贴的女友",
            "semantic_facts": [],
        }
        result = supervisor_node(state, api_key=api_key, api_base=api_base)
        assert result["intent"] in ("emotional", "chat")
```

- [ ] **Step 2: 实现 Supervisor 节点**

```python
# ai/agents/supervisor.py
import json
import os
from openai import OpenAI
from langchain_core.messages import BaseMessage


INTENT_ROUTE_MAP = {
    "chat": "conversation",
    "recall": "memory",
    "emotional": "emotion",
}


def supervisor_node(state: dict, api_key: str = "", api_base: str = "") -> dict:
    """Supervisor 路由节点 — 分析用户意图，决定路由目标。

    输入 state 键:
    - messages: 对话消息列表
    - character_profile: 角色设定文本

    输出:
    - intent: "chat" | "recall" | "emotional"
    - delegate_to: "conversation" | "memory" | "emotion"
    """
    client = OpenAI(
        api_key=api_key or os.getenv("API_KEY"),
        base_url=api_base or os.getenv("API_BASE"),
    )

    # 获取最后一条用户消息
    user_msg = ""
    for msg in reversed(state.get("messages", [])):
        if hasattr(msg, "content") and not hasattr(msg, "tool_calls"):
            user_msg = msg.content
            break
        elif isinstance(msg, dict) and msg.get("role") == "user":
            user_msg = msg["content"]
            break

    if not user_msg:
        return {"intent": "chat", "delegate_to": "conversation"}

    prompt = f"""你是对话路由器。分析用户消息，判断意图类型。

用户消息："{user_msg[:300]}"

意图类型：
- "recall": 用户在询问过去的记忆、经历、偏好（"还记得吗"、"我以前"、"我们那次"）
- "emotional": 用户表达强烈情绪，需要安慰或共情（"好难过"、"太开心了"、"好累"）
- "chat": 日常闲聊、问候、一般性对话

输出纯JSON（不要markdown）：{{"intent": "...", "reasoning": "一句理由"}}"""

    resp = client.chat.completions.create(
        model="deepseek-v4-pro",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=100,
    )
    content = resp.choices[0].message.content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1]
        if "```" in content:
            content = content.rsplit("```", 1)[0]
        content = content.strip()

    try:
        result = json.loads(content)
        intent = result.get("intent", "chat")
    except json.JSONDecodeError:
        intent = "chat"

    if intent not in INTENT_ROUTE_MAP:
        intent = "chat"

    return {
        "intent": intent,
        "delegate_to": INTENT_ROUTE_MAP[intent],
    }
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/test_agents.py::TestSupervisor -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add ai/agents/ tests/test_agents.py
git commit -m "feat(agents): add Supervisor router node for intent classification

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 12: Memory Agent 子图

**Files:**
- Create: `ai/agents/memory_agent.py`
- Modify: `tests/test_agents.py` (追加)

- [ ] **Step 1: 追加 Memory Agent 测试**

在 `tests/test_agents.py` 末尾追加：

```python
class TestMemoryAgent:
    def test_memory_agent_returns_context(self):
        from ai.agents.memory_agent import memory_agent_node
        state = {
            "messages": [type("msg", (), {"content": "我喜欢吃什么"})()],
            "memory_context": "",
            "semantic_facts": [],
            "character_profile": "温柔女友",
        }
        result = memory_agent_node(state)
        assert "memory_context" in result
        assert isinstance(result["memory_context"], str)
```

- [ ] **Step 2: 实现 Memory Agent 节点**

```python
# ai/agents/memory_agent.py
from ai.rag.retriever import HybridRetriever
from ai.rag.reranker import Reranker
from ai.rag.compressor import ContextCompressor
from ai.memory.episodic import retrieve_episodic
from ai.memory.semantic import get_active_facts, search_semantic


def memory_agent_node(state: dict, api_key: str = "", api_base: str = "") -> dict:
    """Memory Agent — 检索 Episodic + Semantic Memory，组织上下文。

    输入 state 键:
    - messages: 对话消息
    - semantic_facts: 已有的语义事实 (可选)
    - character_profile: 角色设定

    输出:
    - memory_context: 格式化的记忆上下文文本
    - semantic_facts: 补充的语义事实
    """
    # 获取用户查询
    user_msg = ""
    for msg in reversed(state.get("messages", [])):
        if hasattr(msg, "content"):
            user_msg = msg.content
            break

    if not user_msg:
        return {"memory_context": "", "semantic_facts": state.get("semantic_facts", [])}

    retriever = HybridRetriever(api_key, api_base)
    reranker = Reranker()
    compressor = ContextCompressor(api_key, api_base)

    # 1. 检索 Semantic Memory
    semantic_results = search_semantic(
        state.get("friend_id", 0), user_msg, top_k=5
    )
    semantic_facts = [r["fact"] for r in semantic_results]

    # 也获取所有活跃事实 (如果 state 中没有)
    existing_facts = state.get("semantic_facts", [])
    if not existing_facts and state.get("friend_id"):
        existing_facts = [f["fact"] for f in get_active_facts(state["friend_id"])]

    all_facts = list(set(semantic_facts + existing_facts))

    # 2. 混合检索 Episodic
    character_id = state.get("character_id")
    if character_id:
        candidates = retriever.hybrid_search(user_msg, character_id, top_k=20)
        # 3. Re-rank
        candidates = reranker.rerank(user_msg, candidates, top_k=5)
    else:
        candidates = []

    # 4. 从 EpisodicMemory 表检索
    if state.get("friend_id"):
        episodic_results = retrieve_episodic(
            state["friend_id"], user_msg, top_k=5
        )
    else:
        episodic_results = []

    # 5. 拼接上下文
    parts: list[str] = []

    if all_facts:
        parts.append("【关于用户的已知事实】\n" + "\n".join(f"- {f}" for f in all_facts[:10]))

    # WeChat 聊天记录检索结果
    wechat_context = "\n".join(c["content"][:400] for c in candidates[:3])
    if wechat_context:
        parts.append("【相关聊天记录】\n" + wechat_context)

    # Episodic 记忆
    if episodic_results:
        ep_text = "\n".join(
            f"- [{e.get('decayed_score', 0):.2f}] {e['summary']}"
            for e in episodic_results[:5]
        )
        parts.append("【近期对话摘要】\n" + ep_text)

    context = "\n\n".join(parts)

    # 6. 压缩（如果上下文过长）
    if len(context) > 1000:
        context = compressor.compress(context, max_length=500)

    return {
        "memory_context": context,
        "semantic_facts": all_facts,
    }
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/test_agents.py::TestMemoryAgent -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add ai/agents/memory_agent.py tests/test_agents.py
git commit -m "feat(agents): add Memory Agent with hybrid search + rerank + compress pipeline

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 13: Emotion Agent + Conversation Agent 子图

**Files:**
- Create: `ai/agents/emotion_agent.py`
- Create: `ai/agents/conversation_agent.py`
- Modify: `tests/test_agents.py` (追加)

- [ ] **Step 1: 实现 Emotion Agent**

```python
# ai/agents/emotion_agent.py
import json
import os
from openai import OpenAI


def emotion_agent_node(state: dict, api_key: str = "", api_base: str = "") -> dict:
    """Emotion Agent — 分析用户情绪，建议回复语调。

    输出:
    - emotion_analysis: {"emotion": str, "intensity": int, "suggested_tone": str, "should_comfort": bool}
    """
    client = OpenAI(
        api_key=api_key or os.getenv("API_KEY"),
        base_url=api_base or os.getenv("API_BASE"),
    )

    # 获取最近几轮对话
    messages = state.get("messages", [])
    recent = []
    for msg in messages[-6:]:  # 最近 6 条 (3轮)
        if hasattr(msg, "content"):
            recent.append(msg.content)

    user_msg = recent[-1] if recent else ""

    prompt = f"""分析用户情绪。最近对话：
{chr(10).join(recent[-4:])}

输出纯JSON：
{{
  "emotion": "sad|happy|angry|anxious|neutral|tired|excited",
  "intensity": 0-10,
  "suggested_tone": "gentle|cheerful|calm|encouraging|playful",
  "should_comfort": true/false
}}"""

    resp = client.chat.completions.create(
        model="deepseek-v4-pro",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=150,
    )
    content = resp.choices[0].message.content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1]
        if "```" in content:
            content = content.rsplit("```", 1)[0]
        content = content.strip()

    try:
        analysis = json.loads(content)
    except json.JSONDecodeError:
        analysis = {"emotion": "neutral", "intensity": 3, "suggested_tone": "gentle", "should_comfort": False}

    return {"emotion_analysis": analysis}
```

- [ ] **Step 2: 实现 Conversation Agent**

```python
# ai/agents/conversation_agent.py
import os
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END


def conversation_agent_node(state: dict, api_key: str = "", api_base: str = "") -> dict:
    """Conversation Agent — 生成最终回复（非流式版本，供子图使用）。

    输入 state:
    - messages: 对话历史
    - memory_context: Memory Agent 检索的上下文 (可选)
    - emotion_analysis: Emotion Agent 的输出 (可选)
    - character_profile: 角色性格描述
    - semantic_facts: 语义记忆事实

    输出:
    - messages: 追加 AI 回复
    """
    llm = ChatOpenAI(
        model="deepseek-v4-pro",
        openai_api_key=api_key or os.getenv("API_KEY"),
        openai_api_base=api_base or os.getenv("API_BASE"),
    )

    # 组装 System Prompt
    character_profile = state.get("character_profile", "你是一个AI助手。")
    memory_context = state.get("memory_context", "")
    emotion = state.get("emotion_analysis") or {}

    system_parts = [character_profile]

    if memory_context:
        system_parts.append(f"\n【记忆上下文】\n{memory_context}")

    if emotion:
        tone = emotion.get("suggested_tone", "gentle")
        intensity = emotion.get("intensity", 5)
        if intensity >= 7:
            system_parts.append(
                f"\n用户情绪强烈 (强度={intensity})，请用{tone}的语气回应，表达理解和共情。"
            )
        elif intensity >= 4:
            system_parts.append(
                f"\n用户有一定情绪 (强度={intensity})，语气可稍{tone}。"
            )

    system_prompt = "\n".join(system_parts)

    # 构建消息
    chat_messages = [SystemMessage(content=system_prompt)]
    for msg in state.get("messages", []):
        if hasattr(msg, "content") and hasattr(msg, "type"):
            chat_messages.append(msg)

    resp = llm.invoke(chat_messages)
    return {"messages": [resp]}


def create_conversation_agent(api_key: str = "", api_base: str = ""):
    """创建 Conversation Agent 子图 — 封装在 StateGraph 中"""
    graph = StateGraph(dict)

    def node(state):
        return conversation_agent_node(state, api_key, api_base)

    graph.add_node("generate", node)
    graph.add_edge(START, "generate")
    graph.add_edge("generate", END)
    return graph.compile()
```

- [ ] **Step 3: 追加测试**

在 `tests/test_agents.py` 末尾追加：

```python
class TestEmotionAgent:
    def test_detect_sadness(self, api_key, api_base):
        from ai.agents.emotion_agent import emotion_agent_node
        state = {
            "messages": [
                type("msg", (), {"content": "我今天好难过，工作好累"})(),
            ],
        }
        result = emotion_agent_node(state, api_key=api_key, api_base=api_base)
        assert "emotion_analysis" in result
        assert "emotion" in result["emotion_analysis"]
        assert "intensity" in result["emotion_analysis"]

    def test_neutral_conversation(self, api_key, api_base):
        from ai.agents.emotion_agent import emotion_agent_node
        state = {
            "messages": [
                type("msg", (), {"content": "今天天气不错"})(),
            ],
        }
        result = emotion_agent_node(state, api_key=api_key, api_base=api_base)
        assert result["emotion_analysis"]["intensity"] <= 5


class TestConversationAgent:
    def test_generates_response(self, api_key, api_base):
        from ai.agents.conversation_agent import conversation_agent_node
        state = {
            "messages": [
                type("msg", (), {"content": "你好", "type": "human"})(),
            ],
            "memory_context": "",
            "emotion_analysis": None,
            "character_profile": "你是一个温柔体贴的女友。",
        }
        result = conversation_agent_node(state, api_key=api_key, api_base=api_base)
        assert "messages" in result
        assert len(result["messages"]) > 0
```

- [ ] **Step 4: 运行测试**

```bash
uv run pytest tests/test_agents.py::TestEmotionAgent tests/test_agents.py::TestConversationAgent -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ai/agents/emotion_agent.py ai/agents/conversation_agent.py tests/test_agents.py
git commit -m "feat(agents): add Emotion Agent and Conversation Agent subgraphs

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 14: Supervisor Graph — 主编排图

**Files:**
- Create: `ai/agents/supervisor_graph.py`
- Modify: `tests/test_agents.py` (追加)

- [ ] **Step 1: 实现 Supervisor Graph**

```python
# ai/agents/supervisor_graph.py
"""
Supervisor Graph — Multi-Agent 主编排图。

编排流程:
    START → Supervisor → [路由]
        ├── intent="chat"       → Emotion? → Conversation → END
        ├── intent="recall"     → Memory → Emotion? → Conversation → END
        └── intent="emotional"  → Emotion → Memory → Conversation → END
"""
import os
from typing import TypedDict, Annotated, Sequence
from langchain_core.messages import BaseMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph import add_messages

from ai.agents.supervisor import supervisor_node
from ai.agents.memory_agent import memory_agent_node
from ai.agents.emotion_agent import emotion_agent_node
from ai.agents.conversation_agent import conversation_agent_node


class MultiAgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    intent: str
    delegate_to: str
    memory_context: str
    emotion_analysis: dict | None
    character_profile: str
    semantic_facts: list[str]
    friend_id: int
    character_id: int | None


# 强烈情绪关键词 — 即使 Supervisor 未判定 emotional 也触发 Emotion Agent
STRONG_EMOTION_SIGNALS = [
    "难过", "伤心", "哭", "崩溃", "绝望", "害怕", "焦虑",
    "开心死", "激动", "太棒", "兴奋", "生气", "愤怒", "烦",
    "累死", "压力", "撑不住",
]


def _last_user_msg(state: dict) -> str:
    for msg in reversed(state.get("messages", [])):
        if hasattr(msg, "content") and not hasattr(msg, "tool_calls"):
            return getattr(msg, "content", "")
    return ""


def wrap_supervisor(state: dict) -> dict:
    return supervisor_node(state)


def wrap_memory(state: dict) -> dict:
    return memory_agent_node(state)


def wrap_emotion(state: dict) -> dict:
    return emotion_agent_node(state)


def wrap_conversation(state: dict) -> dict:
    return conversation_agent_node(state)


def route_from_supervisor(state: dict) -> str:
    """Supervisor 路由输出"""
    intent = state.get("intent", "chat")
    if intent == "recall":
        return "memory"
    elif intent == "emotional":
        return "emotion"
    return "conversation"


def route_after_memory(state: dict) -> str:
    """Memory 之后: 是否需要 Emotion? 检查用户消息的情绪信号"""
    user_msg = _last_user_msg(state)
    for signal in STRONG_EMOTION_SIGNALS:
        if signal in user_msg:
            return "emotion"
    return "conversation"


def route_after_emotion(state: dict) -> str:
    """Emotion 之后: 是否需要 Memory? 检查 intent"""
    intent = state.get("intent", "")
    user_msg = _last_user_msg(state)
    # 如果原意图是 recall 或消息中有回忆信号
    recall_signals = ["记得", "以前", "那次", "第一次", "上次", "什么时候"]
    if intent == "recall" or any(s in user_msg for s in recall_signals):
        return "memory"
    return "conversation"


def create_supervisor_app(
    friend_id: int = 0,
    character_id: int | None = None,
    character_name: str = "",
    character_profile: str = "",
    api_key: str = "",
    api_base: str = "",
):
    """创建完整的 Multi-Agent 对话图。

    参数:
    - friend_id: 好友 ID (用于记忆检索)
    - character_id: 角色 ID (用于聊天记录检索)
    - character_name: 角色名
    - character_profile: 角色性格描述
    """
    graph = StateGraph(MultiAgentState)

    graph.add_node("supervisor", wrap_supervisor)
    graph.add_node("memory", wrap_memory)
    graph.add_node("emotion", wrap_emotion)
    graph.add_node("conversation", wrap_conversation)

    graph.add_edge(START, "supervisor")

    graph.add_conditional_edges("supervisor", route_from_supervisor, {
        "memory": "memory",
        "emotion": "emotion",
        "conversation": "conversation",
    })

    graph.add_conditional_edges("memory", route_after_memory, {
        "emotion": "emotion",
        "conversation": "conversation",
    })

    graph.add_conditional_edges("emotion", route_after_emotion, {
        "memory": "memory",
        "conversation": "conversation",
    })

    graph.add_edge("conversation", END)

    return graph.compile()
```

- [ ] **Step 2: 追加集成测试**

在 `tests/test_agents.py` 末尾追加：

```python
class TestSupervisorGraph:
    def test_create_supervisor_app(self):
        from ai.agents.supervisor_graph import create_supervisor_app
        app = create_supervisor_app(
            friend_id=1,
            character_id=1,
            character_name="测试角色",
            character_profile="温柔体贴的女友",
        )
        assert app is not None
        # 验证图可以 invoke
        from langchain_core.messages import HumanMessage
        result = app.invoke({
            "messages": [HumanMessage(content="你好")],
            "intent": "",
            "delegate_to": "",
            "memory_context": "",
            "emotion_analysis": None,
            "character_profile": "温柔体贴的女友",
            "semantic_facts": [],
            "friend_id": 1,
            "character_id": 1,
        })
        assert len(result["messages"]) > 1  # 应有 AI 回复
```

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/test_agents.py::TestSupervisorGraph -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add ai/agents/supervisor_graph.py tests/test_agents.py
git commit -m "feat(agents): add Supervisor Graph orchestrating multi-agent flow

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 15: 集成到现有 API + 向后兼容

**Files:**
- Modify: `api/chat.py`
- Modify: `ai/chat_graph.py`
- Modify: `ai/memory_update.py`

- [ ] **Step 1: 更新 api/chat.py — 切换到 SupervisorGraph**

在 `api/chat.py` 中修改 `chat` 函数和 `event_stream` 函数。

首先，修改导入部分。将：
```python
from ai.chat_graph import ChatGraph
from ai.memory_update import update_memory
```

替换为：
```python
from ai.chat_graph import ChatGraph  # 保留兼容
from ai.agents.supervisor_graph import create_supervisor_app
from ai.memory.episodic import write_episodic
from ai.memory.reflection import reflect_memories
from ai.memory_update import update_memory  # 保留兼容
```

然后，修改 `chat` 函数中的 app 创建和 inputs 组装逻辑：

```python
# 原代码 (~line 362-378):
# app = ChatGraph.create_app(...)
# inputs = {"messages": [HumanMessage(message)]}
# inputs = add_system_prompt(inputs, friend)
# inputs = add_recent_messages(inputs, friend)

# 改为:
app = create_supervisor_app(
    friend_id=friend.id,
    character_id=friend.character.id,
    character_name=friend.character.name,
    character_profile=friend.character.profile,
)

# 组装 system prompt (保持与现有 add_system_prompt 一致的逻辑)
system_prompts = SystemPrompt.objects.filter(title="回复").order_by("order_number")
system_text = ""
for sp in system_prompts:
    system_text += sp.prompt
system_text += f"\n【角色性格】\n{friend.character.profile}\n"
system_text += f"【长期记忆】\n{friend.memory}\n"

# 预搜索记忆上下文 (复用现有 _pre_search_memory)
memory_context = _pre_search_memory(message, friend)

# 组装初始消息
messages = [SystemMessage(content=system_text)]
message_raw = list(Message.objects.filter(friend=friend).order_by("-id")[:10])
message_raw.reverse()
for m in message_raw:
    messages.append(HumanMessage(content=m.user_message))
    messages.append(AIMessage(content=m.output))
messages.append(HumanMessage(content=message))

inputs = {
    "messages": messages,
    "intent": "",
    "delegate_to": "",
    "memory_context": memory_context or "",
    "emotion_analysis": None,
    "character_profile": friend.character.profile,
    "semantic_facts": [],
    "friend_id": friend.id,
    "character_id": friend.character.id,
}
```

然后，修改 `event_stream` 函数中保存 Message 之后的逻辑。在保存 Message 之后（`Message.objects.create(...)` 之后），添加记忆写入：

```python
# 在 Message.objects.create(...) 之后添加:

# 写入 Episodic Memory (异步，不阻塞响应)
import threading
threading.Thread(
    target=write_episodic,
    args=(friend, message, full_output),
    daemon=True,
).start()

# 检查是否需要 Reflection (每 10 轮触发)
from django.utils.timezone import now
recent_count = EpisodicMemory.objects.filter(
    friend=friend,
    created_at__gt=friend.last_reflection_time,
).count()
if recent_count >= 10:
    hours_since = (now() - friend.last_reflection_time).total_seconds() / 3600
    if hours_since >= 1:
        threading.Thread(
            target=reflect_memories,
            args=(friend,),
            kwargs={"force": False},
            daemon=True,
        ).start()
```

同时更新 `update_memory` 调用逻辑，改为使用新的反射机制：

```python
# 原: if Message.objects.filter(friend=friend).count() % 1 == 0:
#         update_memory(friend)
# 改为: if Message.objects.filter(friend=friend).count() % 10 == 0:
#         threading.Thread(target=reflect_memories, args=(friend,), daemon=True).start()
```

然后在文件顶部添加缺少的 import：
```python
from web.models.memory import EpisodicMemory  # 新增
```

**注意**: 流式输出部分 (`tts_sender`, `tts_receiver`, `event_stream` 生成器) 保持不变。`event_stream` 中 `work()` 使用的 `app` 现在是 supervisor_app。

- [ ] **Step 2: 更新 ai/chat_graph.py — 薄封装**

```python
# ai/chat_graph.py — 添加向后兼容的封装
# 在文件末尾添加:

# 保留 create_app 静态方法，内部重定向
from ai.agents.supervisor_graph import create_supervisor_app

# 将原来的 ChatGraph.create_app 替换为:
class ChatGraph:
    @staticmethod
    def create_app(character_id: int | None = None, character_name: str = "", chat_sender_name: str = ""):
        """向后兼容 — 内部调用新的 Supervisor Graph"""
        # 新架构不再需要 chat_sender_name 参数，但保留以兼容调用方
        return create_supervisor_app(
            character_id=character_id,
            character_name=character_name,
            character_profile="",  # 由 api/chat.py 的 add_system_prompt 提供
        )
```

- [ ] **Step 3: 更新 ai/memory_update.py — 薄封装**

```python
# ai/memory_update.py — 修改 update_memory 函数

from ai.memory.reflection import reflect_memories

def update_memory(friend):
    """向后兼容 — 使用新的反射机制"""
    reflect_memories(friend, force=True)
```

- [ ] **Step 4: 验证导入无误**

```bash
uv run python -c "
import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'django_settings')
import django; django.setup()

from ai.agents.supervisor_graph import create_supervisor_app
print('SupervisorGraph import OK')

from ai.chat_graph import ChatGraph
print('ChatGraph import OK')

from ai.memory.episodic import write_episodic
print('Episodic import OK')

from ai.memory.reflection import reflect_memories
print('Reflection import OK')

from web.models.memory import EpisodicMemory, SemanticMemory
print('Models import OK')
"
```

Expected: All "OK"

- [ ] **Step 5: 运行全量测试**

```bash
uv run pytest tests/ -v
```

Expected: 所有测试 PASS

- [ ] **Step 6: Commit**

```bash
git add api/chat.py ai/chat_graph.py ai/memory_update.py
git commit -m "feat: integrate Supervisor Graph into API, add episodic write on chat

- api/chat.py: switch to create_supervisor_app, add EpisodicMemory write + reflection trigger
- ai/chat_graph.py: thin wrapper for backward compatibility
- ai/memory_update.py: redirect to new reflection mechanism

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 16: 端到端验证

- [ ] **Step 1: 启动服务验证**

```bash
# 手动启动服务
uv run uvicorn main:app --port 8000 &
sleep 2
# 测试 health check
curl -s http://localhost:8000/ | head -1
# Expected: HTML (SPA fallback)
kill %1
```

- [ ] **Step 2: 运行完整测试套件**

```bash
uv run pytest tests/ -v --tb=short
```

- [ ] **Step 3: 验证现有 API 兼容性**

```bash
uv run python -c "
import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'django_settings')
import django; django.setup()

# 验证所有 router 可导入
from api.auth import router as auth_router
from api.user import router as user_router
from api.character import router as character_router
from api.friend import router as friend_router
from api.chat import router as chat_router
from api.message import router as message_router
from api.asr import router as asr_router
from api.homepage import router as homepage_router
from api.import_data import router as import_router
from api.voice import router as voice_router
print('All routers OK')
"
```

- [ ] **Step 4: Commit (如有修改)**

```bash
git add -A
git diff --cached --stat
git commit -m "test: add end-to-end verification for integration

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## 总结

| 阶段 | Tasks | 新增文件 | 修改文件 |
|------|-------|---------|---------|
| RAG 管道 | 2-6 | 5 (ai/rag/*) | 1 (tests) |
| 分层记忆 | 7-10 | 7 (models, ai/memory/*) | 3 (models/__init__, friend, tests) |
| Multi-Agent | 11-14 | 5 (ai/agents/*) | 1 (tests) |
| 集成 | 15-16 | 0 | 3 (api/chat, ai/chat_graph, ai/memory_update) |

**总计: 16 个 Task, ~17 个新文件, ~8 个修改文件。**
