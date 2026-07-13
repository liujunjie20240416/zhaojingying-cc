from fastapi import APIRouter, Depends, Query

from api.deps import get_current_user
from api.schemas import GetOrCreateFriendRequest, RemoveFriendRequest
from web.models.character import Character
from web.models.friend import Friend
from web.utils.user_profile import get_or_create_user_profile
from ai.memory.import_access import sync_imported_context_to_friend

router = APIRouter()


@router.get("/api/friend/get_list/")
def get_friend_list(
    items_count: int = Query(0), user=Depends(get_current_user)
):
    try:
        friends_raw = Friend.objects.filter(me__user=user).order_by("-update_time")[
            items_count : items_count + 20
        ]
        friends = []
        for f in friends_raw:
            c = f.character
            author = c.author
            friends.append(
                {
                    "id": f.id,
                    "character": {
                        "id": c.id,
                        "name": c.name,
                        "profile": c.profile,
                        "photo": c.photo.url,
                        "background_image": c.background_image.url,
                        "author": {
                            "user_id": author.user_id,
                            "username": author.user.username,
                            "photo": author.photo.url,
                        },
                    },
                }
            )
        return {"result": "success", "friends": friends}
    except Exception:
        return {"result": "系统异常，请稍后重试"}


@router.post("/api/friend/get_or_create/")
def get_or_create_friend(
    data: GetOrCreateFriendRequest, user=Depends(get_current_user)
):
    try:
        user_profile = get_or_create_user_profile(user)
        friends = Friend.objects.filter(
            character_id=data.character_id, me=user_profile
        )
        if friends.exists():
            friend = friends.first()
        else:
            friend = Friend.objects.create(
                character_id=data.character_id, me=user_profile
            )
            sync_imported_context_to_friend(friend)
        c = friend.character
        author = c.author
        return {
            "result": "success",
            "friend": {
                "id": friend.id,
                "character": {
                    "id": c.id,
                    "name": c.name,
                    "profile": c.profile,
                    "photo": c.photo.url,
                    "background_image": c.background_image.url,
                    "author": {
                        "user_id": author.user_id,
                        "username": author.user.username,
                        "photo": author.photo.url,
                    },
                },
            },
        }
    except Exception:
        return {"result": "系统异常，请稍后重试"}


@router.post("/api/friend/remove/")
def remove_friend(data: RemoveFriendRequest, user=Depends(get_current_user)):
    try:
        Friend.objects.filter(id=data.friend_id, me__user=user).delete()
        return {"result": "success"}
    except Exception:
        return {"result": "系统异常，请稍后重试"}
