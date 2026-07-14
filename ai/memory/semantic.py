# ai/memory/semantic.py
import json
import logging
import os
from pathlib import Path
import uuid

import lancedb
from django.utils.timezone import now
from langchain_community.vectorstores import LanceDB

from ai.custom_embeddings import CustomEmbeddings
from ai.rag.scoring import lance_distance_to_relevance
from web.models.friend import Friend
from web.models.memory import MemoryEvidence, SemanticMemory

_STORAGE_DIR = str(Path(__file__).resolve().parent.parent / "documents" / "lancedb_storage")
logger = logging.getLogger(__name__)


SUBJECT_LABELS = {
    "user": "用户",
    "girlfriend": "女友",
    "relationship": "两人关系",
}

CATEGORY_LABELS = {
    "identity": "身份",
    "preference": "偏好",
    "experience": "经历",
    "relationship": "互动规律",
}

VALID_SUBJECTS = set(SUBJECT_LABELS)
VALID_CATEGORIES = set(CATEGORY_LABELS)
VALID_MEMORY_STATES = {"current", "historical", "superseded"}


def _table_names(db) -> set[str]:
    listing = db.list_tables()
    return set(getattr(listing, "tables", listing))


def default_mutability(subject: str, category: str, source: str = "ai") -> bool:
    """判断一条记忆默认是否允许被后续 reflection 更新。"""
    if subject == "girlfriend":
        return False
    if category in ("identity", "experience"):
        return False
    if subject == "relationship" and source == "import":
        return False
    if category in ("preference", "relationship"):
        return True
    return False


def _normalize_subject(subject: str) -> str:
    return subject if subject in VALID_SUBJECTS else "user"


def _normalize_category(category: str) -> str:
    return category if category in VALID_CATEGORIES else "preference"


def get_active_facts(
    friend_id: int,
    category: str | None = None,
    subject: str | None = None,
    include_historical: bool = True,
) -> list[dict]:
    """获取所有活跃的语义记忆事实"""
    qs = SemanticMemory.objects.filter(friend_id=friend_id, is_active=True)
    if not include_historical:
        qs = qs.filter(memory_state="current")
    if category:
        qs = qs.filter(category=category)
    if subject:
        qs = qs.filter(subject=subject)
    return [
        {"id": f.id, "fact": f.fact, "subject": f.subject, "category": f.category,
         "confidence": f.confidence, "source": f.source, "is_locked": f.is_locked,
         "is_mutable": f.is_mutable, "memory_state": f.memory_state,
         "valid_from": f.valid_from, "valid_to": f.valid_to, "created_at": f.created_at}
        for f in qs.order_by("memory_state", "-confidence")
    ]


def _index_fact(friend_id: int, fact: str, memory_id: int | None = None):
    table_name = f"semantic_{friend_id}"
    try:
        db = lancedb.connect(_STORAGE_DIR)
        LanceDB.from_texts(
            [fact], CustomEmbeddings(),
            metadatas=[{"memory_id": str(memory_id)}] if memory_id is not None else None,
            connection=db, table_name=table_name, mode="append",
        )
        return True
    except Exception:
        logger.exception("Failed to append SemanticMemory %s to %s", memory_id, table_name)
        return False


def index_semantic_memory(memory: SemanticMemory) -> bool:
    """Append or refresh one active Semantic Memory vector."""
    if not memory.is_active:
        return False
    return _index_fact(memory.friend_id, memory.fact, memory.id)


def rebuild_semantic_index(friend_id: int) -> bool:
    """Safely rebuild the vector projection from active SQLite facts.

    SQLite remains authoritative. A failed embedding/build keeps the previous
    LanceDB table available and returns False instead of silently deleting it.
    """
    table_name = f"semantic_{friend_id}"
    temp_name = f"{table_name}__rebuild_{uuid.uuid4().hex[:10]}"
    memories = list(SemanticMemory.objects.filter(
        friend_id=friend_id, is_active=True
    ).order_by("id").values("id", "fact"))
    db = None
    try:
        db = lancedb.connect(_STORAGE_DIR)
        table_names = _table_names(db)
        if not memories:
            if table_name in table_names:
                db.drop_table(table_name)
            return True
        LanceDB.from_texts(
            [memory["fact"] for memory in memories], CustomEmbeddings(),
            metadatas=[{"memory_id": str(memory["id"])} for memory in memories],
            connection=db, table_name=temp_name,
        )
        temp_table = db.open_table(temp_name)
        if temp_table.count_rows() != len(memories):
            raise RuntimeError("Semantic index row count mismatch")
        # LanceDB OSS does not implement rename_table. Build and validate the
        # full projection under a temporary name first, then replace the live
        # table from the already-embedded Arrow data in one local operation.
        db.create_table(table_name, data=temp_table.to_arrow(), mode="overwrite")
        if db.open_table(table_name).count_rows() != len(memories):
            raise RuntimeError("Live semantic index row count mismatch")
        db.drop_table(temp_name)
        return True
    except Exception:
        if db is not None:
            try:
                if temp_name in _table_names(db):
                    db.drop_table(temp_name)
            except Exception:
                logger.exception("Failed to clean temporary semantic index %s", temp_name)
        logger.exception("Failed to rebuild semantic index %s; previous index retained", table_name)
        return False


