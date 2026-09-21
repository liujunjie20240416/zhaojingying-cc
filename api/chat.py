import asyncio
import base64
import json
import logging
import threading
import uuid
from queue import Queue

import websockets
from django.db import transaction
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, AIMessage

logger = logging.getLogger(__name__)

from ai.config import dashscope_api_key, dashscope_wss_url, turn_deadline
from api.deps import get_current_user
from api.errors import ApiError
from api.schemas import ChatRequest
from storage.models.character import Character
from storage.models.friend import Friend, Message, MessageAttachment, SystemPrompt
from ai.agents.supervisor_graph import create_supervisor_app
from ai.memory.reflection_jobs import (
    enqueue_completed_chat_days,
    process_pending_reflection_jobs,
)
from ai.memory.history_search import index_online_message
from ai.memory.conversation_summary import prepare_conversation_context
from ai.memory.semantic import build_core_memory_context
from ai.tracing import record_trace, serialize_messages
from ai.time.time_tools import format_current_time_context

router = APIRouter()




async def tts_sender(app, inputs, mq, ws, task_id):
    try:
        result = await asyncio.wait_for(
            app.ainvoke(
                inputs,
                config={
                    "run_name": "chat_supervisor_graph",
                    "metadata": inputs.get("trace_metadata", {}),
                    "tags": ["chat", "supervisor-graph"],
                },
            ),
            timeout=turn_deadline(),
        )
    except asyncio.TimeoutError:
        # deadline 只保证用户不再干等，不保证服务端停手：节点是同步函数，跑在
        # executor 线程上，取消不了——那个线程会把已经发出去的调用跑完。
        # 所以这里记一笔日志，因为「谁超的」只有这条线索引得到。
        logger.error(
            "Chat turn exceeded %.0fs deadline for friend %s",
            turn_deadline(),
            inputs.get("friend_id"),
        )
        raise
    final_message = result.get("messages", [])[-1]
    provenance = dict(result.get("reply_provenance", inputs.get("reply_provenance", {})))
    # supervisor_decision 是从三个 state 字段拼出来的 provenance 字段，不是 state 键。
    provenance["supervisor_decision"] = {
        "has_emotion": result.get("has_emotion", False),
        "memory_kind": result.get("memory_kind", "none"),
        "classification_source": result.get("classification_source", ""),
    }
    mq.put_nowait({"reply_provenance": provenance})
    bubbles = list((getattr(final_message, "additional_kwargs", {}) or {}).get("bubbles") or [])
    if not bubbles and getattr(final_message, "content", ""):
        bubbles = [str(final_message.content)]
    mq.put_nowait({"bubbles": bubbles})
    for bubble in bubbles:
        await ws.send(
            json.dumps(
                {
                    "header": {
                        "action": "continue-task",
                        "task_id": task_id,
                        "streaming": "duplex",
                    },
                    "payload": {"input": {"text": bubble}},
                }
            )
        )
    if getattr(final_message, "usage_metadata", None):
        mq.put_nowait({"usage": final_message.usage_metadata})
    await ws.send(
        json.dumps(
            {
                "header": {
                    "action": "finish-task",
                    "task_id": task_id,
                    "streaming": "duplex",
                },
                "payload": {"input": {}},
            }
        )
    )


async def tts_receiver(mq, ws):
    async for msg in ws:
        if isinstance(msg, bytes):
            audio = base64.b64encode(msg).decode("utf-8")
            mq.put_nowait({"audio": audio})
        else:
            data = json.loads(msg)
            event = data["header"]["event"]
            if event in ["task-finished", "task-failed"]:
                break


