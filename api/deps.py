from fastapi import Depends, HTTPException, Request
from rest_framework_simplejwt.tokens import AccessToken
from django.contrib.auth.models import User


def get_current_user(request: Request) -> User:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, detail="未认证")
    try:
        token = AccessToken(auth.split(" ")[1])
        user_id = token["user_id"]
        return User.objects.get(id=user_id)
    except Exception as e:
        raise HTTPException(401, detail="token无效或已过期")