def delete_semantic_index_entries(friend_id: int, memory_ids: list[int]) -> bool:
    """Delete selected Semantic Memory vectors without re-embedding kept facts."""
    normalized_ids = sorted({int(memory_id) for memory_id in memory_ids})
    if not normalized_ids:
        return True
    table_name = f"semantic_{friend_id}"
    try:
        db = lancedb.connect(_STORAGE_DIR)
        if table_name not in _table_names(db):
            return True
        values = ", ".join(f"'{memory_id}'" for memory_id in normalized_ids)
        db.open_table(table_name).delete(f"metadata.memory_id IN ({values})")
        return True
    except Exception:
        # SQLite remains authoritative. search_semantic resolves every vector
        # back to an active DB row, so a stale vector cannot resurrect a
        # deleted fact; maintenance can retry storage cleanup later.
        logger.exception(
            "Failed to delete Semantic Memory ids %s from %s",
            normalized_ids,
            table_name,
        )
        return False


def add_memory_evidence(
    memory: SemanticMemory,
    *,
    source_type: str,
    message_refs: list[int] | None = None,
    excerpt: str = "",
    chat_day=None,
) -> MemoryEvidence:
    """Attach normalized, deduplicated provenance to a semantic fact."""
    refs = sorted({int(ref) for ref in (message_refs or []) if str(ref).isdigit()})
    start_ref = refs[0] if refs else None
    end_ref = refs[-1] if refs else None
    existing = MemoryEvidence.objects.filter(
        memory=memory,
        source_type=source_type,
        start_message_ref=start_ref,
        end_message_ref=end_ref,
    ).first()
    if existing:
        merged_refs = sorted(set(existing.message_refs or []) | set(refs))
        changed = []
        if merged_refs != existing.message_refs:
            existing.message_refs = merged_refs
            changed.append("message_refs")
        if excerpt and not existing.excerpt:
            existing.excerpt = excerpt[:2000]
            changed.append("excerpt")
        if chat_day and not existing.chat_day:
            existing.chat_day = chat_day
            changed.append("chat_day")
        if changed:
            existing.save(update_fields=changed)
        return existing
    return MemoryEvidence.objects.create(
        memory=memory,
        source_type=source_type,
        message_refs=refs,
        start_message_ref=start_ref,
        end_message_ref=end_ref,
        excerpt=excerpt[:2000],
        chat_day=chat_day,
    )


def add_fact(
    friend: Friend,
    fact: str,
    category: str = "preference",
    confidence: float = 0.5,
    evidence: str = "",
    source: str = "ai",
    is_locked: bool | None = None,
    subject: str = "user",
    is_mutable: bool | None = None,
    memory_state: str = "current",
    valid_from=None,
    valid_to=None,
    index: bool = True,
):
    """添加新事实，同时向量化到 LanceDB"""
    subject = _normalize_subject(subject)
    category = _normalize_category(category)
    if is_mutable is None:
        is_mutable = default_mutability(subject, category, source)
    if is_locked is None:
        is_locked = not is_mutable
    if memory_state not in VALID_MEMORY_STATES:
        memory_state = "current"
    if valid_from is None and memory_state == "current":
        valid_from = now()
    sm = SemanticMemory.objects.create(
        friend=friend, fact=fact, subject=subject, category=category,
        confidence=confidence, evidence=evidence, source=source,
        is_locked=is_locked, is_mutable=is_mutable,
        memory_state=memory_state, valid_from=valid_from, valid_to=valid_to,
    )
    if index:
        _index_fact(friend.id, fact, sm.id)
    return sm