async def run_tts_tasks(app, inputs, mq, voice_id):
    task_id = uuid.uuid4().hex
    api_key = dashscope_api_key()
    wss_url = dashscope_wss_url()
    headers = {"Authorization": f"Bearer {api_key}"}
    async with websockets.connect(wss_url, additional_headers=headers, proxy=None) as ws:
        await ws.send(
            json.dumps(
                {
                    "header": {
                        "action": "run-task",
                        "task_id": task_id,
                        "streaming": "duplex",
                    },
                    "payload": {
                        "task_group": "audio",
                        "task": "tts",
                        "function": "SpeechSynthesizer",
                        "model": "cosyvoice-v3-flash",
                        "parameters": {
                            "text_type": "PlainText",
                            "voice": voice_id,
                            "format": "mp3",
                            "sample_rate": 22050,
                            "volume": 50,
                            "rate": 1.25,
                            "pitch": 1,
                        },
                        "input": {},
                    },
                }
            )
        )
        async for msg in ws:
            if json.loads(msg)["header"]["event"] == "task-started":
                break
        await asyncio.gather(
            tts_sender(app, inputs, mq, ws, task_id),
            tts_receiver(mq, ws),
        )


def work(app, inputs, mq, voice_id):
    try:
        asyncio.run(run_tts_tasks(app, inputs, mq, voice_id))
    except Exception:
        # A graph failure (provider outage, missing config, network error) must
        # surface to the client instead of ending the stream silently. The full
        # traceback stays server-side; the client only receives a safe message.
        logger.exception("Chat graph failed for friend %s", inputs.get("friend_id"))
        mq.put_nowait({"error": {"message": "AI 回复生成失败，请重试"}})
    finally:
        mq.put_nowait(None)


def _run_post_chat_tasks(message_id: int, friend_id: int, process_reflection: bool):
    index_online_message(message_id)
    if process_reflection:
        process_pending_reflection_jobs(friend_id=friend_id, limit=3)


def _build_conversation_messages(
    message: str,
    emotion_context: list,
    history_rows: list[Message],
) -> list:
    """Build model history without mutating the user's original message.

    emotion_context remains structured graph state for Supervisor/Emotion Agent;
    it must not become retrieval text or alter the HumanMessage content.
    """
    messages = []
    for row in history_rows:
        messages.append(HumanMessage(content=row.user_message))
        messages.append(AIMessage(content=row.output))
    messages.append(HumanMessage(content=message))
    return messages


def _save_completed_message(
    *,
    friend_id: int,
    expected_generation: int,
    display_message: str,
    inputs: dict,
    full_output: str,
    output_bubbles: list,
    full_usage: dict,
    attachment_ids: list[int],
    reply_provenance: dict | None = None,
) -> Message | None:
    """Save a completed response only if its Online Chat generation is current.

    The row lock makes this atomic with clear_history(): either the message is
    saved first and then cleared, or clear increments the generation first and
    this stale response is discarded.
    """
    with transaction.atomic():
        current_friend = Friend.objects.select_for_update().get(id=friend_id)
        if current_friend.online_history_generation != expected_generation:
            return None

        saved_message = Message.objects.create(
            friend=current_friend,
            user_message=display_message[:500],
            input=json.dumps(
                [m.model_dump() for m in inputs["messages"]],
                ensure_ascii=False,
            )[:10000],
            output=full_output,
            output_bubbles=output_bubbles,
            reply_provenance=reply_provenance or {},
            input_tokens=full_usage.get("input_tokens", 0),
            output_tokens=full_usage.get("output_tokens", 0),
            total_tokens=full_usage.get("total_tokens", 0),
        )
        if attachment_ids:
            MessageAttachment.objects.filter(
                id__in=attachment_ids,
                friend=current_friend,
                message__isnull=True,
            ).update(message=saved_message)
        return saved_message


