import asyncio
import base64
import json
import threading
import uuid
from queue import Queue

import websockets
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, BaseMessageChunk, SystemMessage, AIMessage

from ai.config import dashscope_api_key, dashscope_wss_url
from api.deps import get_current_user
from api.schemas import ChatRequest
from web.models.character import Character
from web.models.friend import Friend, Message, SystemPrompt
from ai.agents.supervisor_graph import create_supervisor_app
from ai.memory.reflection import reflect_memories
from ai.tracing import record_trace, serialize_messages
from ai.tools.time_tools import format_current_time_context

router = APIRouter()




async def tts_sender(app, inputs, mq, ws, task_id):
    trace_metadata = inputs.get("trace_metadata", {})
    async for msg, metadata in app.astream(
        inputs,
        stream_mode="messages",
        config={
            "run_name": "chat_supervisor_graph",
            "metadata": trace_metadata,
            "tags": ["chat", "supervisor-graph"],
        },
    ):
        if isinstance(msg, BaseMessageChunk):
            if msg.content:
                await ws.send(
                    json.dumps(
                        {
                            "header": {
                                "action": "continue-task",
                                "task_id": task_id,
                                "streaming": "duplex",
                            },
                            "payload": {"input": {"text": msg.content}},
                        }
                    )
                )
                mq.put_nowait({"content": msg.content})
            if hasattr(msg, "usage_metadata") and msg.usage_metadata:
                mq.put_nowait({"usage": msg.usage_metadata})
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


def event_stream(app, inputs, friend, message):
    mq = Queue()
    thread = threading.Thread(
        target=work,
        args=(app, inputs, mq, friend.character.voice.voice_id),
    )
    thread.start()

    full_output = ""
    full_usage = {}
    while True:
        msg = mq.get()
        if not msg:
            break
        if msg.get("content", None):
            full_output += msg["content"]
            yield f"data: {json.dumps({'content': msg['content']}, ensure_ascii=False)}\n\n"
        if msg.get("audio", None):
            yield f"data: {json.dumps({'audio': msg['audio']}, ensure_ascii=False)}\n\n"
        if msg.get("usage", None):
            full_usage = msg["usage"]

    yield "data: [DONE]\n\n"
    input_tokens = full_usage.get("input_tokens", 0)
    output_tokens = full_usage.get("output_tokens", 0)
    total_tokens = full_usage.get("total_tokens", 0)
    Message.objects.create(
        friend=friend,
        user_message=message[:500],
        input=json.dumps(
            [m.model_dump() for m in inputs["messages"]],
            ensure_ascii=False,
        )[:10000],
        output=full_output[:500],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )
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

    # Reflection — read raw messages directly, no episodic intermediate step
    from django.utils.timezone import now
    hours_since = (now() - friend.last_reflection_time).total_seconds() / 3600
    if hours_since >= 6:
        threading.Thread(
            target=reflect_memories,
            args=(friend, False),
            daemon=True,
        ).start()


@router.post("/api/friend/message/chat/")
def chat(data: ChatRequest, user=Depends(get_current_user)):
    message = data.message.strip()
    if not message:
        return {"result": "消息不能为空"}

    friends = Friend.objects.filter(pk=data.friend_id, me__user=user)
    if not friends.exists():
        return {"result": "好友不存在"}

    friend = friends.first()

    # Use new Supervisor Graph
    app = create_supervisor_app(
        friend_id=friend.id,
        character_id=friend.character.id,
        character_name=friend.character.name,
        character_profile=friend.character.profile,
    )

    # Build system prompt + recent messages (same as before)
    system_prompts = SystemPrompt.objects.filter(title="回复").order_by("order_number")
    system_text = ""
    for sp in system_prompts:
        system_text += sp.prompt
    system_text += f"\n【角色性格】\n{friend.character.profile}\n"
    system_text += format_current_time_context()
    system_text += f"【长期记忆】\n{friend.memory}\n"
    system_text += (
        "\n【表情理解规则】\n"
        "用户消息里的 emoji 可能代表真实情绪，请结合上下文理解，不要只当装饰符号。\n"
        "如果用户使用 🙂‍↕️，通常表示不满、别扭、有点抗拒、嘴硬或小情绪。\n"
        "默认不要在每句回复末尾添加情绪标记或 emoji。"
        "只有当情绪非常明确、需要强调语气时，才偶尔使用一个中文全角情绪标记，"
        "例如【开心】【生气】【委屈】【害羞】。"
        "不要连续多轮使用同一个情绪标记，尤其不要把【亲亲】当作固定结尾。"
        "不要使用 [开心] 这种半角方括号格式。"
        "每次最多使用一个情绪标记。\n"
    )

    emotion_context = []
    for item in data.emotion_context[:8]:
        emoji = str(item.get("emoji", ""))[:20]
        meaning = str(item.get("meaning", ""))[:80]
        if emoji and meaning:
            emotion_context.append(f"{emoji}：{meaning}")

    model_message = message
    if emotion_context:
        model_message += "\n\n【用户表情含义】\n" + "\n".join(emotion_context)

    messages = [SystemMessage(content=system_text)]
    message_raw = list(Message.objects.filter(friend=friend).order_by("-id")[:10])
    message_raw.reverse()
    for m in message_raw:
        messages.append(HumanMessage(content=m.user_message))
        messages.append(AIMessage(content=m.output))
    messages.append(HumanMessage(content=model_message))

    inputs = {
        "messages": messages,
        "intent": "",
        "delegate_to": "",
        "memory_context": "",
        "emotion_analysis": None,
        "character_profile": friend.character.profile,
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
            "system_prompt": system_text,
            "recent_message_count": len(message_raw),
            "messages": serialize_messages(messages),
            "friend_memory_cache": friend.memory,
            "character_profile": friend.character.profile,
        },
        metadata=inputs["trace_metadata"],
    )

    return StreamingResponse(
        event_stream(app, inputs, friend, message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
