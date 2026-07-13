"""主编排器 — 预处理 Pipeline。

触发: api/import_data.py 导入完成后异步启动。

流程：
  Step 1: Chunking（0 LLM）→ 按聊天日切分
  Step 2: Map（N LLM，并行）→ 每个 Chunk 同时分析用户和 AI 角色
  Step 3: Relationship Reduce（1 LLM）→ 总结关系演变 overview/timeline
  Step 4: Write（0 LLM）→ 写入 TimeChunk / TopicTag / SemanticMemory / ImportAnalysis
"""

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.utils.timezone import now as djnow

from web.models.character import Character
from web.models.import_analysis import ImportAnalysis, PreprocessingCheckpoint
from ai.preprocessing.chunker import chunk_messages
from ai.preprocessing.chunk_analyzer import analyze_chunk
from ai.preprocessing.relationship_overview import analyze_relationship_overview
from ai.preprocessing.style_analyzer import analyze_style_profile
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
    total_messages = _count_unique_messages(chunks)
    _initialize_progress(character_id, total_messages, len(chunks))
    logger.info(f"[Preprocessing] {len(chunks)} chunks, {total_messages} messages total")

    if not chunks:
        _set_status(character_id, "done")
        return

    # ── Step 2: Map — 并行分析每个 Chunk ──
    logger.info(f"[Preprocessing] Step 2: Map — {len(chunks)} chunks, {max_workers} workers...")
    chunk_results: list[dict] = [None] * len(chunks)
    source_fingerprint = _source_fingerprint(chunks)
    chunk_fingerprints = [_chunk_fingerprint(chunk) for chunk in chunks]
    cached_results = {
        checkpoint.chunk_index: checkpoint.result_json
        for checkpoint in PreprocessingCheckpoint.objects.filter(
            character_id=character_id,
            source_fingerprint=source_fingerprint,
        )
        if checkpoint.chunk_index < len(chunks)
        and checkpoint.chunk_fingerprint == chunk_fingerprints[checkpoint.chunk_index]
        and checkpoint.result_json
        and not checkpoint.result_json.get("error")
    }
    for index, result in cached_results.items():
        chunk_results[index] = result
    if cached_results:
        logger.info(
            "[Preprocessing] Resuming from checkpoint — %s/%s chunks already complete",
            len(cached_results),
            len(chunks),
        )
        _update_chunk_progress(character_id, chunk_results, len(chunks))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(analyze_chunk, chunk, target_name, "", "", api_key, api_base): i
            for i, chunk in enumerate(chunks)
            if chunk_results[i] is None
        }
        for future in as_completed(futures):
            i = futures[future]
            try:
                chunk_results[i] = future.result()
            except Exception as e:
                logger.error(f"[Preprocessing] Chunk {i} analysis failed: {e}")
                chunk_results[i] = {"chunk_index": i, "error": True, "error_msg": str(e)}

            if chunk_results[i] and not chunk_results[i].get("error"):
                _save_checkpoint(
                    character_id,
                    source_fingerprint,
                    i,
                    chunk_fingerprints[i],
                    chunk_results[i],
                )

            # 更新进度
            _update_chunk_progress(character_id, chunk_results, len(chunks))

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
            if chunk_results[i] and not chunk_results[i].get("error"):
                _save_checkpoint(
                    character_id,
                    source_fingerprint,
                    i,
                    chunk_fingerprints[i],
                    chunk_results[i],
                )
        except Exception as e:
            logger.error(f"[Preprocessing] Chunk {r['chunk_index']} retry also failed: {e}")

    failed_after_retry = sum(1 for r in chunk_results if r and r.get("error"))
    _update_chunk_progress(character_id, chunk_results, len(chunks))
    logger.info(
        f"[Preprocessing] Map complete — {len(chunk_results)} results, "
        f"{failed_after_retry} failed, {retry_count} retried"
    )

    if failed_after_retry:
        _set_status(
            character_id,
            "partial",
            _format_map_failure(chunk_results, len(chunks)),
        )
        return

    # ── Step 3: Relationship Reduce ──
    logger.info("[Preprocessing] Step 3: Relationship overview reduce...")
    _set_stage(character_id, "relationship_reduce")
    relationship_analysis = analyze_relationship_overview(
        chunk_results, chunks, target_name, api_key, api_base
    )
    _set_stage(character_id, "style_reduce")
    # Re-import may contain more history, so compile Style Profile again from
    # the complete Imported Chat. Online Chat never participates here.
    style_profile = analyze_style_profile(
        character_id, target_name, chunk_results, api_key, api_base
    )

    # ── Step 4: Write — 写入 DB ──
    logger.info("[Preprocessing] Step 4: Writing to DB...")
    _set_stage(character_id, "writing")
    write_results(
        character_id, chunk_results, chunks, total_messages,
        relationship_analysis, style_profile, len(chunks),
    )
    logger.info("[Preprocessing] Done!")

    # 最终状态
    ImportAnalysis.objects.filter(character_id=character_id).update(
        total_messages=total_messages,
        total_chunks=len(chunks),
        completed_chunks=len(chunks),
        failed_chunks=0,
        stage="done",
        status="done",
        error_message="",
        updated_at=djnow(),
    )
    # 成功后只保留当前原始聊天对应的断点，旧导入不会无限堆积。
    PreprocessingCheckpoint.objects.filter(character_id=character_id).exclude(
        source_fingerprint=source_fingerprint
    ).delete()


