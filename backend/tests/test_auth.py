from datetime import UTC, datetime, timedelta

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.auth.models import AuthAdminGuard, AuthSession, User
from app.modules.auth.schemas import LoginRequest
from app.modules.auth.service import (
    _decode_token,
    _session_digest,
    bootstrap_development_admin,
    login,
    verify_password,
)


def _login(client: TestClient, username: str, password: str) -> dict:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_login_and_read_current_user(client: TestClient) -> None:
    payload = _login(client, "  ADMIN  ", "admin123")
    assert payload["token_type"] == "bearer"
    assert payload["user"]["roles"] == ["ADMIN"]

    me_response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {payload['access_token']}"},
    )
    assert me_response.status_code == 200
    assert me_response.json()["username"] == "admin"


def test_login_persists_and_signs_the_same_whole_second(monkeypatch) -> None:
    fixed_now = datetime(2026, 9, 13, 14, 6, 0, 999_999, tzinfo=UTC)
    monkeypatch.setattr("app.modules.auth.service.utc_now_aware", lambda: fixed_now)
    engine = create_engine("sqlite://")
    User.__table__.create(engine)
    AuthSession.__table__.create(engine)
    AuthAdminGuard.__table__.create(engine)
    with Session(engine) as session:
        session.add(AuthAdminGuard(id=1))
        session.commit()
        bootstrap_development_admin(session, username="admin", password="admin123")
        token = login(
            session,
            LoginRequest(username="admin", password="admin123"),
        ).access_token
        payload = _decode_token(token)
        stored = session.get(AuthSession, _session_digest(str(payload["sid"])))
        assert stored is not None
        assert stored.issued_at == fixed_now.replace(tzinfo=None, microsecond=0)
        assert stored.expires_at.microsecond == 0
        assert int(stored.issued_at.replace(tzinfo=UTC).timestamp()) == payload["iat"]
        assert int(stored.expires_at.replace(tzinfo=UTC).timestamp()) == payload["exp"]
    engine.dispose()


def test_login_rejects_invalid_password(client: TestClient) -> None:
    wrong_password = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )
    missing_user = client.post(
        "/api/v1/auth/login",
        json={"username": "missing-user", "password": "wrong-password"},
    )
    assert wrong_password.status_code == missing_user.status_code == 401
    assert wrong_password.json()["code"] == missing_user.json()["code"] == "AUTH_ERROR"
    assert wrong_password.json()["message"] == missing_user.json()["message"]
    assert "wrong-password" not in wrong_password.text


