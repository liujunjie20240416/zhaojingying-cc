"""Unified raw conversation retrieval across Imported Chat and Online Chat."""

import logging
import re
import uuid
from pathlib import Path

import jieba
import lancedb
from django.db.models import Q
from django.utils.timezone import localtime
from langchain_community.vectorstores import LanceDB

from ai.custom_embeddings import CustomEmbeddings
from ai.rag.retriever import HybridRetriever
from ai.rag.scoring import lance_distance_to_relevance
from web.models.chat_message import ChatMessage
from web.models.friend import Message

logger = logging.getLogger(__name__)
_STORAGE_DIR = str(Path(__file__).resolve().parent.parent / "documents" / "lancedb_storage")
_ONLINE_TABLE_PREFIX = "online_"
_QUERY_STOP_WORDS = {
    "记得", "以前", "时候", "什么", "怎么", "我们", "那个", "事情",
    "是不是", "有没有", "后来", "现在", "当时", "聊天", "说过",
}


def _table_names(db) -> set[str]:
    listing = db.list_tables()
    return set(getattr(listing, "tables", listing))


def _query_terms(query: str) -> list[str]:
    terms = []
    for word in jieba.cut(query or ""):
        word = word.strip()
        if len(word) >= 2 and word not in _QUERY_STOP_WORDS:
            terms.append(word)
    terms.extend(re.findall(r"[a-zA-Z0-9]{3,}", query or ""))
    return list(dict.fromkeys(terms))[:8]