def _stable_hash(value) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _chunk_fingerprint(chunk: dict) -> str:
    return _stable_hash(chunk)


def _source_fingerprint(chunks: list[dict]) -> str:
    """Fingerprint source messages once, excluding overlap duplicates."""
    messages = {}
    for chunk in chunks:
        for message in chunk.get("messages", []):
            key = message.get("msg_index")
            messages[key] = message
    return _stable_hash([messages[key] for key in sorted(messages, key=lambda item: str(item))])


def _save_checkpoint(
    character_id: int,
    source_fingerprint: str,
    chunk_index: int,
    chunk_fingerprint: str,
    result: dict,
):
    PreprocessingCheckpoint.objects.update_or_create(
        character_id=character_id,
        source_fingerprint=source_fingerprint,
        chunk_index=chunk_index,
        defaults={
            "chunk_fingerprint": chunk_fingerprint,
            "result_json": result,
            "updated_at": djnow(),
        },
    )


def _count_unique_messages(chunks: list[dict]) -> int:
    """Count source messages once even when AnalysisChunks overlap."""
    return len({
        message["msg_index"]
        for chunk in chunks
        for message in chunk.get("messages", [])
    })


def _set_status(character_id: int, status: str, error: str = ""):
    try:
        analysis, _ = ImportAnalysis.objects.get_or_create(
            character_id=character_id,
            defaults={"status": status, "error_message": error},
        )
        analysis.status = status
        analysis.error_message = error
        analysis.stage = status
        analysis.updated_at = djnow()
        analysis.save(update_fields=["status", "error_message", "stage", "updated_at"])
    except Exception:
        pass


def _initialize_progress(character_id: int, total_messages: int, total_chunks: int):
    try:
        ImportAnalysis.objects.filter(character_id=character_id).update(
            total_messages=total_messages,
            total_chunks=total_chunks,
            completed_chunks=0,
            failed_chunks=0,
            stage="map",
            updated_at=djnow(),
        )
    except Exception:
        pass


def _update_chunk_progress(character_id: int, results: list, total_chunks: int):
    successful = sum(1 for result in results if result and not result.get("error"))
    failed = sum(1 for result in results if result and result.get("error"))
    ImportAnalysis.objects.filter(character_id=character_id).update(
        total_chunks=total_chunks,
        completed_chunks=successful,
        failed_chunks=failed,
        stage="map",
        updated_at=djnow(),
    )


def _set_stage(character_id: int, stage: str):
    ImportAnalysis.objects.filter(character_id=character_id).update(
        stage=stage,
        updated_at=djnow(),
    )


def _format_map_failure(results: list, total_chunks: int) -> str:
    successful = sum(1 for result in results if result and not result.get("error"))
    failed_results = [result for result in results if result and result.get("error")]
    messages = []
    for result in failed_results:
        message = str(result.get("error_msg") or "未知错误").strip()
        if message and message not in messages:
            messages.append(message[:300])
        if len(messages) >= 3:
            break
    detail = "；".join(messages) or "模型调用失败"
    return (
        f"预处理尚未完成：成功 {successful}/{total_chunks} 个 Chunk，"
        f"失败 {len(failed_results)} 个。可充值或排除错误后从断点继续。原因：{detail}"
    )[:1000]
