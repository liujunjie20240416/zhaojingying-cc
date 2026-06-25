import asyncio
import base64
import json
import os
import threading
import uuid
from queue import Queue

import websockets
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, BaseMessageChunk, SystemMessage, AIMessage

from api.deps import get_current_user
from api.schemas import ChatRequest
from web.models.character import Character
from web.models.friend import Friend, Message, SystemPrompt
from ai.agents.supervisor_graph import create_supervisor_app
from ai.memory.reflection import reflect_memories

router = APIRouter()




async def tts_sender(app, inputs, mq, ws, task_id):
    async for msg, metadata in app.astream(
        inputs, stream_mode="messages"
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
    api_key = os.getenv("API_KEY")
    wss_url = os.getenv("WSS_URL")
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
    system_text += f"【长期记忆】\n{friend.memory}\n"

    messages = [SystemMessage(content=system_text)]
    message_raw = list(Message.objects.filter(friend=friend).order_by("-id")[:10])
    message_raw.reverse()
    for m in message_raw:
        messages.append(HumanMessage(content=m.user_message))
        messages.append(AIMessage(content=m.output))
    messages.append(HumanMessage(content=message))

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
    }

    return StreamingResponse(
        event_stream(app, inputs, friend, message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
