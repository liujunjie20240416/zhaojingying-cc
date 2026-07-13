from fastapi import APIRouter, Depends, Form, Query, UploadFile
import lancedb
from django.db import connection
from django.utils.timezone import now as djnow
from pathlib import Path

from api.deps import get_current_user
from api.schemas import RemoveCharacterRequest, UpdateImportedMemoryVisibilityRequest
from ai.memory.import_access import set_imported_context_visibility
from web.models.character import Character, Voice
from web.utils.photo import remove_old_photo
from web.utils.user_profile import get_or_create_user_profile

router = APIRouter()
_STORAGE_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "documents" / "lancedb_storage")


def _remove_import_artifacts(character_id: int):
    fts_table = f"chat_fts_{character_id}"
    with connection.cursor() as c:
        c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=%s",
            [fts_table],
        )
        if c.fetchone():
            c.execute(f'DROP TABLE "{fts_table}"')

    table_name = f"wechat_{character_id}"
    try:
        db = lancedb.connect(_STORAGE_DIR)
        if table_name in db.table_names():
            db.drop_table(table_name)
    except Exception:
        pass


@router.post("/api/create/character/create/")
def create_character(
    name: str = Form(...),
    voice_id: int = Form(...),
    profile: str = Form(...),
    photo: UploadFile = Form(...),
    background_image: UploadFile = Form(...),
    user=Depends(get_current_user),
):
    try:
        user_profile = get_or_create_user_profile(user)
        name = name.strip()
        profile = profile.strip()[:100000]

        if not name:
            return {"result": "名字不能为空"}
        if not profile:
            return {"result": "角色介绍不能为空"}
        if not photo.filename:
            return {"result": "头像不能为空"}
        if not background_image.filename:
            return {"result": "聊天背景不能为空"}

        voice = Voice.objects.get(id=voice_id)
        character = Character(
            author=user_profile,
            name=name,
            voice=voice,
            profile=profile,
        )
        character.photo.save(photo.filename, photo.file, save=False)
        character.background_image.save(
            background_image.filename, background_image.file, save=False
        )
        character.save()
        return {"result": "success", "character_id": character.id}
    except Exception:
        return {"result": "系统异常，请稍后重试"}


@router.post("/api/create/character/update/")
def update_character(
    character_id: int = Form(...),
    name: str = Form(...),
    voice_id: int = Form(...),
    profile: str = Form(...),
    photo: UploadFile | None = None,
    background_image: UploadFile | None = None,
    user=Depends(get_current_user),
):
    try:
        character = Character.objects.get(id=character_id, author__user=user)
        name = name.strip()
        profile = profile.strip()[:100000]

        if not name:
            return {"result": "名字不能为空"}
        if not profile:
            return {"result": "角色介绍不能为空"}

        if photo and photo.filename:
            remove_old_photo(character.photo)
            character.photo.save(photo.filename, photo.file, save=False)
        if background_image and background_image.filename:
            remove_old_photo(character.background_image)
            character.background_image.save(
                background_image.filename, background_image.file, save=False
            )

        voice = Voice.objects.get(id=voice_id)
        character.name = name
        character.voice = voice
        character.profile = profile
        character.update_time = djnow()
        character.save()
        return {"result": "success"}
    except Exception:
        return {"result": "系统异常，请稍后重试"}


@router.post("/api/create/character/remove/")
def remove_character(data: RemoveCharacterRequest, user=Depends(get_current_user)):
    try:
        character = Character.objects.get(pk=data.character_id, author__user=user)
        character_id = character.id
        remove_old_photo(character.photo)
        remove_old_photo(character.background_image)
        character.delete()
        _remove_import_artifacts(character_id)
        return {"result": "success"}
    except Exception:
        return {"result": "系统异常，请稍后重试"}


@router.post("/api/create/character/imported-memory-visibility/")
def update_imported_memory_visibility(
    data: UpdateImportedMemoryVisibilityRequest,
    user=Depends(get_current_user),
):
    try:
        character = Character.objects.get(id=data.character_id, author__user=user)
        set_imported_context_visibility(character, data.visibility)
        return {
            "result": "success",
            "visibility": character.imported_memory_visibility,
        }
    except Character.DoesNotExist:
        return {"result": "角色不存在或不属于你"}
    except ValueError as exc:
        return {"result": str(exc)}
    except Exception:
        return {"result": "系统异常，请稍后重试"}


@router.get("/api/create/character/get_single/")
def get_single_character(
    character_id: int = Query(...), user=Depends(get_current_user)
):
    try:
        character = Character.objects.get(id=character_id, author__user=user)
        voices_raw = Voice.objects.order_by("id")
        voices = [{"id": v.id, "name": v.name} for v in voices_raw]
        return {
            "result": "success",
            "character": {
                "id": character.id,
                "name": character.name,
                "profile": character.profile,
                "photo": character.photo.url,
                "background_image": character.background_image.url,
                "voice_id": character.voice_id,
                "style_profile": character.style_profile,
                "imported_memory_visibility": character.imported_memory_visibility,
            },
            "voices": voices,
        }
    except Exception:
        return {"result": "系统异常，请稍后重试"}


@router.get("/api/create/character/get_list/")
def get_list_character(items_count: int = Query(...), user_id: int = Query(...)):
    try:
        from django.contrib.auth.models import User as DjangoUser

        user = DjangoUser.objects.get(id=user_id)
        user_profile = get_or_create_user_profile(user)
        character_raw = Character.objects.filter(author=user_profile).order_by("-id")[
            items_count : items_count + 20
        ]
        characters = []
        for c in character_raw:
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
        return {
            "result": "success",
            "user_profile": {
                "user_id": user.id,
                "username": user.username,
                "profile": user_profile.profile,
                "photo": user_profile.photo.url,
            },
            "characters": characters,
        }
    except Exception:
        return {"result": "系统异常，请稍后重试"}
