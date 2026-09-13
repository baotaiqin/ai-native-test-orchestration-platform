from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user, get_db_session
from app.infrastructure.redis.client import (
    RedisStoreUnavailableError,
    get_runner_heartbeat_store,
)
from app.main import app
from app.modules.auth.schemas import CurrentUser
from app.modules.runners.models import (
    Runner,
    RunnerCapability,
    RunnerRegistrationToken,
    RunnerSlot,
    RunnerTag,
)
from app.modules.runners.security import digest_secret
from tests.auth_helpers import install_test_auth, uninstall_test_auth


class FakeRunnerHeartbeatStore:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any]] = {}
        self.ttls: dict[str, int] = {}
        self.fail_read = False
        self.fail_write = False
        self.fail_delete = False
        self.fail_unexpected_read = False

    def key_for(self, runner_id: str) -> str:
        return f"test:runner:heartbeat:{runner_id}"

    def set_heartbeat(self, runner_id: str, payload: dict[str, Any], *, ttl: int) -> None:
        if self.fail_write:
            raise RedisStoreUnavailableError
        key = self.key_for(runner_id)
        self.values[key] = payload.copy()
        self.ttls[key] = ttl

    def get_heartbeat(self, runner_id: str) -> dict[str, Any] | None:
        if self.fail_unexpected_read:
            raise RuntimeError("unexpected fake store failure")
        if self.fail_read:
            raise RedisStoreUnavailableError
        return self.values.get(self.key_for(runner_id))

    def delete_heartbeat(self, runner_id: str) -> None:
        if self.fail_delete:
            raise RedisStoreUnavailableError
        key = self.key_for(runner_id)
        self.values.pop(key, None)
        self.ttls.pop(key, None)


@pytest.fixture
def runner_context() -> Generator[
    tuple[TestClient, sessionmaker[Session], FakeRunnerHeartbeatStore], None, None
]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tables = (
        Runner.__table__,
        RunnerRegistrationToken.__table__,
        RunnerTag.__table__,
        RunnerCapability.__table__,
        RunnerSlot.__table__,
    )
    for table in tables:
        table.create(engine)
    install_test_auth(engine)

    fake_store = FakeRunnerHeartbeatStore()

    def override_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_runner_heartbeat_store] = lambda: fake_store
    try:
        with TestClient(app) as client:
            yield client, session_factory, fake_store
    finally:
        app.dependency_overrides.clear()
        uninstall_test_auth(engine)
        for table in reversed(tables):
            table.drop(engine)
        engine.dispose()


def _admin_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _runner_snapshot() -> dict[str, Any]:
    return {
        "name": "Local Runner",
        "hostname": "WIN-TEST",
        "ip": "127.0.0.1",
        "os": "Windows 11",
        "cpu": "8 cores",
        "ram": "16 GB",
        "disk": "100 GB",
        "python": "3.12.5",
        "chrome": "128",
        "playwright": "1.49",
        "java": "21",
        "jmeter": None,
        "tags": ["Windows", "web", "windows"],
        "capabilities": [
            {"name": "API", "status": "READY"},
            {"name": "WEB", "status": "READY"},
            {"name": "JMETER", "status": "UNAVAILABLE", "reason": "not installed"},
        ],
        "slots": [
            {"type": "API", "total": 5, "available": 5},
            {"type": "WEB", "total": 2, "available": 1},
            {"type": "PERFORMANCE", "total": 0, "available": 0},
        ],
    }