def resolve_conflict(
    friend_id: int,
    old_fact: str,
    new_fact: str,
    *,
    index: bool = True,
):
    """冲突解决：旧事实转为历史状态，新事实成为当前状态。"""
    old_sm = SemanticMemory.objects.filter(friend_id=friend_id, fact=old_fact, is_active=True).first()
    if old_sm and (old_sm.is_locked or not old_sm.is_mutable):
        new_sm = SemanticMemory.objects.create(
            friend_id=friend_id, fact=new_fact, subject=old_sm.subject,
            category=old_sm.category, confidence=0.6,
            evidence=f"Potential conflict with locked fact: {old_fact}",
            is_mutable=old_sm.is_mutable, is_locked=old_sm.is_locked,
            memory_state="current", valid_from=now(),
        )
        if index:
            _index_fact(friend_id, new_fact, new_sm.id)
        return new_sm
    current_time = now()
    new_sm = SemanticMemory.objects.create(
        friend_id=friend_id, fact=new_fact,
        subject=old_sm.subject if old_sm else "user",
        category=old_sm.category if old_sm else "preference", confidence=0.6,
        evidence=f"Updated from: {old_fact}",
        is_mutable=old_sm.is_mutable if old_sm else True,
        memory_state="current", valid_from=current_time,
    )
    if old_sm:
        old_sm.memory_state = "historical"
        old_sm.valid_to = current_time
        old_sm.replaced_by = new_sm
        old_sm.save(update_fields=["memory_state", "valid_to", "replaced_by", "updated_at"])
    if index:
        _index_fact(friend_id, new_fact, new_sm.id)
    return new_sm


def search_semantic(
    friend_id: int,
    query: str,
    top_k: int = 10,
    *,
    include_imported: bool = True,
) -> list[dict]:
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
            )
            if not include_imported:
                qs = qs.exclude(source="import")
            qs = qs.order_by("memory_state", "-confidence")[:5]
            for sm in qs:
                if sm.id not in results:
                    results[sm.id] = {
                        "id": sm.id, "fact": sm.fact, "category": sm.category,
                        "confidence": sm.confidence, "source": "keyword",
                        "subject": sm.subject, "is_locked": sm.is_locked,
                        "is_mutable": sm.is_mutable, "memory_state": sm.memory_state,
                        "score": 1.0 if sm.memory_state == "current" else 0.55,
                    }

    # 2. 语义搜索（补充）
    try:
        db = lancedb.connect(_STORAGE_DIR)
        if table_name in db.list_tables():
            vdb = LanceDB(connection=db, embedding=CustomEmbeddings(), table_name=table_name)
            docs = vdb.similarity_search_with_score(query, k=top_k)
            for doc, score in docs:
                memory_id = (doc.metadata or {}).get("memory_id")
                sm_query = SemanticMemory.objects.filter(
                    id=memory_id, friend_id=friend_id, is_active=True
                ).first() if memory_id else SemanticMemory.objects.filter(
                    friend_id=friend_id, fact=doc.page_content, is_active=True
                ).first()
                sm = sm_query
                if sm and not include_imported and sm.source == "import":
                    sm = None
                if sm and sm.id not in results:
                    results[sm.id] = {
                        "id": sm.id, "fact": sm.fact, "category": sm.category,
                        "confidence": sm.confidence, "source": "semantic",
                        "subject": sm.subject, "is_locked": sm.is_locked,
                        "is_mutable": sm.is_mutable, "memory_state": sm.memory_state,
                        "score": (
                            lance_distance_to_relevance(score)
                            if sm.memory_state == "current"
                            else lance_distance_to_relevance(score) * 0.6
                        ),
                    }
    except Exception:
        pass

    # 关键词命中排前，语义排后
    sorted_results = sorted(
        results.values(),
        key=lambda r: (r.get("memory_state") == "current", r["score"]),
        reverse=True,
    )
    return sorted_results[:top_k]


def sync_friend_memory_cache(friend: Friend):
    """将语义记忆同步到 Friend.memory 字段作为缓存，按主体和分类分组输出。"""
    facts = get_active_facts(friend.id, include_historical=False)
    grouped: dict[str, dict[str, list[str]]] = {}
    per_bucket_limit = 4
    for f in facts:
        subject = f["subject"]
        cat = f["category"]
        bucket = grouped.setdefault(subject, {}).setdefault(cat, [])
        if len(bucket) < per_bucket_limit:
            bucket.append(f"- {f['fact']}")

    parts: list[str] = []
    for subject in ("user", "girlfriend", "relationship"):
        if subject not in grouped:
            continue
        subject_parts = []
        for cat in ("identity", "preference", "experience", "relationship"):
            if cat in grouped[subject]:
                label = CATEGORY_LABELS.get(cat, cat)
                subject_parts.append(f"### {label}\n" + "\n".join(grouped[subject][cat]))
        if subject_parts:
            parts.append(f"## {SUBJECT_LABELS[subject]}\n" + "\n\n".join(subject_parts))

    cache = "\n\n".join(parts) if parts else ""
    friend.memory = cache[:5000]
    friend.save(update_fields=["memory"])
