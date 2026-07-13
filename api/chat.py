import asyncio
import base64
import json
import threading
import uuid
from queue import Queue

import websockets
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, AIMessage

from ai.config import dashscope_api_key, dashscope_wss_url
from api.deps import get_current_user
from api.schemas import ChatRequest
from web.models.character import Character
from web.models.friend import Friend, Message, MessageAttachment, SystemPrompt
from ai.agents.supervisor_graph import create_supervisor_app
from ai.memory.reflection_jobs import (
    enqueue_completed_chat_days,
    process_pending_reflection_jobs,
)
from ai.memory.history_search import index_online_message
from ai.memory.conversation_summary import prepare_conversation_context
from ai.tracing import record_trace, serialize_messages
from ai.tools.time_tools import format_current_time_context

router = APIRouter()




async def tts_sender(app, inputs, mq, ws, task_id):
    result = await app.ainvoke(
        inputs,
        config={
            "run_name": "chat_supervisor_graph",
            "metadata": inputs.get("trace_metadata", {}),
            "tags": ["chat", "supervisor-graph"],
        },
    )
    final_message = result.get("messages", [])[-1]
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


def event_stream(app, inputs, friend, message, attachment_ids):
    mq = Queue()
    thread = threading.Thread(
        target=work,
        args=(app, inputs, mq, friend.character.voice.voice_id),
    )
    thread.start()

    full_output = ""
    output_bubbles = []
    full_usage = {}
    while True:
        msg = mq.get()
        if not msg:
            break
        if msg.get("bubbles") is not None:
            output_bubbles = [str(item) for item in msg["bubbles"] if str(item).strip()]
            full_output = "\n".join(output_bubbles)
            yield f"data: {json.dumps({'bubbles': output_bubbles}, ensure_ascii=False)}\n\n"
        if msg.get("audio", None):
            yield f"data: {json.dumps({'audio': msg['audio']}, ensure_ascii=False)}\n\n"
        if msg.get("usage", None):
            full_usage = msg["usage"]

    input_tokens = full_usage.get("input_tokens", 0)
    output_tokens = full_usage.get("output_tokens", 0)
    total_tokens = full_usage.get("total_tokens", 0)
    saved_message = Message.objects.create(
        friend=friend,
        user_message=message[:500],
        input=json.dumps(
            [m.model_dump() for m in inputs["messages"]],
            ensure_ascii=False,
        )[:10000],
        output=full_output,
        output_bubbles=output_bubbles,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )
    if attachment_ids:
        MessageAttachment.objects.filter(
            id__in=attachment_ids, friend=friend, message__isnull=True
        ).update(message=saved_message)
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
        return {"result": "消息不能为空"}

    friends = Friend.objects.filter(pk=data.friend_id, me__user=user)
    if not friends.exists():
        return {"result": "好友不存在"}

    friend = friends.first()
    attachment_ids = list(dict.fromkeys(data.attachment_ids))
    attachments = list(MessageAttachment.objects.filter(
        id__in=attachment_ids, friend=friend, message__isnull=True
    ))
    if len(attachments) != len(attachment_ids):
        return {"result": "图片不存在、已发送或不属于当前好友"}
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
    app = create_supervisor_app(
        friend_id=friend.id,
        character_id=friend.character.id,
        character_name=friend.character.name,
        character_profile=friend.character.profile,
    )

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

    inputs = {
        "messages": messages,
        "intent": "",
        "delegate_to": "",
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
            "friend_memory_cache_not_injected": friend.memory,
            "character_profile": friend.character.profile,
            "vision_attachment_count": len(vision_attachments),
        },
        metadata=inputs["trace_metadata"],
    )

    return StreamingResponse(
        event_stream(app, inputs, friend, display_message, attachment_ids),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
