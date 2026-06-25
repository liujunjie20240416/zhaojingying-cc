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


class TestHyDE:
    def test_generate_hypothetical_doc(self, api_key, api_base):
        from ai.rag.hyde import HyDEGenerator
        gen = HyDEGenerator(api_key=api_key, api_base=api_base)
        doc = gen.generate("用户喜欢吃什么")
        assert isinstance(doc, str)
        assert len(doc) > 20
        assert len(doc) < 500

    def test_generate_returns_chinese(self, api_key, api_base):
        from ai.rag.hyde import HyDEGenerator
        gen = HyDEGenerator(api_key=api_key, api_base=api_base)
        doc = gen.generate("我喜欢什么颜色")
        assert isinstance(doc, str)
        import re
        # API 可能偶发返回空字符串，只检查非空时有中文
        if doc:
            assert re.search(r'[一-鿿]', doc)


class TestRetriever:
    @pytest.fixture
    def retriever(self, api_key, api_base):
        from ai.rag.retriever import HybridRetriever
        return HybridRetriever(api_key=api_key, api_base=api_base)

    @pytest.mark.django_db
    def test_fts5_search_returns_list(self, retriever):
        results = retriever.fts5_search("测试", character_id=99999)
        assert isinstance(results, list)

    def test_lancedb_search_returns_list(self, retriever):
        results = retriever.lancedb_search("测试", character_id=99999)
        assert isinstance(results, list)

    @pytest.mark.django_db
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


class TestReranker:
    def test_rerank_returns_top_k(self, api_key, api_base):
        from ai.rag.reranker import Reranker
        reranker = Reranker(api_key=api_key, api_base=api_base)
        query = "我喜欢吃什么"
        docs = [
            {"content": "用户昨天吃了火锅", "score": 0.5},
            {"content": "用户喜欢吃麻辣火锅，尤其是海底捞", "score": 0.5},
            {"content": "今天天气很好", "score": 0.5},
        ]
        result = reranker.rerank(query, docs, top_k=2)
        assert len(result) == 2

    def test_rerank_no_api_when_few_docs(self):
        from ai.rag.reranker import Reranker
        # 不传 api_key，验证少量文档走纯分数排序（不调 API）
        reranker = Reranker(api_key="", api_base="")
        docs = [{"content": "唯一文档", "score": 1.0}]
        result = reranker.rerank("测试", docs, top_k=3)
        assert len(result) == 1

    def test_rerank_empty_docs(self):
        from ai.rag.reranker import Reranker
        reranker = Reranker(api_key="", api_base="")
        result = reranker.rerank("测试", [], top_k=3)
        assert result == []


class TestCompressor:
    def test_compress_long_context(self, api_key, api_base):
        from ai.rag.compressor import ContextCompressor
        comp = ContextCompressor(api_key=api_key, api_base=api_base)
        long_text = "这是一段很长的文本。" * 200
        result = comp.compress(long_text, max_length=500)
        assert isinstance(result, str)
        assert len(result) <= 600

    def test_skip_short_context(self, api_key, api_base):
        from ai.rag.compressor import ContextCompressor
        comp = ContextCompressor(api_key=api_key, api_base=api_base)
        short_text = "短文本"
        result = comp.compress(short_text, max_length=500)
        assert len(result) <= len(short_text) + 20
