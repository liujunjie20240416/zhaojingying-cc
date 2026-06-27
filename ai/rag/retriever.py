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
                        "score": 1.0,
                    })
            except Exception:
                pass

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
            if table_name not in db.list_tables():
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
        """基于 Jaccard 相似度去重，保留 score 最高的"""
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
        use_hyde: bool = False,
        use_rewriter: bool = False,
    ) -> list[dict]:
        """混合检索主入口 — FTS5 + LanceDB 并行，去重后返回 top_k 结果"""
        queries = self.rewriter.rewrite(query) if use_rewriter else [query]
        hyde_doc = self.hyde.generate(query) if use_hyde else ""

        all_results: list[dict] = []

        fts_results = self.fts5_search(query, character_id, limit=10)
        all_results.extend(fts_results)

        search_queries = [query]
        if hyde_doc:
            search_queries.append(hyde_doc)
        search_queries.extend(queries[:2])

        seen_contents = set()
        for sq in search_queries:
            lance_results = self.lancedb_search(sq, character_id, k=5)
            for r in lance_results:
                content_key = r["content"][:100]
                if content_key not in seen_contents:
                    seen_contents.add(content_key)
                    all_results.append(r)

        all_results = self._deduplicate(all_results)
        all_results.sort(key=lambda d: d.get("score", 0), reverse=True)
        return all_results[:top_k]
