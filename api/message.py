import hashlib
import io
import logging
import os
import uuid

from django.core.files.base import ContentFile
from django.db import transaction
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from django.utils.timezone import localtime, now
from PIL import Image, ImageOps, UnidentifiedImageError

from api.deps import get_current_user
from api.errors import ApiError
from api.schemas import RemoveFriendRequest
from web.models.friend import Message, Friend, MessageAttachment
from web.models.memory import EpisodicMemory, MemoryEvidence, SemanticMemory
from web.models.reflection_job import ReflectionJob
from ai.memory.history_search import drop_online_history_index
from ai.memory.semantic import delete_semantic_index_entries

router = APIRouter()
logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 30_000_000
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _attachment_payload(attachment: MessageAttachment) -> dict:
    return {
        "id": attachment.id,
        "url": f"/api/friend/message/attachment/{attachment.id}/content/",
        "mime_type": attachment.mime_type,
        "width": attachment.width,
        "height": attachment.height,
    }


@router.get("/api/friend/message/attachment/{attachment_id}/content/")
def get_attachment_content(attachment_id: int, user=Depends(get_current_user)):
    """Serve a private attachment only to the user who owns its Friend row."""
    attachment = MessageAttachment.objects.filter(
        id=attachment_id,
        friend__me__user=user,
    ).first()
    if not attachment:
        # Use the same response for a missing attachment and another user's
        # attachment so callers cannot enumerate private attachment ids.
        raise ApiError(404, "attachment_not_found", "图片不存在")
    try:
        path = attachment.file.path
    except (NotImplementedError, ValueError) as exc:
        raise ApiError(500, "attachment_storage_unavailable", "图片暂时无法读取") from exc
    if not os.path.isfile(path):
        raise ApiError(404, "attachment_file_not_found", "图片文件不存在")
    return FileResponse(
        path,
        media_type=attachment.mime_type,
        headers={"Cache-Control": "private, no-store"},
        content_disposition_type="inline",
    )


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
        raise ApiError(404, "friend_not_found", "好友不存在") from exc
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise ApiError(415, "unsupported_image_type", "仅支持 JPG、PNG 和 WebP 图片")
    # Keep the entire handler synchronous so FastAPI runs Django ORM, Pillow
    # decoding and storage writes in its worker thread instead of the event loop.
    raw = file.file.read(MAX_IMAGE_BYTES + 1)
    if len(raw) > MAX_IMAGE_BYTES:
        raise ApiError(413, "image_too_large", "图片不能超过 10MB")
    try:
        image = Image.open(io.BytesIO(raw))
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise ApiError(413, "image_dimensions_too_large", "图片分辨率过大")
        image.load()
        image = ImageOps.exif_transpose(image)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ApiError(415, "invalid_image", "图片文件无效或已损坏") from exc
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
    except Exception as exc:
        raise ApiError(500, "message_history_failed", "聊天记录加载失败，请稍后重试", True) from exc


@router.post("/api/friend/message/clear/")
def clear_history(
    data: RemoveFriendRequest,
    user=Depends(get_current_user),
):
    """Clear Online Chat and only the memories derived from Online Chat."""
    try:
        with transaction.atomic():
            friend = Friend.objects.select_for_update().get(
                id=data.friend_id,
                me__user=user,
            )
            attachment_files = [
                (attachment.file.storage, attachment.file.name)
                for attachment in MessageAttachment.objects.filter(friend=friend)
                if attachment.file.name
            ]
            ai_memory_ids = list(SemanticMemory.objects.filter(
                friend=friend,
                source="ai",
            ).values_list("id", flat=True))
            deleted = {
                "messages": Message.objects.filter(friend=friend).count(),
                "attachments": len(attachment_files),
                "semantic_memories": SemanticMemory.objects.filter(
                    friend=friend,
                    source="ai",
                ).count(),
                "online_evidences": MemoryEvidence.objects.filter(
                    memory__friend=friend,
                    source_type="online_chat",
                ).count(),
                "episodic_memories": EpisodicMemory.objects.filter(friend=friend).count(),
                "reflection_jobs": ReflectionJob.objects.filter(friend=friend).count(),
            }
            Message.objects.filter(friend=friend).delete()
            # Also remove uploads that were never attached to a completed Message.
            MessageAttachment.objects.filter(friend=friend).delete()
            ReflectionJob.objects.filter(friend=friend).delete()
            EpisodicMemory.objects.filter(friend=friend).delete()
            MemoryEvidence.objects.filter(
                memory__friend=friend,
                source_type="online_chat",
            ).delete()
            # Imported Chat is Character-scoped source material and user memories
            # are explicit assertions. Neither should disappear when the user
            # clears only this Friend's Online Chat.
            SemanticMemory.objects.filter(friend=friend, source="ai").delete()

            friend.conversation_summary = ""
            friend.summary_through_message_id = None
            friend.summary_updated_at = None
            friend.last_reflection_time = now()
            friend.last_reflected_chat_day = None
            friend.online_history_generation += 1
            friend.save(update_fields=[
                "conversation_summary",
                "summary_through_message_id",
                "summary_updated_at",
                "last_reflection_time",
                "last_reflected_chat_day",
                "online_history_generation",
            ])
        for storage, name in attachment_files:
            try:
                storage.delete(name)
            except Exception:
                # The protected endpoint is already inaccessible because its DB
                # row is gone; retain a trace so orphaned storage can be cleaned.
                logger.exception("Failed to delete cleared attachment %s", name)
        drop_online_history_index(friend.id)
        delete_semantic_index_entries(friend.id, ai_memory_ids)
        return {"result": "success", "deleted": deleted}
    except Friend.DoesNotExist as exc:
        raise ApiError(404, "friend_not_found", "好友不存在") from exc
    except Exception as exc:
        raise ApiError(500, "message_clear_failed", "聊天记录清除失败，请稍后重试", True) from exc
