"""主编排器 — 预处理 Pipeline。

触发: api/import_data.py 导入完成后异步启动。

流程：
  Step 1: Chunking（0 LLM）→ 按聊天日切分
  Step 2: Map（N LLM，并行）→ 每个 Chunk 同时分析用户和 AI 角色
  Step 3: Relationship Reduce（1 LLM）→ 总结关系演变 overview/timeline
  Step 4: Write（0 LLM）→ 写入 TimeChunk / TopicTag / SemanticMemory / ImportAnalysis
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from web.models.character import Character
from web.models.import_analysis import ImportAnalysis
from ai.preprocessing.chunker import chunk_messages
from ai.preprocessing.chunk_analyzer import analyze_chunk
from ai.preprocessing.relationship_overview import analyze_relationship_overview
from ai.preprocessing.writer import write_results
from ai.config import require_llm_config

logger = logging.getLogger(__name__)

MAX_CONTEXT_RETRY_CHUNKS = 5


def run_preprocessing(
    character_id: int,
    api_key: str = "",
    api_base: str = "",
    max_workers: int = 5,
):
    """主流程。"""
    logger.info(f"[Preprocessing] Starting for character_id={character_id}")
    _set_status(character_id, "analyzing")
    _update_progress(character_id, 0, 1)
    try:
        require_llm_config()
    except RuntimeError as exc:
        _set_status(character_id, "failed", str(exc))
        return

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

    # 对少量失败的 Chunk 做补救重试。不要全量串行重试，否则大导入会长时间卡在 95%。
    failed_indices = [i for i, r in enumerate(chunk_results) if r and r.get("error")]
    retry_count = 0
    for i in failed_indices:
        if retry_count >= MAX_CONTEXT_RETRY_CHUNKS:
            break
        r = chunk_results[i]
        prev_s = chunk_results[i - 1].get("chunk_summary", "") if i > 0 and chunk_results[i - 1] else ""
        next_s = (
            chunk_results[i + 1].get("chunk_summary", "")
            if i + 1 < len(chunk_results) and chunk_results[i + 1]
            else ""
        )
        if not prev_s and not next_s:
            continue
        retry_count += 1
        logger.info(f"[Preprocessing] Retrying failed Chunk {r['chunk_index']} with context...")
        try:
            chunk_results[i] = analyze_chunk(
                chunks[r["chunk_index"]], target_name, prev_s, next_s, api_key, api_base
            )
        except Exception as e:
            logger.error(f"[Preprocessing] Chunk {r['chunk_index']} retry also failed: {e}")

    failed_after_retry = sum(1 for r in chunk_results if r and r.get("error"))
    logger.info(
        f"[Preprocessing] Map complete — {len(chunk_results)} results, "
        f"{failed_after_retry} failed, {retry_count} retried"
    )

    valid_results = [r for r in chunk_results if r and not r.get("error")]
    if not valid_results:
        _set_status(character_id, "failed", "所有聊天片段的 AI 分析都失败了，请检查 API Key、模型返回或聊天内容格式")
        return

    # ── Step 3: Relationship Reduce ──
    logger.info("[Preprocessing] Step 3: Relationship overview reduce...")
    _set_progress_pct(character_id, 96)
    relationship_analysis = analyze_relationship_overview(
        chunk_results, chunks, target_name, api_key, api_base
    )

    # ── Step 4: Write — 写入 DB ──
    logger.info("[Preprocessing] Step 4: Writing to DB...")
    _set_progress_pct(character_id, 98)
    write_results(character_id, chunk_results, chunks, total_messages, relationship_analysis)
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
        analysis.error_message = error
        analysis.save()
    except Exception:
        pass


def _update_progress(character_id: int, done: int, total: int):
    """更新 ImportAnalysis.total_messages 为进度百分比，供前端轮询。

    Map 阶段后面还有 Reduce 和写库，不能在这里显示 100%，否则前端会像完成
    一样卡在“正在处理记忆 100%”。
    """
    try:
        pct = int(done / total * 95) if total > 0 else 0
        _set_progress_pct(character_id, min(pct, 95))
    except Exception:
        pass


def _set_progress_pct(character_id: int, pct: int):
    try:
        ImportAnalysis.objects.filter(character_id=character_id).update(
            total_messages=max(0, min(int(pct), 99)),
        )
    except Exception:
        pass
