# ai/memory/semantic.py
import json
import os
from pathlib import Path

import lancedb
from langchain_community.vectorstores import LanceDB

from ai.custom_embeddings import CustomEmbeddings
from web.models.friend import Friend
from web.models.memory import SemanticMemory

_STORAGE_DIR = str(Path(__file__).resolve().parent.parent / "documents" / "lancedb_storage")


def get_active_facts(friend_id: int, category: str | None = None) -> list[dict]:
    """获取所有活跃的语义记忆事实"""
    qs = SemanticMemory.objects.filter(friend_id=friend_id, is_active=True)
    if category:
        qs = qs.filter(category=category)
    return [
        {"id": f.id, "fact": f.fact, "category": f.category, "confidence": f.confidence,
         "source": f.source, "is_locked": f.is_locked, "created_at": f.created_at}
        for f in qs.order_by("-confidence")
    ]


def _index_fact(friend_id: int, fact: str):
    table_name = f"semantic_{friend_id}"
    try:
        db = lancedb.connect(_STORAGE_DIR)
        LanceDB.from_texts([fact], CustomEmbeddings(), connection=db, table_name=table_name, mode="append")
    except Exception:
        pass


def add_fact(friend: Friend, fact: str, category: str = "preference", confidence: float = 0.5,
             evidence: str = "", source: str = "ai", is_locked: bool = False):
    """添加新事实，同时向量化到 LanceDB"""
    sm = SemanticMemory.objects.create(
        friend=friend, fact=fact, category=category, confidence=confidence, evidence=evidence,
        source=source, is_locked=is_locked,
    )
    _index_fact(friend.id, fact)
    return sm


def resolve_conflict(friend_id: int, old_fact: str, new_fact: str):
    """冲突解决：标记旧事实为 inactive，创建新事实"""
    old_sm = SemanticMemory.objects.filter(friend_id=friend_id, fact=old_fact, is_active=True).first()
    if old_sm and old_sm.is_locked:
        new_sm = SemanticMemory.objects.create(
            friend_id=friend_id, fact=new_fact, category=old_sm.category,
            confidence=0.6, evidence=f"Potential conflict with user-locked fact: {old_fact}",
        )
        _index_fact(friend_id, new_fact)
        return new_sm
    new_sm = SemanticMemory.objects.create(
        friend_id=friend_id, fact=new_fact,
        category=old_sm.category if old_sm else "other", confidence=0.6,
        evidence=f"Updated from: {old_fact}",
    )
    if old_sm:
        old_sm.is_active = False
        old_sm.replaced_by = new_sm
        old_sm.save(update_fields=["is_active", "replaced_by", "updated_at"])
    _index_fact(friend_id, new_fact)
    return new_sm


def search_semantic(friend_id: int, query: str, top_k: int = 10) -> list[dict]:
    """搜索 Semantic Memory — 关键词 + 语义双路检索。

    关键词匹配：jieba 分词后扫描 fact 字段，精确命中词条
    语义匹配：LanceDB 向量搜索，找含义相近的事实
    合并去重后返回，关键词命中优先。
    """
    import re

    table_name = f"semantic_{friend_id}"
    results: dict[int, dict] = {}  # id -> result, 用于去重合并

    # 1. 关键词搜索（优先）
    import jieba
    keywords: list[str] = []
    for word in jieba.cut(query):
        word = word.strip()
        if len(word) >= 2 and re.search(r"[一-鿿]", word):
            keywords.append(word)
    keywords.extend(re.findall(r"[a-zA-Z]{3,}", query))
    keywords = list(dict.fromkeys(keywords))  # 去重保持顺序

    if keywords:
        for kw in keywords[:5]:  # 最多 5 个关键词
            qs = SemanticMemory.objects.filter(
                friend_id=friend_id, is_active=True,
                fact__icontains=kw,
            )[:3]
            for sm in qs:
                if sm.id not in results:
                    results[sm.id] = {
                        "id": sm.id, "fact": sm.fact, "category": sm.category,
                        "confidence": sm.confidence, "source": "keyword",
                        "is_locked": sm.is_locked, "score": 1.0,
                    }

    # 2. 语义搜索（补充）
    try:
        db = lancedb.connect(_STORAGE_DIR)
        if table_name in db.list_tables():
            vdb = LanceDB(connection=db, embedding=CustomEmbeddings(), table_name=table_name)
            docs = vdb.similarity_search_with_score(query, k=top_k)
            for doc, score in docs:
                sm = SemanticMemory.objects.filter(
                    friend_id=friend_id, fact=doc.page_content, is_active=True
                ).first()
                if sm and sm.id not in results:
                    results[sm.id] = {
                        "id": sm.id, "fact": sm.fact, "category": sm.category,
                        "confidence": sm.confidence, "source": "semantic",
                        "is_locked": sm.is_locked, "score": float(score),
                    }
    except Exception:
        pass

    # 关键词命中排前，语义排后
    sorted_results = sorted(results.values(), key=lambda r: r["score"], reverse=True)
    return sorted_results[:top_k]


CATEGORY_LABELS = {
    "identity": "身份",
    "preference": "偏好",
    "experience": "经历",
    "relationship": "互动规律",
}


def sync_friend_memory_cache(friend: Friend):
    """将语义记忆同步到 Friend.memory 字段作为缓存，按分类分组输出。"""
    facts = get_active_facts(friend.id)
    grouped: dict[str, list[str]] = {}
    for f in facts[:20]:
        cat = f["category"]
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append(f"- {f['fact']}")

    parts: list[str] = []
    for cat in ("identity", "preference", "experience", "relationship"):
        if cat in grouped:
            label = CATEGORY_LABELS.get(cat, cat)
            parts.append(f"### {label}\n" + "\n".join(grouped[cat]))

    cache = "\n\n".join(parts) if parts else ""
    friend.memory = cache[:5000]
    friend.save(update_fields=["memory"])
