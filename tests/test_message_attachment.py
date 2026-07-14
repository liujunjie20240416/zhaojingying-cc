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
    assert payload["attachment"]["url"] == (
        f"/api/friend/message/attachment/{payload['attachment']['id']}/content/"
    )
    assert MessageAttachment.objects.filter(friend=friend).count() == 1

    attachment_url = payload["attachment"]["url"]
    with TestClient(app, raise_server_exceptions=False) as client:
        anonymous = client.get(attachment_url)
        owner = client.get(
            attachment_url,
            headers={"Authorization": f"Bearer {token}"},
        )

        other_user = User.objects.create_user(username="other-image-user")
        UserProfile.objects.create(user=other_user)
        other_token = str(AccessToken.for_user(other_user))
        other = client.get(
            attachment_url,
            headers={"Authorization": f"Bearer {other_token}"},
        )

    assert anonymous.status_code == 401
    assert other.status_code == 404
    assert owner.status_code == 200
    assert owner.headers["content-type"] == "image/webp"
    assert owner.headers["cache-control"] == "private, no-store"
    assert owner.content


def test_static_media_blocks_private_chat_image_paths(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from main import PublicMediaStaticFiles

    (tmp_path / "chat_images").mkdir()
    (tmp_path / "chat_images" / "private.webp").write_bytes(b"private")
    (tmp_path / "public.txt").write_text("public", encoding="utf-8")
    static_app = FastAPI()
    static_app.mount("/media", PublicMediaStaticFiles(directory=tmp_path))

    with TestClient(static_app) as client:
        assert client.get("/media/chat_images/private.webp").status_code == 404
        assert client.get("/media/public.txt").text == "public"
