import pytest
from django.contrib.auth.models import User
from fastapi.testclient import TestClient
from rest_framework_simplejwt.tokens import AccessToken

from main import app
from web.models.character import Character


def _auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {AccessToken.for_user(user)}"}


def _assert_error(response, *, status: int, code: str, retryable: bool):
    assert response.status_code == status, response.text
    payload = response.json()
    assert payload["result"] == "error"
    assert payload["error"]["code"] == code
    assert payload["error"]["message"]
    assert payload["error"]["retryable"] is retryable
    # Keep one compatibility field while the Vue callers migrate to error.message.
    assert payload["detail"] == payload["error"]["message"]


@pytest.mark.django_db(transaction=True)
def test_missing_resource_uses_standard_404_error():
    user = User.objects.create_user(username="api-errors-missing")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/api/create/character/get_single/",
            params={"character_id": 999_999},
            headers=_auth_headers(user),
        )

    _assert_error(
        response,
        status=404,
        code="character_not_found",
        retryable=False,
    )


@pytest.mark.django_db(transaction=True)
def test_business_validation_uses_standard_422_error():
    user = User.objects.create_user(username="api-errors-validation")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/friend/message/chat/",
            json={"friend_id": 123, "message": "", "attachment_ids": []},
            headers=_auth_headers(user),
        )

    _assert_error(
        response,
        status=422,
        code="empty_message",
        retryable=False,
    )


@pytest.mark.django_db(transaction=True)
def test_unexpected_exception_uses_standard_500_error(monkeypatch):
    user = User.objects.create_user(username="api-errors-internal")

    def fail(*args, **kwargs):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(Character.objects, "get", fail)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/api/create/character/get_single/",
            params={"character_id": 1},
            headers=_auth_headers(user),
        )

    _assert_error(
        response,
        status=500,
        code="internal_error",
        retryable=True,
    )
    assert "database exploded" not in response.text


def test_missing_authentication_uses_standard_401_error():
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/user/account/get_user_info/")

    _assert_error(
        response,
        status=401,
        code="authentication_required",
        retryable=False,
    )


@pytest.mark.django_db(transaction=True)
def test_invalid_login_uses_standard_401_error():
    User.objects.create_user(username="api-errors-login", password="correct-password")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/user/account/login/",
            json={"username": "api-errors-login", "password": "wrong-password"},
        )

    _assert_error(
        response,
        status=401,
        code="invalid_credentials",
        retryable=False,
    )