def test_current_user_requires_token(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_ERROR"


def test_logout_revokes_current_session(client: TestClient) -> None:
    payload = _login(client, "admin", "admin123")
    headers = {"Authorization": f"Bearer {payload['access_token']}"}

    response = client.post("/api/v1/auth/logout", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"revoked": True}
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_change_password_revokes_every_existing_session(client: TestClient) -> None:
    first = _login(client, "admin", "admin123")
    second = _login(client, "admin", "admin123")
    changed = client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {first['access_token']}"},
        json={"current_password": "admin123", "new_password": "new-admin-password"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json() == {"changed": True, "reauthentication_required": True}
    for token in (first["access_token"], second["access_token"]):
        assert (
            client.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 401
        )
    assert client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
    ).status_code == 401
    assert _login(client, "admin", "new-admin-password")["user"]["id"] == "dev-admin"


def test_admin_user_management_is_persistent_and_revokes_changed_roles(
    client: TestClient,
) -> None:
    admin = _login(client, "admin", "admin123")
    admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
    created = client.post(
        "/api/v1/auth/users",
        headers=admin_headers,
        json={
            "username": "  Viewer.One  ",
            "display_name": "Viewer One",
            "password": "viewer-password",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["username"] == "viewer.one"
    assert body["platform_role"] == "USER"
    assert "password" not in created.text.lower()
    assert "hash" not in created.text.lower()

    viewer = _login(client, "VIEWER.ONE", "viewer-password")
    viewer_headers = {"Authorization": f"Bearer {viewer['access_token']}"}
    assert client.get("/api/v1/auth/users", headers=viewer_headers).status_code == 403
    assert client.post(
        "/api/v1/auth/users",
        headers=viewer_headers,
        json={
            "username": "forbidden-user",
            "display_name": "Forbidden User",
            "password": "forbidden-password",
        },
    ).status_code == 403
    assert client.patch(
        f"/api/v1/auth/users/{body['id']}",
        headers=viewer_headers,
        json={"platform_role": "ADMIN"},
    ).status_code == 403

    promoted = client.patch(
        f"/api/v1/auth/users/{body['id']}",
        headers=admin_headers,
        json={"platform_role": "ADMIN"},
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["auth_version"] == 2
    assert client.get("/api/v1/auth/me", headers=viewer_headers).status_code == 401
    promoted_login = _login(client, "viewer.one", "viewer-password")
    assert promoted_login["user"]["roles"] == ["ADMIN"]

    demoted = client.patch(
        f"/api/v1/auth/users/{body['id']}",
        headers=admin_headers,
        json={"platform_role": "USER"},
    )
    assert demoted.status_code == 200, demoted.text
    assert demoted.json()["auth_version"] == 3
    promoted_headers = {
        "Authorization": f"Bearer {promoted_login['access_token']}"
    }
    assert client.get("/api/v1/auth/me", headers=promoted_headers).status_code == 401
    demoted_login = _login(client, "viewer.one", "viewer-password")
    assert demoted_login["user"]["roles"] == ["USER"]

    disabled = client.patch(
        f"/api/v1/auth/users/{body['id']}",
        headers=admin_headers,
        json={"status": "DISABLED"},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["auth_version"] == 4
    demoted_headers = {"Authorization": f"Bearer {demoted_login['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=demoted_headers).status_code == 401
    assert client.post(
        "/api/v1/auth/login",
        json={"username": "viewer.one", "password": "viewer-password"},
    ).status_code == 401

    listed = client.get("/api/v1/auth/users?page=1&page_size=1", headers=admin_headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 2
    assert len(listed.json()["items"]) == 1


def test_last_active_admin_cannot_be_disabled_or_demoted(client: TestClient) -> None:
    admin = _login(client, "admin", "admin123")
    headers = {"Authorization": f"Bearer {admin['access_token']}"}
    for change in ({"status": "DISABLED"}, {"platform_role": "USER"}):
        response = client.patch(
            "/api/v1/auth/users/dev-admin", headers=headers, json=change
        )
        assert response.status_code == 409, response.text
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200


def test_legacy_token_without_persistent_session_is_rejected(client: TestClient) -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    legacy = jwt.encode(
        {
            "sub": "dev-admin",
            "username": "admin",
            "display_name": "开发管理员",
            "roles": ["ADMIN"],
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        settings.secret_key,
        algorithm="HS256",
    )
    response = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {legacy}"}
    )
    assert response.status_code == 401


def test_bootstrap_is_explicit_idempotent_and_never_overwrites_password() -> None:
    engine = create_engine("sqlite://")
    User.__table__.create(engine)
    AuthSession.__table__.create(engine)
    AuthAdminGuard.__table__.create(engine)
    with Session(engine) as session:
        session.add(AuthAdminGuard(id=1))
        session.commit()
        created = bootstrap_development_admin(
            session, username="Admin", password="admin123"
        )
        assert created.status == "CREATED"
        user = session.get(User, "dev-admin")
        assert user is not None
        original_hash = user.password_hash
        assert original_hash != "admin123"
        assert verify_password(original_hash, "admin123")

        repeated = bootstrap_development_admin(
            session, username="admin", password="replacement-password"
        )
        assert repeated.status == "ALREADY_EXISTS"
        session.refresh(user)
        assert user.password_hash == original_hash
        assert not verify_password(user.password_hash, "replacement-password")
    engine.dispose()
