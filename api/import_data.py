"""
聊天记录导入 API

POST /api/import/wechat/  — 上传微信聊天记录文件，解析后存入 LanceDB + FTS5
"""

import os
import tempfile
import threading
import logging
import uuid
from pathlib import Path

import lancedb
from django.db import connection, transaction
from fastapi import APIRouter, Depends, Form, Query, UploadFile
from langchain_community.vectorstores import LanceDB

from ai.custom_embeddings import CustomEmbeddings
from ai.import_storage import imported_fts_table_name, imported_vector_table_name
from api.deps import get_current_user
from api.errors import ApiError
from api.schemas import ResumeImportRequest
from web.models.character import Character
from web.models.chat_message import ChatMessage
from web.models.import_analysis import ImportAnalysis
from tools.wechat_parser import parse_wechat_txt, format_output_as_chunks

router = APIRouter()
logger = logging.getLogger(__name__)
_active_preprocessing: set[int] = set()
_active_preprocessing_lock = threading.Lock()

# LanceDB 存储目录
_STORAGE_DIR = str(
    Path(__file__).resolve().parent.parent / "ai" / "documents" / "lancedb_storage"
)


def _table_names(db) -> set[str]:
    listing = db.list_tables()
    return set(getattr(listing, "tables", listing))


def _ensure_fts5_table(character_id: int, table_name: str | None = None):
    """为指定角色创建 FTS5 虚拟表（如果不存在）。

    tokens 列存 jieba 分词结果（空格连接）：unicode61 不切中文，整段中文是
    一个 token，MATCH "生日" 永远为空；分词后查询侧 jieba 切出的词才能命中。
    """
    table_name = table_name or imported_fts_table_name(character_id)
    with connection.cursor() as c:
        c.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS "{table_name}"
            USING fts5(sender, content, timestamp, tokens, tokenize='unicode61')
        """)
    return table_name


def _tokenize_chinese(text: str) -> str:
    """jieba 分词（空格连接），供 FTS5 tokens 列使用"""
    import jieba
    return " ".join(word for word in jieba.cut(text or "") if word.strip())


def _sync_fts5_table(character_id: int, table_name: str | None = None) -> str:
    """从 chat_message 全量重建 FTS5 投影（含 jieba 分词列）。

    chat_message 是权威源；FTS 表只是关键词检索的派生缓存，可随时重建。
    先 DROP 旧表：存量表可能是无 tokens 列的单列结构，IF NOT EXISTS 不会升级。
    """
    fts_table = table_name or imported_fts_table_name(character_id)
    with connection.cursor() as c:
        c.execute(f'DROP TABLE IF EXISTS "{fts_table}"')
    _ensure_fts5_table(character_id, table_name=fts_table)
    rows = list(
        ChatMessage.objects.filter(character_id=character_id).order_by("id").values_list(
            "id", "sender", "content", "timestamp"
        )
    )
    with connection.cursor() as c:
        c.execute(f'DELETE FROM "{fts_table}"')
        for start in range(0, len(rows), 500):
            batch = rows[start:start + 500]
            c.executemany(
                f'INSERT INTO "{fts_table}" '
                f"(rowid, sender, content, timestamp, tokens) VALUES (?, ?, ?, ?, ?)",
                [
                    (row_id, sender, content, timestamp, _tokenize_chinese(content))
                    for row_id, sender, content, timestamp in batch
                ],
            )
    return fts_table


def _drop_fts5_table(table_name: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=%s",
            [table_name],
        )
        if cursor.fetchone():
            cursor.execute(f'DROP TABLE "{table_name}"')


def _build_import_texts(messages: list[dict], target_name: str) -> tuple[list[str], int, int]:
    chunks = format_output_as_chunks(messages, target_name, chunk_size=40)
    key_messages = [
        f"[{message['timestamp']}] {message['sender']}: {message['content']}"
        for message in messages
        if len(message["content"]) > 30
    ]
    return chunks + key_messages, len(chunks), len(key_messages)


def _publish_sqlite_import(
    character: Character,
    *,
    target_name: str,
    messages: list[dict],
    version: str,
) -> str:
    """Publish raw Imported Chat, FTS5, and the active pointer atomically."""
    fts_table = imported_fts_table_name(character.id, version)
    with transaction.atomic():
        # Readers on other SQLite connections retain the previous committed
        # import until this transaction commits.
        ChatMessage.objects.filter(character_id=character.id).delete()
        batch: list[ChatMessage] = []
        for index, message in enumerate(messages):
            batch.append(ChatMessage(
                character_id=character.id,
                sender=message["sender"],
                content=message["content"],
                timestamp=message.get("timestamp", ""),
                msg_index=index,
            ))
            if len(batch) >= 500:
                ChatMessage.objects.bulk_create(batch)
                batch.clear()
        if batch:
            ChatMessage.objects.bulk_create(batch)

        _sync_fts5_table(character.id, table_name=fts_table)
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT COUNT(*) FROM "{fts_table}"')
            indexed_count = cursor.fetchone()[0]
        if indexed_count != len(messages):
            raise RuntimeError("Imported Chat FTS5 row count mismatch")

        character.chat_sender_name = target_name
        character.import_data_version = version
        character.save(update_fields=["chat_sender_name", "import_data_version"])
    return fts_table


def _cleanup_old_import_projections(character_id: int, version: str) -> None:
    """Best-effort cleanup after publication; it never changes the live pointer."""
    live_fts = imported_fts_table_name(character_id, version)
    live_vector = imported_vector_table_name(character_id, version)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND (name=%s OR name GLOB %s) "
                "AND sql LIKE '%VIRTUAL TABLE%'",
                [
                    imported_fts_table_name(character_id),
                    f"{imported_fts_table_name(character_id)}__import_*",
                ],
            )
            stale_fts = [row[0] for row in cursor.fetchall() if row[0] != live_fts]
        for table_name in stale_fts:
            _drop_fts5_table(table_name)
    except Exception:
        logger.exception("Failed to clean stale FTS5 projections for character_id=%s", character_id)

    try:
        db = lancedb.connect(_STORAGE_DIR)
        legacy_vector = imported_vector_table_name(character_id)
        for table_name in _table_names(db):
            if (
                table_name == legacy_vector
                or table_name.startswith(f"{legacy_vector}__import_")
            ) and table_name != live_vector:
                db.drop_table(table_name)
    except Exception:
        logger.exception("Failed to clean stale LanceDB projections for character_id=%s", character_id)


def publish_imported_chat(
    character: Character,
    *,
    target_name: str,
    messages: list[dict],
) -> dict:
    """Stage and publish an Imported Chat replacement without a mixed live state.

    LanceDB is built and validated under a new version before SQLite is
    touched.  The SQLite transaction then publishes the matching raw records,
    FTS5 table, and version pointer together.  Queries resolve both projection
    names through that pointer, so an exception before commit leaves the
    previous Imported Chat completely live.
    """
    version = uuid.uuid4().hex
    vector_table = imported_vector_table_name(character.id, version)
    texts, chunk_count, key_message_count = _build_import_texts(messages, target_name)
    db = lancedb.connect(_STORAGE_DIR)
    try:
        LanceDB.from_texts(
            texts,
            CustomEmbeddings(),
            connection=db,
            table_name=vector_table,
        )
        if db.open_table(vector_table).count_rows() != len(texts):
            raise RuntimeError("Imported Chat LanceDB row count mismatch")
        fts_table = _publish_sqlite_import(
            character,
            target_name=target_name,
            messages=messages,
            version=version,
        )
    except Exception:
        # The temporary table is unreachable because the active pointer was
        # not published.  Removing it cannot damage the previous import.
        try:
            if vector_table in _table_names(db):
                db.drop_table(vector_table)
        except Exception:
            logger.exception("Failed to clean unpublished LanceDB table %s", vector_table)
        try:
            _drop_fts5_table(imported_fts_table_name(character.id, version))
        except Exception:
            logger.exception("Failed to clean unpublished FTS5 table for character_id=%s", character.id)
        raise

    _cleanup_old_import_projections(character.id, version)
    return {
        "version": version,
        "table_name": vector_table,
        "fts_table": fts_table,
        "total_chunks": chunk_count,
        "total_key_msgs": key_message_count,
    }


@router.post("/api/import/wechat/")
def import_wechat(
    file: UploadFile,
    target_name: str = Form(...),
    character_id: int = Form(...),
    user=Depends(get_current_user),
):
    """
    上传微信聊天记录 TXT 文件，解析后存入：
    1. LanceDB（语义向量搜索）
    2. SQLite FTS5（关键词全文搜索）
    """
    try:
        character = Character.objects.get(id=character_id, author__user=user)
    except Character.DoesNotExist as exc:
        raise ApiError(404, "character_not_found", "角色不存在或不属于你") from exc

    target_name = target_name.strip()

    raw = file.file.read()
    if not raw:
        raise ApiError(422, "empty_import_file", "文件为空")
    try:
        content = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    except UnicodeDecodeError as exc:
        raise ApiError(415, "invalid_import_encoding", "聊天文件必须使用 UTF-8 编码") from exc

    suffix = Path(file.filename or "chat.txt").suffix or ".txt"
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        messages = parse_wechat_txt(
            tmp_path,
            target_name=target_name,
            target_only=False,
            filter_noise=True,
        )
        if not messages:
            raise ApiError(
                422,
                "no_import_messages",
                "未解析到任何有效消息，请检查文件格式和 target_name",
            )

        publication = publish_imported_chat(
            character,
            target_name=target_name,
            messages=messages,
        )

        # ── 触发异步预处理 ──
        _start_preprocessing(character_id)

        return {
            "result": "success",
            "total_messages": len(messages),
            "total_chunks": publication["total_chunks"],
            "total_key_msgs": publication["total_key_msgs"],
            "fts_table": publication["fts_table"],
            "character_id": character_id,
            "table_name": publication["table_name"],
            "analyzing": True,  # 前端可据此轮询状态
        }

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _start_preprocessing(character_id: int) -> bool:
    """Start at most one preprocessing worker per character in this process."""
    with _active_preprocessing_lock:
        if character_id in _active_preprocessing:
            return False
        _active_preprocessing.add(character_id)
    threading.Thread(
        target=_run_preprocessing_async,
        args=(character_id,),
        daemon=True,
    ).start()
    return True


def _run_preprocessing_async(character_id: int):
    """后台线程中跑预处理，不阻塞导入响应"""
    try:
        from ai.preprocessing.pipeline import run_preprocessing

        run_preprocessing(character_id)
    except Exception as exc:
        logger.exception("[Import] Preprocessing failed for character_id=%s", character_id)
        try:
            ImportAnalysis.objects.update_or_create(
                character_id=character_id,
                defaults={
                    "status": "failed",
                    "error_message": str(exc)[:1000],
                },
            )
        except Exception:
            logger.exception("[Import] Failed to persist preprocessing error")
    finally:
        with _active_preprocessing_lock:
            _active_preprocessing.discard(character_id)


@router.post("/api/import/resume/")
def resume_import(data: ResumeImportRequest, user=Depends(get_current_user)):
    """Continue preprocessing from successful Map checkpoints without re-uploading."""
    character = Character.objects.filter(id=data.character_id, author__user=user).first()
    if not character:
        raise ApiError(404, "character_not_found", "角色不存在或不属于你")
    if not ChatMessage.objects.filter(character_id=data.character_id).exists():
        raise ApiError(409, "import_not_resumable", "没有可恢复的聊天原文，请先导入聊天记录")

    started = _start_preprocessing(data.character_id)
    return {
        "result": "success",
        "started": started,
        "message": "已从断点继续处理" if started else "该角色的预处理仍在运行",
    }


@router.get("/api/import/status/")
def import_status(character_id: int = Query(...), user=Depends(get_current_user)):
    """Return durable chunk progress; raw message count is never overloaded as percent."""
    try:
        character = Character.objects.filter(id=character_id, author__user=user).first()
        if not character:
            raise ApiError(404, "character_not_found", "角色不存在或不属于你")

        analysis = ImportAnalysis.objects.filter(character_id=character_id).first()
        if not analysis:
            return {"result": "success", "status": "not_started"}
        resp = {
            "result": "success",
            "status": analysis.status,
            "error_message": analysis.error_message,
            "stage": analysis.stage,
            "total_messages": analysis.total_messages,
            "total_chunks": analysis.total_chunks,
            "completed_chunks": analysis.completed_chunks,
            "failed_chunks": analysis.failed_chunks,
        }
        if analysis.status == "analyzing":
            if analysis.stage == "relationship_reduce":
                resp["progress_pct"] = 96
            elif analysis.stage == "style_reduce":
                resp["progress_pct"] = 97
            elif analysis.stage == "writing":
                resp["progress_pct"] = 98
            else:
                resp["progress_pct"] = int(
                    analysis.completed_chunks / analysis.total_chunks * 95
                ) if analysis.total_chunks else 0
        return resp
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(500, "import_status_failed", "导入状态加载失败，请稍后重试", True) from exc