def event_stream(
    app,
    inputs,
    friend,
    message,
    attachment_ids,
    expected_generation,
):
    mq = Queue()
    thread = threading.Thread(
        target=work,
        args=(app, inputs, mq, friend.character.voice.voice_id),
    )
    thread.start()

    full_output = ""
    output_bubbles = []
    full_usage = {}
    had_error = False
    reply_provenance = inputs.get("reply_provenance", {})
    while True:
        msg = mq.get()
        if not msg:
            break
        if msg.get("error"):
            had_error = True
            yield f"data: {json.dumps({'error': msg['error']}, ensure_ascii=False)}\n\n"
            continue
        if msg.get("bubbles") is not None:
            output_bubbles = [str(item) for item in msg["bubbles"] if str(item).strip()]
            full_output = "\n".join(output_bubbles)
            yield f"data: {json.dumps({'bubbles': output_bubbles}, ensure_ascii=False)}\n\n"
        if msg.get("audio", None):
            yield f"data: {json.dumps({'audio': msg['audio']}, ensure_ascii=False)}\n\n"
        if msg.get("usage", None):
            full_usage = msg["usage"]
        if msg.get("reply_provenance") is not None:
            reply_provenance = msg["reply_provenance"]
            yield f"data: {json.dumps({'reply_provenance': reply_provenance}, ensure_ascii=False)}\n\n"

    # A failed or content-less turn must not persist an empty AI message that
    # later pollutes history and future context assembly.
    if had_error or not full_output.strip():
        if not had_error:
            logger.warning(
                "Chat graph returned no content for friend %s", friend.id,
            )
        yield "data: [DONE]\n\n"
        return

    saved_message = _save_completed_message(
        friend_id=friend.id,
        expected_generation=expected_generation,
        display_message=message,
        inputs=inputs,
        full_output=full_output,
        output_bubbles=output_bubbles,
        full_usage=full_usage,
        attachment_ids=attachment_ids,
        reply_provenance=reply_provenance,
    )
    if saved_message is None:
        record_trace(
            "chat.stream_discarded",
            {"friend_id": friend.id, "expected_generation": expected_generation},
            {"reason": "online_history_was_cleared"},
            metadata=inputs.get("trace_metadata", {}),
        )
        yield "data: [DONE]\n\n"
        return

    record_trace(
        "chat.stream_output",
        {
            "friend_id": friend.id,
            "character_id": friend.character.id,
            "messages": serialize_messages(inputs.get("messages", [])),
        },
        {
            "output": full_output,
            "usage": full_usage,
        },
        metadata=inputs.get("trace_metadata", {}),
    )

    # Persist jobs before starting a best-effort local worker. If the process
    # stops, the DB job remains and the next chat/management worker resumes it.
    has_reflection_jobs = bool(enqueue_completed_chat_days(friend))
    threading.Thread(
        target=_run_post_chat_tasks,
        args=(saved_message.id, friend.id, has_reflection_jobs),
        daemon=True,
    ).start()
    yield "data: [DONE]\n\n"


