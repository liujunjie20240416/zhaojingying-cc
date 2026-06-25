from fastapi import APIRouter, Depends, Query

from django.db.models import Q

from api.deps import get_current_user
from web.models.character import Character, Voice

router = APIRouter()


@router.get("/api/homepage/index/")
def homepage_index(items_count: int = Query(...), search_query: str = Query("")):
    try:
        search_query = search_query.strip()
        if search_query:
            queryset = Character.objects.filter(
                Q(name__icontains=search_query) | Q(profile__icontains=search_query)
            )
        else:
            queryset = Character.objects.all()

        characters_raw = queryset.order_by("-id")[
            items_count : items_count + 20
        ]
        characters = []
        for c in characters_raw:
            author = c.author
            characters.append(
                {
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
                }
            )
        return {"result": "success", "characters": characters}
    except Exception:
        return {"result": "系统异常，请稍后重试"}


@router.get("/api/create/character/voice/get_list/")
def get_voice_list(user=Depends(get_current_user)):
    try:
        voices_raw = Voice.objects.order_by("id")
        voices = [{"id": v.id, "name": v.name} for v in voices_raw]
        return {"result": "success", "voices": voices}
    except Exception:
        return {"result": "系统异常，请稍后重试"}
