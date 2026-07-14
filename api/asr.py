import asyncio
import json
import uuid

import websockets
from fastapi import APIRouter, Depends, Query, UploadFile

from ai.config import dashscope_api_key, dashscope_wss_url
from api.deps import get_current_user
from api.errors import ApiError

router = APIRouter()


async def asr_sender(pcm_data, ws, task_id):
    chunk = 3200
    for i in range(0, len(pcm_data), chunk):
        await ws.send(pcm_data[i : i + chunk])
        await asyncio.sleep(0.01)
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


async def asr_receiver(ws):
    text = ""
    async for msg in ws:
        data = json.loads(msg)
        event = data["header"]["event"]
        if event == "result-generated":
            output = data["payload"]["output"]
            if (
                output.get("transcription", None)
                and output["transcription"]["sentence_end"]
            ):
                text += output["transcription"]["text"]
        elif event in ["task-finished", "task-failed"]:
            break
    return text


async def run_asr_tasks(pcm_data):
    task_id = uuid.uuid4().hex
    api_key = dashscope_api_key()
    wss_url = dashscope_wss_url()
    headers = {"Authorization": f"Bearer {api_key}"}
    async with websockets.connect(wss_url, additional_headers=headers, proxy=None) as ws:
        await ws.send(
            json.dumps(
                {
                    "header": {
                        "streaming": "duplex",
                        "task_id": task_id,
                        "action": "run-task",
                    },
                    "payload": {
                        "model": "gummy-realtime-v1",
                        "parameters": {
                            "sample_rate": 16000,
                            "format": "pcm",
                            "transcription_enabled": True,
                        },
                        "input": {},
                        "task": "asr",
                        "task_group": "audio",
                        "function": "recognition",
                    },
                }
            )
        )
        async for msg in ws:
            if json.loads(msg)["header"]["event"] == "task-started":
                break
        _, text = await asyncio.gather(
            asr_sender(pcm_data, ws, task_id),
            asr_receiver(ws),
        )
        return text


@router.post("/api/friend/message/asr/asr/")
async def asr(audio: UploadFile, user=Depends(get_current_user)):
    if not audio.filename:
        raise ApiError(422, "missing_audio", "音频不存在")
    pcm_data = await audio.read()
    text = await run_asr_tasks(pcm_data)
    return {"result": "success", "text": text}
