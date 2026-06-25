from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from rest_framework_simplejwt.tokens import RefreshToken

from api.deps import get_current_user
from api.schemas import LoginRequest, RegisterRequest
from web.models.user import UserProfile

router = APIRouter()


def set_refresh_cookie(response: Response, refresh: RefreshToken):
    response.set_cookie(
        key="refresh_token",
        value=str(refresh),
        httponly=True,
        samesite="lax",
        secure=True,
        max_age=86400 * 7,
    )


@router.post("/api/user/account/login/")
def login(data: LoginRequest, response: Response):
    username = data.username.strip()
    password = data.password.strip()
    if not username or not password:
        return {"result": "用户名和密码不能为空"}

    user = authenticate(username=username, password=password)
    if not user:
        return {"result": "用户名或密码错误"}

    user_profile, _ = UserProfile.objects.get_or_create(user=user)
    refresh = RefreshToken.for_user(user)
    set_refresh_cookie(response, refresh)
    return {
        "result": "success",
        "access": str(refresh.access_token),
        "user_id": user.id,
        "username": user.username,
        "photo": user_profile.photo.url,
        "profile": user_profile.profile,
    }


@router.post("/api/user/account/register/")
def register(data: RegisterRequest, response: Response):
    username = data.username.strip()
    password = data.password.strip()
    if not username or not password:
        return {"result": "用户名和密码不能为空"}
    if User.objects.filter(username=username).exists():
        return {"result": "用户名已存在"}

    user = User.objects.create_user(username=username, password=password)
    user_profile = UserProfile.objects.create(user=user)
    refresh = RefreshToken.for_user(user)
    set_refresh_cookie(response, refresh)
    return {
        "result": "success",
        "access": str(refresh.access_token),
        "user_id": user.id,
        "username": user.username,
        "photo": user_profile.photo.url,
        "profile": user_profile.profile,
    }


@router.post("/api/user/account/logout/")
def logout(response: Response, user=Depends(get_current_user)):
    response.delete_cookie("refresh_token")
    return {"result": "success"}


@router.post("/api/user/account/refresh_token/")
def refresh_token(request: Request, response: Response):
    refresh_token_cookie = request.cookies.get("refresh_token")
    if not refresh_token_cookie:
        return JSONResponse({"result": "refresh_token不存在"}, status_code=401)

    try:
        refresh = RefreshToken(refresh_token_cookie)
        refresh.set_jti()
        set_refresh_cookie(response, refresh)
        return {"result": "success", "access": str(refresh.access_token)}
    except Exception:
        return JSONResponse({"result": "refresh token过期了"}, status_code=401)
