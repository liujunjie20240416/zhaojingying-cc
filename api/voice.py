"""自定义音色 API — DashScope Voice Enrollment。"""

import uuid

import requests
from django.conf import settings
from fastapi import APIRouter, Depends, File, Form, UploadFile

from ai.config import dashscope_api_key, dashscope_voice_url
from api.deps import get_current_user
from web.models.character import Voice

router = APIRouter()

VOICE_URL = dashscope_voice_url()
API_KEY = dashscope_api_key()


def _dashscope_voice_api(action: str, **kwargs) -> dict:
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    data = {"model": "voice-enrollment", "input": {"action": action, **kwargs}}
    resp = requests.post(VOICE_URL, headers=headers, json=data)
    return resp.json()


@router.post("/api/create/character/voice/custom/create/")
def create_custom_voice(
    audio: UploadFile = File(...),
    voice_name: str = Form(...),
    user=Depends(get_current_user),
):
    try:
        voice_dir = settings.MEDIA_ROOT / "voice_samples"
        voice_dir.mkdir(parents=True, exist_ok=True)
        ext = audio.filename.split(".")[-1] if audio.filename and "." in audio.filename else "wav"
        filename = f"{uuid.uuid4().hex}.{ext}"
        filepath = voice_dir / filename
        filepath.write_bytes(audio.file.read())

        audio_url = f"http://127.0.0.1:8000/media/voice_samples/{filename}"

        result = _dashscope_voice_api(
            "create_voice",
            target_model="cosyvoice-v3-flash",
            prefix=voice_name,
            url=audio_url,
        )

        voice_id = result.get("output", {}).get("voice_id", "")
        if not voice_id:
            filepath.unlink(missing_ok=True)
            return {"result": "failed", "detail": result}

        voice = Voice.objects.create(name=voice_name, voice_id=voice_id)

        return {
            "result": "success",
            "voice": {"id": voice.id, "name": voice.name, "voice_id": voice.voice_id},
        }
    except Exception:
        return {"result": "系统异常，请稍后重试"}


@router.get("/api/create/character/voice/custom/list/")
def list_custom_voices(user=Depends(get_current_user)):
    try:
        voices = [
            {"id": v.id, "name": v.name, "voice_id": v.voice_id}
            for v in Voice.objects.order_by("-create_time")
        ]
        return {"result": "success", "voices": voices}
    except Exception:
        return {"result": "系统异常，请稍后重试"}


@router.post("/api/create/character/voice/custom/delete/")
def delete_custom_voice(
    voice_id: str = Form(...),
    user=Depends(get_current_user),
):
    try:
        _dashscope_voice_api("delete_voice", voice_id=voice_id)
        Voice.objects.filter(voice_id=voice_id).delete()
        return {"result": "success"}
    except Exception:
        return {"result": "系统异常，请稍后重试"}
