"""主编排器 — Map-Only 预处理 Pipeline（方案 B：无 Reduce）。

触发: api/import_data.py 导入完成后异步启动。

流程：
  Step 1: Chunking（0 LLM）→ 按聊天日切分
  Step 2: Map（N LLM，并行）→ 每个 Chunk 同时分析用户和 AI 角色
  Step 3: Write（0 LLM）→ 直接写入 TimeChunk / TopicTag / SemanticMemory
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from web.models.character import Character
from web.models.import_analysis import ImportAnalysis
from ai.preprocessing.chunker import chunk_messages
from ai.preprocessing.chunk_analyzer import analyze_chunk
from ai.preprocessing.writer import write_results

logger = logging.getLogger(__name__)


def run_preprocessing(
    character_id: int,
    api_key: str = "",
    api_base: str = "",
    max_workers: int = 5,
):
    """主流程。"""
    logger.info(f"[Preprocessing] Starting for character_id={character_id}")
    _set_status(character_id, "analyzing")

    try:
        character = Character.objects.get(id=character_id)
        target_name = character.chat_sender_name or character.name
    except Character.DoesNotExist:
        _set_status(character_id, "failed", "Character not found")
        return

    # ── Step 1: Chunking ──
    logger.info("[Preprocessing] Step 1: Chunking...")
    chunks = chunk_messages(character_id)
    total_messages = sum(len(c["messages"]) for c in chunks)
    logger.info(f"[Preprocessing] {len(chunks)} chunks, {total_messages} messages total")

    if not chunks:
        _set_status(character_id, "done")
        return

    # ── Step 2: Map — 并行分析每个 Chunk ──
    logger.info(f"[Preprocessing] Step 2: Map — {len(chunks)} chunks, {max_workers} workers...")
    chunk_results: list[dict] = [None] * len(chunks)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(analyze_chunk, chunk, target_name, "", "", api_key, api_base): i
            for i, chunk in enumerate(chunks)
        }
        for future in as_completed(futures):
            i = futures[future]
            try:
                chunk_results[i] = future.result()
            except Exception as e:
                logger.error(f"[Preprocessing] Chunk {i} analysis failed: {e}")
                chunk_results[i] = {"chunk_index": i, "error": True, "error_msg": str(e)}

            # 更新进度
            done = sum(1 for r in chunk_results if r is not None)
            _update_progress(character_id, done, len(chunks))

    # 对失败的 Chunk 用相邻成功 Chunk 的摘要做上下文重试
    for i, r in enumerate(chunk_results):
        if r.get("error"):
            prev_s = chunk_results[i - 1].get("chunk_summary", "") if i > 0 else ""
            next_s = chunk_results[i + 1].get("chunk_summary", "") if i + 1 < len(chunk_results) else ""
            if prev_s or next_s:
                logger.info(f"[Preprocessing] Retrying failed Chunk {r['chunk_index']} with context...")
                try:
                    chunk_results[i] = analyze_chunk(
                        chunks[r["chunk_index"]], target_name, prev_s, next_s, api_key, api_base
                    )
                except Exception as e:
                    logger.error(f"[Preprocessing] Chunk {r['chunk_index']} retry also failed: {e}")

    logger.info(f"[Preprocessing] Map complete — {len(chunk_results)} results")

    # ── Step 3: Write — 直接写入（无 Reduce） ──
    logger.info("[Preprocessing] Step 3: Writing to DB...")
    write_results(character_id, chunk_results, chunks, total_messages)
    logger.info("[Preprocessing] Done!")

    # 最终状态
    ImportAnalysis.objects.filter(character_id=character_id).update(
        total_messages=total_messages,
    )


def _set_status(character_id: int, status: str, error: str = ""):
    try:
        analysis, _ = ImportAnalysis.objects.get_or_create(
            character_id=character_id,
            defaults={"status": status, "error_message": error},
        )
        analysis.status = status
        if error:
            analysis.error_message = error
        analysis.save()
    except Exception:
        pass


def _update_progress(character_id: int, done: int, total: int):
    """更新 ImportAnalysis.total_messages 为进度百分比（0-100），供前端轮询。"""
    try:
        pct = int(done / total * 100) if total > 0 else 0
        ImportAnalysis.objects.filter(character_id=character_id).update(
            total_messages=pct,  # 临时存进度百分比，写完后覆盖为真实条数
        )
    except Exception:
        pass