def _create_token(client: TestClient, headers: dict[str, str]) -> tuple[int, str]:
    response = client.post(
        "/api/v1/runners/registration-tokens",
        headers=headers,
        json={"expires_in_seconds": 60},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["id"], body["token"]


def _register(client: TestClient, token: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/runners/register",
        json={"registration_token": token, **_runner_snapshot()},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_registration_token_is_one_time_and_never_listed(
    runner_context: tuple[TestClient, sessionmaker[Session], FakeRunnerHeartbeatStore],
) -> None:
    client, session_factory, _ = runner_context
    headers = _admin_headers(client)
    token_id, token = _create_token(client, headers)

    listed_before = client.get("/api/v1/runners/registration-tokens", headers=headers)
    assert listed_before.status_code == 200
    assert token not in listed_before.text
    assert "token_digest" not in listed_before.text
    assert listed_before.json()["items"][0]["status"] == "ACTIVE"

    registered = _register(client, token)
    credential = registered["credential"]
    assert credential.startswith("rc_")
    assert token not in client.post(
        "/api/v1/runners/register",
        json={"registration_token": token, **_runner_snapshot()},
    ).text
    duplicate = client.post(
        "/api/v1/runners/register",
        json={"registration_token": token, **_runner_snapshot()},
    )
    assert duplicate.status_code == 401
    assert token not in duplicate.text
    assert credential not in duplicate.text

    listed_after = client.get("/api/v1/runners/registration-tokens", headers=headers)
    assert listed_after.json()["items"][0]["status"] == "CONSUMED"
    assert token not in listed_after.text
    assert credential not in listed_after.text

    with session_factory() as session:
        stored = session.get(RunnerRegistrationToken, token_id)
        assert stored is not None
        assert stored.token_digest == digest_secret(token)
        assert stored.token_digest != token
        assert stored.consumed_runner_id is not None


def test_registration_token_expiry_and_revoke_are_enforced(
    runner_context: tuple[TestClient, sessionmaker[Session], FakeRunnerHeartbeatStore],
) -> None:
    client, session_factory, _ = runner_context
    headers = _admin_headers(client)

    _, expired_token = _create_token(client, headers)
    with session_factory() as session:
        token = session.scalar(
            select(RunnerRegistrationToken).where(
                RunnerRegistrationToken.token_digest == digest_secret(expired_token)
            )
        )
        assert token is not None
        token.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        session.commit()
    expired = client.post(
        "/api/v1/runners/register",
        json={"registration_token": expired_token, **_runner_snapshot()},
    )
    assert expired.status_code == 401
    assert expired_token not in expired.text

    token_id, revoked_token = _create_token(client, headers)
    revoked = client.post(
        f"/api/v1/runners/registration-tokens/{token_id}/revoke",
        headers=headers,
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"
    rejected = client.post(
        "/api/v1/runners/register",
        json={"registration_token": revoked_token, **_runner_snapshot()},
    )
    assert rejected.status_code == 401
    assert revoked_token not in rejected.text


def test_registration_normalizes_tags_and_persists_bounded_capabilities_and_slots(
    runner_context: tuple[TestClient, sessionmaker[Session], FakeRunnerHeartbeatStore],
) -> None:
    client, session_factory, _ = runner_context
    token = _create_token(client, _admin_headers(client))[1]
    registered = _register(client, token)

    detail = client.get(
        f"/api/v1/runners/{registered['runner_id']}",
        headers=_admin_headers(client),
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["tags"] == ["web", "windows"]
    assert [item["name"] for item in body["capabilities"]] == ["API", "JMETER", "WEB"]
    assert body["slots"][-1] == {"type": "WEB", "total": 2, "available": 1}

    with session_factory() as session:
        runner = session.get(Runner, registered["runner_id"])
        assert runner is not None
        assert session.scalar(
            select(RunnerTag).where(RunnerTag.runner_id == runner.id, RunnerTag.tag == "windows")
        ) is not None

    invalid = _runner_snapshot()
    invalid["capabilities"] = [
        {"name": "API", "status": "READY"},
        {"name": "API", "status": "READY"},
    ]
    validation_token = _create_token(client, _admin_headers(client))[1]
    response = client.post(
        "/api/v1/runners/register",
        json={"registration_token": validation_token, **invalid},
    )
    assert response.status_code == 422
    assert validation_token not in response.text
    capability_details = response.json()["details"]
    assert capability_details
    assert {"loc", "type", "msg"} <= capability_details[0].keys()

    invalid_slots = _runner_snapshot()
    invalid_slots["slots"] = [{"type": "API", "total": 1, "available": 2}]
    response = client.post(
        "/api/v1/runners/register",
        json={
            "registration_token": _create_token(client, _admin_headers(client))[1],
            **invalid_slots,
        },
    )
    assert response.status_code == 422
    slot_details = response.json()["details"]
    assert slot_details
    assert {"loc", "type", "msg"} <= slot_details[0].keys()
    assert "slots" in str(slot_details)


def test_heartbeat_uses_separate_credential_and_refreshes_redis_ttl(
    runner_context: tuple[TestClient, sessionmaker[Session], FakeRunnerHeartbeatStore],
) -> None:
    client, session_factory, store = runner_context
    admin_headers = _admin_headers(client)
    registered = _register(client, _create_token(client, admin_headers)[1])
    runner_id = registered["runner_id"]
    credential = registered["credential"]

    snapshot = _runner_snapshot()
    snapshot["name"] = "Updated Runner"
    heartbeat = client.post(
        f"/api/v1/runners/{runner_id}/heartbeat",
        headers={"Authorization": f"Bearer {credential}"},
        json=snapshot,
    )
    assert heartbeat.status_code == 200, heartbeat.text
    heartbeat_body = heartbeat.json()
    assert heartbeat_body["runner_id"] == runner_id
    server_time = datetime.fromisoformat(
        heartbeat_body["server_time"].replace("Z", "+00:00")
    )
    assert server_time.tzinfo is not None
    assert server_time.utcoffset() is not None
    key = store.key_for(runner_id)
    assert store.values[key]["status"] == "ONLINE"
    assert store.ttls[key] == 90

    detail = client.get(f"/api/v1/runners/{runner_id}", headers=admin_headers)
    assert detail.status_code == 200
    assert detail.json()["online"] is True
    assert detail.json()["online_status"] == "ONLINE"
    assert detail.json()["name"] == "Updated Runner"

    with session_factory() as session:
        runner = session.get(Runner, runner_id)
        assert runner is not None
        assert runner.last_heartbeat_at is not None
        assert runner.credential_digest == digest_secret(credential)
        assert runner.credential_digest != credential

    user_jwt = admin_headers
    rejected_user_jwt = client.post(
        f"/api/v1/runners/{runner_id}/heartbeat",
        headers=user_jwt,
        json=snapshot,
    )
    assert rejected_user_jwt.status_code == 401
    assert "access_token" not in rejected_user_jwt.text


def test_revoked_runner_credential_cannot_heartbeat(
    runner_context: tuple[TestClient, sessionmaker[Session], FakeRunnerHeartbeatStore],
) -> None:
    client, _, store = runner_context
    admin_headers = _admin_headers(client)
    registered = _register(client, _create_token(client, admin_headers)[1])
    runner_id = registered["runner_id"]
    credential = registered["credential"]
    heartbeat_headers = {"Authorization": f"Bearer {credential}"}
    assert client.post(
        f"/api/v1/runners/{runner_id}/heartbeat",
        headers=heartbeat_headers,
        json=_runner_snapshot(),
    ).status_code == 200
    assert client.post(
        f"/api/v1/runners/{runner_id}/revoke", headers=admin_headers
    ).status_code == 200
    assert store.key_for(runner_id) not in store.values

    rejected = client.post(
        f"/api/v1/runners/{runner_id}/heartbeat",
        headers=heartbeat_headers,
        json=_runner_snapshot(),
    )
    assert rejected.status_code == 401
    assert credential not in rejected.text


def test_redis_unavailable_is_explicit_and_never_reported_online(
    runner_context: tuple[TestClient, sessionmaker[Session], FakeRunnerHeartbeatStore],
) -> None:
    client, _, store = runner_context
    admin_headers = _admin_headers(client)
    registered = _register(client, _create_token(client, admin_headers)[1])
    runner_id = registered["runner_id"]
    credential = registered["credential"]
    store.fail_write = True
    heartbeat = client.post(
        f"/api/v1/runners/{runner_id}/heartbeat",
        headers={"Authorization": f"Bearer {credential}"},
        json=_runner_snapshot(),
    )
    assert heartbeat.status_code == 503
    assert heartbeat.json()["code"] == "REDIS_UNAVAILABLE"
    assert credential not in heartbeat.text

    store.fail_write = False
    assert client.post(
        f"/api/v1/runners/{runner_id}/heartbeat",
        headers={"Authorization": f"Bearer {credential}"},
        json=_runner_snapshot(),
    ).status_code == 200
    store.fail_read = True
    detail = client.get(f"/api/v1/runners/{runner_id}", headers=admin_headers)
    assert detail.status_code == 200
    assert detail.json()["online"] is False
    assert detail.json()["online_status"] == "UNKNOWN"
    assert detail.json()["redis_available"] is False
    store.fail_read = False
    store.fail_unexpected_read = True
    with TestClient(app, raise_server_exceptions=False) as defect_client:
        defect = defect_client.get(f"/api/v1/runners/{runner_id}", headers=admin_headers)
    assert defect.status_code == 500
    assert credential not in defect.text


def test_only_admin_can_manage_runner_control_plane(
    runner_context: tuple[TestClient, sessionmaker[Session], FakeRunnerHeartbeatStore],
) -> None:
    client, _, _ = runner_context
    viewer = CurrentUser(id="viewer", username="viewer", display_name="Viewer", roles=["VIEWER"])
    app.dependency_overrides[get_current_user] = lambda: viewer
    try:
        response = client.post(
            "/api/v1/runners/registration-tokens", json={"expires_in_seconds": 60}
        )
        assert response.status_code == 403
        assert client.get("/api/v1/runners").status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
