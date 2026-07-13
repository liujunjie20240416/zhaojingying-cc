import hashlib
import io
import uuid

from django.core.files.base import ContentFile
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from django.utils.timezone import localtime
from PIL import Image, ImageOps, UnidentifiedImageError

from api.deps import get_current_user
from api.schemas import RemoveFriendRequest
from web.models.friend import Message, Friend, MessageAttachment
from web.models.reflection_job import ReflectionJob
from ai.memory.history_search import drop_online_history_index

router = APIRouter()

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 30_000_000
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _attachment_payload(attachment: MessageAttachment) -> dict:
    return {
        "id": attachment.id,
        "url": attachment.file.url,
        "mime_type": attachment.mime_type,
        "width": attachment.width,
        "height": attachment.height,
    }


@router.post("/api/friend/message/attachment/upload/")
def upload_attachment(
    friend_id: int = Form(...),
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    """Validate, strip metadata and persist a private chat image."""
    try:
        friend = Friend.objects.get(pk=friend_id, me__user=user)
    except Friend.DoesNotExist as exc:
        raise HTTPException(status_code=404, detail="好友不存在") from exc
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=415, detail="仅支持 JPG、PNG 和 WebP 图片")
    # Keep the entire handler synchronous so FastAPI runs Django ORM, Pillow
    # decoding and storage writes in its worker thread instead of the event loop.
    raw = file.file.read(MAX_IMAGE_BYTES + 1)
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="图片不能超过 10MB")
    try:
        image = Image.open(io.BytesIO(raw))
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise HTTPException(status_code=413, detail="图片分辨率过大")
        image.load()
        image = ImageOps.exif_transpose(image)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(status_code=415, detail="图片文件无效或已损坏") from exc
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA" if "transparency" in image.info else "RGB")
    output = io.BytesIO()
    image.save(output, format="WEBP", quality=88, method=4)
    normalized = output.getvalue()
    attachment = MessageAttachment(
        friend=friend, mime_type="image/webp", file_size=len(normalized),
        width=image.width, height=image.height,
        sha256=hashlib.sha256(normalized).hexdigest(),
    )
    attachment.file.save(f"{uuid.uuid4().hex}.webp", ContentFile(normalized), save=False)
    attachment.save()
    return {"result": "success", "attachment": _attachment_payload(attachment)}


@router.get("/api/friend/message/get_history/")
def get_history(
    last_message_id: int = Query(...),
    friend_id: int = Query(...),
    user=Depends(get_current_user),
):
    try:
        queryset = Message.objects.filter(
            friend_id=friend_id, friend__me__user=user
        )
        if last_message_id > 0:
            queryset = queryset.filter(pk__lt=last_message_id)
        messages_raw = queryset.prefetch_related("attachments").order_by("-id")[:10]
        messages = [
            {
                "id": m.id,
                "user_message": m.user_message,
                "output": m.output,
                "output_bubbles": m.output_bubbles or ([m.output] if m.output else []),
                "create_time": localtime(m.create_time).isoformat(),
                "attachments": [_attachment_payload(a) for a in m.attachments.all()],
            }
            for m in messages_raw
        ]
        return {"result": "success", "messages": messages}
    except Exception:
        return {"result": "系统异常，请稍后重试"}


@router.post("/api/friend/message/clear/")
def clear_history(
    data: RemoveFriendRequest,
    user=Depends(get_current_user),
):
    """清空指定好友的对话历史 + 长期记忆"""
    try:
        friend = Friend.objects.get(id=data.friend_id, me__user=user)
        deleted, _ = Message.objects.filter(friend=friend).delete()
        ReflectionJob.objects.filter(friend=friend).delete()
        drop_online_history_index(friend.id)
        friend.memory = ""
        friend.conversation_summary = ""
        friend.summary_through_message_id = None
        friend.summary_updated_at = None
        friend.last_reflected_chat_day = None
        friend.save(update_fields=[
            "memory",
            "conversation_summary",
            "summary_through_message_id",
            "summary_updated_at",
            "last_reflected_chat_day",
        ])
        return {"result": "success", "deleted": deleted}
    except Friend.DoesNotExist:
        return {"result": "好友不存在"}
    except Exception:
        return {"result": "系统异常，请稍后重试"}
