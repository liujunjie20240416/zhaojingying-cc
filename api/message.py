from fastapi import APIRouter, Depends, Query
from django.utils.timezone import localtime

from api.deps import get_current_user
from api.schemas import RemoveFriendRequest
from web.models.friend import Message, Friend

router = APIRouter()


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
        messages_raw = queryset.order_by("-id")[:10]
        messages = [
            {
                "id": m.id,
                "user_message": m.user_message,
                "output": m.output,
                "create_time": localtime(m.create_time).isoformat(),
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
        friend.memory = ""
        friend.save()
        return {"result": "success", "deleted": deleted}
    except Friend.DoesNotExist:
        return {"result": "好友不存在"}
    except Exception:
        return {"result": "系统异常，请稍后重试"}