@router.post("/api/friend/message/chat/")
def chat(data: ChatRequest, user=Depends(get_current_user)):
    message = data.message.strip()
    display_message = message
    if not message and not data.attachment_ids:
        raise ApiError(422, "empty_message", "消息不能为空")

    friends = Friend.objects.filter(pk=data.friend_id, me__user=user)
    if not friends.exists():
        raise ApiError(404, "friend_not_found", "好友不存在")

    friend = friends.first()
    attachment_ids = list(dict.fromkeys(data.attachment_ids))
    attachments = list(MessageAttachment.objects.filter(
        id__in=attachment_ids, friend=friend, message__isnull=True
    ))
    if len(attachments) != len(attachment_ids):
        raise ApiError(
            409,
            "attachment_unavailable",
            "图片不存在、已发送或不属于当前好友",
        )
    if not message:
        message = "请看看我发的图片"

    vision_attachments = []
    for attachment in attachments:
        with attachment.file.open("rb") as image_file:
            encoded = base64.b64encode(image_file.read()).decode("ascii")
        vision_attachments.append({
            "id": attachment.id,
            "data_url": f"data:{attachment.mime_type};base64,{encoded}",
        })

    # Use new Supervisor Graph
    app = create_supervisor_app()

    # Conversation Agent will build exactly one final SystemMessage.
    system_prompts = SystemPrompt.objects.filter(title="回复").order_by("order_number")
    base_system_prompt = "".join(sp.prompt for sp in system_prompts)
    response_rules = (
        "\n【表情理解规则】\n"
        "用户消息里的 emoji 可能代表真实情绪，请结合上下文理解，不要只当装饰符号。\n"
        "如果用户使用 🙂‍↕️，通常表示不满、别扭、有点抗拒、嘴硬或小情绪。\n"
        "默认不要在每句回复末尾添加情绪标记或 emoji。"
        "只有当情绪非常明确、需要强调语气时，才偶尔使用一个中文全角情绪标记，"
        "例如【开心】【生气】【委屈】【害羞】。"
        "不要连续多轮使用同一个情绪标记，尤其不要把【亲亲】当作固定结尾。"
        "不要使用 [开心] 这种半角方括号格式。"
        "如果确实需要情绪标记，只能从【开心】【高兴】【生气】【很生气】【委屈】【哭】"
        "【难过】【害羞】【亲亲】【无语】【惊讶】【爱你】【想你】【撒娇】【别扭】【吃醋】中选择。"
        "每次最多使用一个情绪标记。\n"
    )

    emotion_context = []
    for item in data.emotion_context[:8]:
        emoji = str(item.get("emoji", ""))[:20]
        meaning = str(item.get("meaning", ""))[:80]
        if emoji and meaning:
            emotion_context.append(f"{emoji}：{meaning}")

    conversation_summary, message_raw = prepare_conversation_context(friend)
    messages = _build_conversation_messages(message, emotion_context, message_raw)

    # 意图继承已移除：每一轮都由 supervisor 重新分类。「哈哈」这类无信息量的
    # 短消息仍由快速通道兜底，但「还有呢」这类省略指代的短消息现在每轮都过分类器，
    # 靠分类器提示词里的最近对话理解指代——多一次调用的代价见 spec §6。

    inputs = {
        "messages": messages,
        "has_emotion": False,
        "memory_kind": "none",
        "memory_context": "",
        "emotion_analysis": None,
        "emotion_context": emotion_context,
        "vision_attachments": vision_attachments,
        "character_profile": friend.character.profile,
        "style_profile": friend.character.style_profile,
        "base_system_prompt": base_system_prompt + response_rules,
        "time_context": format_current_time_context(),
        "conversation_summary": conversation_summary,
        "character_name": friend.character.name,
        "chat_sender_name": friend.character.chat_sender_name or friend.character.name,
        "semantic_facts": [],
        "core_memory_context": build_core_memory_context(friend.id),
        "reply_provenance": {
            "recent_online": [
                {
                    "message_id": row.id,
                    "excerpt": f"用户：{row.user_message[:300]}\nAI：{(row.output or '')[:500]}",
                }
                for row in message_raw[-10:]
            ],
            "summary_through_message_id": friend.summary_through_message_id,
            "has_working_summary": bool(conversation_summary),
            "retrieved_raw": [],
        },
        "last_provider_input_tokens": Message.objects.filter(friend=friend).order_by("-id").values_list(
            "input_tokens", flat=True
        ).first() or 0,
        "friend_id": friend.id,
        "character_id": friend.character.id,
        "trace_metadata": {
            "friend_id": friend.id,
            "character_id": friend.character.id,
            "character_name": friend.character.name,
            "chat_sender_name": friend.character.chat_sender_name or friend.character.name,
            "entrypoint": "api/friend/message/chat",
        },
    }

    record_trace(
        "chat.request_preprocessed",
        {
            "raw_user_message": message,
            "emotion_context": emotion_context,
            "base_system_prompt": base_system_prompt,
            "style_profile": friend.character.style_profile,
            "time_context": inputs["time_context"],
            "recent_message_count": len(message_raw),
            "conversation_summary": conversation_summary,
            "messages": serialize_messages(messages),
            "character_profile": friend.character.profile,
            "vision_attachment_count": len(vision_attachments),
            "reply_provenance": inputs["reply_provenance"],
        },
        metadata=inputs["trace_metadata"],
    )

    return StreamingResponse(
        event_stream(
            app,
            inputs,
            friend,
            display_message,
            attachment_ids,
            friend.online_history_generation,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
