from fastapi import APIRouter, Depends, Form, UploadFile
from django.contrib.auth.models import User
from django.utils.timezone import now

from api.deps import get_current_user
from web.models.user import UserProfile
from web.utils.photo import remove_old_photo
from web.utils.user_profile import get_or_create_user_profile

router = APIRouter()


@router.get("/api/user/account/get_user_info/")
def get_user_info(user=Depends(get_current_user)):
    try:
        user_profile = get_or_create_user_profile(user)
        return {
            "result": "success",
            "user_id": user.id,
            "username": user.username,
            "photo": user_profile.photo.url,
            "profile": user_profile.profile,
        }
    except Exception:
        return {"result": "系统异常，请稍后重试"}


@router.post("/api/user/profile/update/")
def update_profile(
    username: str = Form(...),
    profile: str = Form(...),
    photo: UploadFile | None = None,
    user=Depends(get_current_user),
):
    try:
        user_profile = get_or_create_user_profile(user)
        username = username.strip()
        profile = profile.strip()[:500]

        if not username:
            return {"result": "用户名不能为空"}
        if not profile:
            return {"result": "简介不能为空"}
        if username != user.username and User.objects.filter(username=username).exists():
            return {"result": "用户名已存在"}

        if photo and photo.filename:
            remove_old_photo(user_profile.photo)
            user_profile.photo.save(
                photo.filename,
                photo.file,
                save=False,
            )
        user_profile.profile = profile
        user_profile.update_time = now()
        user_profile.save()
        user.username = username
        user.save()

        return {
            "result": "success",
            "user_id": user.id,
            "username": user.username,
            "profile": user_profile.profile,
            "photo": user_profile.photo.url,
        }
    except Exception:
        return {"result": "系统异常，请稍后重试"}