def _char_bigrams(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", text or "")
    return {compact[index:index + 2] for index in range(max(0, len(compact) - 1))}


def _lexical_relevance(query: str, content: str, terms: list[str]) -> float:
    if query and query in content:
        return 1.0
    term_score = min(0.8, sum(0.2 for term in terms if term in content))
    query_grams = _char_bigrams(query)
    content_grams = _char_bigrams(content)
    overlap = (
        len(query_grams & content_grams) / len(query_grams)
        if query_grams else 0.0
    )
    return max(term_score, overlap * 0.7)


def _online_content(message: Message) -> str:
    timestamp = localtime(message.create_time).isoformat()
    return (
        f"[{timestamp}] 用户：{message.user_message}\n"
        f"[{timestamp}] AI：{message.output}"
    )


class ImportedChatAdapter:
    """Search Character-scoped imported raw chat and expand matched windows."""

    def __init__(self, api_key: str = "", api_base: str = ""):
        self.retriever = HybridRetriever(api_key, api_base)

    def search(
        self,
        query: str,
        character_id: int,
        *,
        time_scope: tuple[int, int] | None = None,
        top_k: int = 12,
    ) -> list[dict]:
        if not character_id or not ChatMessage.objects.filter(character_id=character_id).exists():
            return []
        candidates = self.retriever.hybrid_search(
            query, character_id, top_k=top_k, use_hyde=False
        )
        hits = []
        for candidate in candidates:
            rowid = candidate.get("rowid")
            if rowid:
                message = ChatMessage.objects.filter(
                    id=rowid, character_id=character_id
                ).first()
                if not message:
                    continue
                if time_scope and not (
                    time_scope[0] <= message.msg_index <= time_scope[1]
                ):
                    continue
                start = max(0, message.msg_index - 5)
                end = message.msg_index + 5
                window = ChatMessage.objects.filter(
                    character_id=character_id,
                    msg_index__gte=start,
                    msg_index__lte=end,
                )
                if time_scope:
                    window = window.filter(
                        msg_index__gte=time_scope[0], msg_index__lte=time_scope[1]
                    )
                window = list(window.order_by("msg_index"))
                content = "\n".join(
                    f"[{item.timestamp}] {item.sender}：{item.content}"
                    for item in window
                )
                hits.append({
                    "source_type": "import_chat",
                    "message_refs": [item.msg_index for item in window],
                    "timestamp": message.timestamp,
                    "content": content,
                    "score": float(candidate.get("score", 0)),
                })
            else:
                hits.append({
                    "source_type": "import_chat",
                    "message_refs": [],
                    "timestamp": "",
                    "content": candidate.get("content", ""),
                    "score": float(candidate.get("score", 0)),
                })
        return hits


class OnlineChatAdapter:
    """Search Friend-scoped Online Chat with lexical and vector retrieval."""

    def __init__(self):
        self.embeddings = CustomEmbeddings()

    def search(self, query: str, friend_id: int, *, top_k: int = 12) -> list[dict]:
        if not friend_id or not Message.objects.filter(friend_id=friend_id).exists():
            return []
        terms = _query_terms(query)
        candidate_map: dict[int, dict] = {}

        if terms:
            exact_filter = Q()
            for term in terms:
                exact_filter |= Q(user_message__icontains=term) | Q(output__icontains=term)
            exact_messages = Message.objects.filter(
                exact_filter, friend_id=friend_id
            ).order_by("-id")[:100]
        else:
            exact_messages = []

        # Fuzzy fallback is bounded; exact term queries above still cover all history.
        recent_messages = Message.objects.filter(friend_id=friend_id).order_by("-id")[:1000]
        for message in list(exact_messages) + list(recent_messages):
            if message.id in candidate_map:
                continue
            content = _online_content(message)
            score = _lexical_relevance(query, content, terms)
            if score < 0.12:
                continue
            candidate_map[message.id] = {
                "source_type": "online_chat",
                "message_refs": [message.id],
                "timestamp": localtime(message.create_time).isoformat(),
                "content": content,
                "score": score,
            }

        table_name = f"{_ONLINE_TABLE_PREFIX}{friend_id}"
        try:
            db = lancedb.connect(_STORAGE_DIR)
            if table_name in _table_names(db):
                store = LanceDB(
                    connection=db, embedding=self.embeddings, table_name=table_name
                )
                for doc, distance in store.similarity_search_with_score(query, k=top_k):
                    message_id = (doc.metadata or {}).get("message_id")
                    try:
                        message_id = int(message_id)
                    except (TypeError, ValueError):
                        continue
                    message = Message.objects.filter(
                        id=message_id, friend_id=friend_id
                    ).first()
                    if not message:
                        continue
                    score = lance_distance_to_relevance(distance)
                    existing = candidate_map.get(message.id)
                    if existing:
                        existing["score"] = max(existing["score"], score)
                    else:
                        candidate_map[message.id] = {
                            "source_type": "online_chat",
                            "message_refs": [message.id],
                            "timestamp": localtime(message.create_time).isoformat(),
                            "content": _online_content(message),
                            "score": score,
                        }
        except Exception:
            logger.exception("Online Chat vector search failed for friend %s", friend_id)

        return sorted(
            candidate_map.values(), key=lambda item: item["score"], reverse=True
        )[:top_k]


class ConversationHistorySearch:
    """Deep Module presenting one search Interface over both raw chat stores."""

    def __init__(self, api_key: str = "", api_base: str = ""):
        self.imported = ImportedChatAdapter(api_key, api_base)
        self.online = OnlineChatAdapter()

    def search(
        self,
        queries: list[str],
        *,
        friend_id: int,
        character_id: int | None,
        imported_time_scope: tuple[int, int] | None = None,
        top_k: int = 20,
    ) -> list[dict]:
        hits: dict[tuple, dict] = {}
        for query in list(dict.fromkeys(queries))[:3]:
            imported_hits = self.imported.search(
                query,
                character_id or 0,
                time_scope=imported_time_scope,
                top_k=12,
            )
            online_hits = self.online.search(query, friend_id, top_k=12)
            for hit in imported_hits + online_hits:
                key = (
                    hit["source_type"],
                    tuple(hit.get("message_refs") or []),
                    hit.get("content", "")[:100],
                )
                existing = hits.get(key)
                if not existing or hit.get("score", 0) > existing.get("score", 0):
                    hits[key] = hit
        return sorted(
            hits.values(), key=lambda item: item.get("score", 0), reverse=True
        )[:top_k]


def index_online_message(message_id: int) -> bool:
    """Append one Online Chat turn to its Friend-scoped vector projection."""
    message = Message.objects.filter(id=message_id).first()
    if not message:
        return False
    try:
        db = lancedb.connect(_STORAGE_DIR)
        LanceDB.from_texts(
            [_online_content(message)],
            CustomEmbeddings(),
            metadatas=[{"message_id": str(message.id)}],
            connection=db,
            table_name=f"{_ONLINE_TABLE_PREFIX}{message.friend_id}",
            mode="append",
        )
        return True
    except Exception:
        logger.exception("Failed to index Online Chat message %s", message_id)
        return False


def rebuild_online_history_index(friend_id: int) -> bool:
    """Rebuild the vector projection from all retained Online Chat rows."""
    messages = list(Message.objects.filter(friend_id=friend_id).order_by("id"))
    table_name = f"{_ONLINE_TABLE_PREFIX}{friend_id}"
    temp_name = f"{table_name}__rebuild_{uuid.uuid4().hex[:10]}"
    backup_name = f"{table_name}__backup_{uuid.uuid4().hex[:10]}"
    db = None
    try:
        db = lancedb.connect(_STORAGE_DIR)
        existing_names = _table_names(db)
        if not messages:
            if table_name in existing_names:
                db.drop_table(table_name)
            return True
        LanceDB.from_texts(
            [_online_content(message) for message in messages],
            CustomEmbeddings(),
            metadatas=[{"message_id": str(message.id)} for message in messages],
            connection=db,
            table_name=temp_name,
        )
        if db.open_table(temp_name).count_rows() != len(messages):
            raise RuntimeError("Online Chat index row count mismatch")
        if table_name in existing_names:
            db.rename_table(table_name, backup_name)
        try:
            db.rename_table(temp_name, table_name)
        except Exception:
            if backup_name in _table_names(db):
                db.rename_table(backup_name, table_name)
            raise
        if backup_name in _table_names(db):
            db.drop_table(backup_name)
        return True
    except Exception:
        if db is not None:
            try:
                if temp_name in _table_names(db):
                    db.drop_table(temp_name)
            except Exception:
                logger.exception("Failed to clean Online Chat temporary index")
        logger.exception("Failed to rebuild Online Chat index for friend %s", friend_id)
        return False


def drop_online_history_index(friend_id: int) -> None:
    table_name = f"{_ONLINE_TABLE_PREFIX}{friend_id}"
    try:
        db = lancedb.connect(_STORAGE_DIR)
        if table_name in _table_names(db):
            db.drop_table(table_name)
    except Exception:
        logger.exception("Failed to drop Online Chat index for friend %s", friend_id)
