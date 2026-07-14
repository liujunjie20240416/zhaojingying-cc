from fastapi import Request
from rest_framework_simplejwt.tokens import AccessToken
from django.contrib.auth.models import User

from api.errors import ApiError


def get_current_user(request: Request) -> User:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise ApiError(401, "authentication_required", "未认证")
    try:
        token = AccessToken(auth.split(" ")[1])
        user_id = token["user_id"]
        return User.objects.get(id=user_id)
    except Exception as exc:
        raise ApiError(401, "access_token_invalid", "token无效或已过期") from exc
