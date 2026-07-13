import io

import pytest
from django.contrib.auth.models import User
from fastapi.testclient import TestClient
from PIL import Image
from rest_framework_simplejwt.tokens import AccessToken

from main import app
from web.models.character import Character
from web.models.friend import Friend, MessageAttachment
from web.models.user import UserProfile


@pytest.mark.django_db(transaction=True)
def test_upload_chat_image_uses_safe_sync_django_boundary(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = User.objects.create_user(username="image-uploader", password="password")
    profile = UserProfile.objects.create(user=user)
    character = Character.objects.create(
        author=profile,
        name="测试角色",
        photo="character/photos/default.jpg",
        background_image="character/background_images/default.jpg",
        profile="温柔",
    )
    friend = Friend.objects.create(me=profile, character=character)

    image_buffer = io.BytesIO()
    Image.new("RGB", (32, 24), "red").save(image_buffer, format="PNG")
    token = str(AccessToken.for_user(user))

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/friend/message/attachment/upload/",
            data={"friend_id": str(friend.id)},
            files={"file": ("test.png", image_buffer.getvalue(), "image/png")},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["result"] == "success"
    assert payload["attachment"]["mime_type"] == "image/webp"
    assert payload["attachment"]["width"] == 32
    assert payload["attachment"]["height"] == 24
    assert MessageAttachment.objects.filter(friend=friend).count() == 1
