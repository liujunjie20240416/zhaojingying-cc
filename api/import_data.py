"""
聊天记录导入 API

POST /api/import/wechat/  — 上传微信聊天记录文件，解析后存入 LanceDB + FTS5
"""

import os
import tempfile
import threading
from pathlib import Path

import lancedb
from django.db import connection
from fastapi import APIRouter, Depends, Form, Query, UploadFile
from langchain_community.vectorstores import LanceDB

from ai.custom_embeddings import CustomEmbeddings
from api.deps import get_current_user
from web.models.character import Character
from web.models.chat_message import ChatMessage
from web.models.import_analysis import ImportAnalysis
from tools.wechat_parser import parse_wechat_txt, format_output_as_chunks

router = APIRouter()

# LanceDB 存储目录
_STORAGE_DIR = str(
    Path(__file__).resolve().parent.parent / "ai" / "documents" / "lancedb_storage"
)


def _ensure_fts5_table(character_id: int):
    """为指定角色创建 FTS5 虚拟表（如果不存在）"""
    table_name = f"chat_fts_{character_id}"
    with connection.cursor() as c:
        c.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS "{table_name}"
            USING fts5(sender, content, timestamp, tokenize='unicode61')
        """)
    return table_name


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
    except Character.DoesNotExist:
        return {"result": "角色不存在或不属于你"}

    # 保存发送人映射
    character.chat_sender_name = target_name.strip()
    character.save(update_fields=["chat_sender_name"])

    raw = file.file.read()
    if not raw:
        return {"result": "文件为空"}
    content = raw.decode("utf-8") if isinstance(raw, bytes) else raw

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
            return {"result": "未解析到任何有效消息，请检查文件格式和 target_name"}

        # ── 1. 存入 LanceDB（语义向量） ──
        chunks = format_output_as_chunks(messages, target_name, chunk_size=40)
        key_messages: list[str] = []
        for m in messages:
            if len(m["content"]) > 30:
                key_messages.append(f"[{m['timestamp']}] {m['sender']}: {m['content']}")
        all_texts = chunks + key_messages

        table_name = f"wechat_{character_id}"
        db = lancedb.connect(_STORAGE_DIR)
        if table_name in db.table_names():
            db.drop_table(table_name)
        LanceDB.from_texts(all_texts, CustomEmbeddings(), connection=db, table_name=table_name)

        # ── 2. 存入 SQLite FTS5（关键词全文搜索） ──
        fts_table = _ensure_fts5_table(character_id)

        # 清空旧数据
        ChatMessage.objects.filter(character_id=character_id).delete()
        with connection.cursor() as c:
            c.execute(f'DELETE FROM "{fts_table}"')

        # 批量插入 ChatMessage（每 500 条一批）
        batch: list[ChatMessage] = []
        for i, msg in enumerate(messages):
            batch.append(ChatMessage(
                character_id=character_id,
                sender=msg["sender"],
                content=msg["content"],
                timestamp=msg.get("timestamp", ""),
                msg_index=i,
            ))
            if len(batch) >= 500:
                ChatMessage.objects.bulk_create(batch)
                batch.clear()
        if batch:
            ChatMessage.objects.bulk_create(batch)

        # 同步到 FTS5：INSERT INTO fts_table SELECT FROM chat_message
        with connection.cursor() as c:
            c.execute(f"""
                INSERT INTO "{fts_table}" (rowid, sender, content, timestamp)
                SELECT id, sender, content, timestamp FROM chat_message
                WHERE character_id = {character_id}
            """)

        # ── 触发异步预处理 ──
        threading.Thread(
            target=_run_preprocessing_async,
            args=(character_id,),
            daemon=True,
        ).start()

        return {
            "result": "success",
            "total_messages": len(messages),
            "total_chunks": len(chunks),
            "total_key_msgs": len(key_messages),
            "fts_table": fts_table,
            "character_id": character_id,
            "table_name": table_name,
            "analyzing": True,  # 前端可据此轮询状态
        }

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _run_preprocessing_async(character_id: int):
    """后台线程中跑预处理，不阻塞导入响应"""
    try:
        from ai.preprocessing.pipeline import run_preprocessing

        run_preprocessing(character_id)
    except Exception:
        pass


@router.get("/api/import/status/")
def import_status(character_id: int = Query(...), user=Depends(get_current_user)):
    """查询预处理进度。analyzing 时 total_messages 为进度百分比(0-100)，done 后为实际条数。"""
    try:
        analysis = ImportAnalysis.objects.filter(character_id=character_id).first()
        if not analysis:
            return {"result": "success", "status": "not_started"}
        resp = {
            "result": "success",
            "status": analysis.status,
            "error_message": analysis.error_message,
        }
        if analysis.status == "analyzing":
            resp["progress_pct"] = analysis.total_messages  # 临时存的是百分比
        else:
            resp["total_messages"] = analysis.total_messages
        return resp
    except Exception:
        return {"result": "系统异常，请稍后重试"}
