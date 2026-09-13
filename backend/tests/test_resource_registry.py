from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_session
from app.core.exceptions import AuthorizationError, ResourceConflictError
from app.main import app
from app.modules.auth.schemas import CurrentUser
from app.modules.projects.models import Project, ProjectMember
from app.modules.resource_registry import service as registry_service
from app.modules.resource_registry.models import ResourceRegistryEntry
from app.modules.resource_registry.schemas import (
    CleanupConfig,
    CleanupOutcome,
    CleanupStatus,
    ResourceRegisterRequest,
)
from app.modules.resource_registry.service import (
    CleanupExecutionResult,
    execute_cleanup,
    mark_cleanup_result,
    plan_cleanup,
    register_resource,
)
from app.modules.test_cases.runtime import preview_runtime
from app.modules.test_cases.schemas import (
    ApiRequestTemplate,
    RegisterResourceAction,
    RuntimePreviewRequest,
)
from tests.auth_helpers import install_test_auth, uninstall_test_auth


@pytest.fixture
def registry_context() -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tables = (Project.__table__, ProjectMember.__table__, ResourceRegistryEntry.__table__)
    for table in tables:
        table.create(engine)
    install_test_auth(engine)

    def override() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override
    try:
        with TestClient(app) as client:
            yield client, session_factory
    finally:
        app.dependency_overrides.clear()
        uninstall_test_auth(engine)
        for table in reversed(tables):
            table.drop(engine)
        engine.dispose()


def _scope(client: TestClient) -> tuple[dict[str, str], int]:
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    project = client.post(
        "/api/v1/projects", headers=headers, json={"name": "Registry", "code": "REGISTRY"}
    ).json()
    return headers, project["id"]


def _api_cleanup(url: str = "https://cleanup.example.test/users/{{resource_id}}") -> dict:
    return {
        "cleanup_type": "API",
        "method": "DELETE",
        "url": url,
        "policy": "ALWAYS",
    }


def test_register_sequence_lifo_plan_and_secret_free_summary(
    registry_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = registry_context
    headers, project_id = _scope(client)
    for resource_id in ("user-1", "order-1", "coupon-1"):
        response = client.post(
            "/api/v1/resource-registry",
            headers=headers,
            json={
                "project_id": project_id,
                "run_id": "run-lifo",
                "resource_type": resource_id.split("-")[0].upper(),
                "resource_id": resource_id,
                "cleanup": _api_cleanup(),
            },
        )
        assert response.status_code == 201, response.text

    listed = client.get(
        "/api/v1/resource-registry",
        headers=headers,
        params={"project_id": project_id, "run_id": "run-lifo"},
    )
    assert listed.status_code == 200
    assert [item["resource_id"] for item in listed.json()["items"]] == [
        "coupon-1",
        "order-1",
        "user-1",
    ]
    assert listed.json()["items"][0]["cleanup_config"]["header_names"] == []
    assert "value" not in str(listed.json())

    plan = client.post(
        "/api/v1/resource-registry/cleanup/plan",
        headers=headers,
        json={"project_id": project_id, "run_id": "run-lifo", "outcome": "FAILURE"},
    )
    assert [item["resource"]["resource_id"] for item in plan.json()["items"]] == [
        "coupon-1",
        "order-1",
        "user-1",
    ]
    assert all(item["action"] == "CLEAN" for item in plan.json()["items"])

def test_policy_outcomes_failures_continue_and_idempotency(
    registry_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = registry_context
    headers, project_id = _scope(client)
    entries: list[int] = []
    for resource_id, policy in (
        ("always", "ALWAYS"),
        ("success", "ON_SUCCESS"),
        ("failure", "ON_FAILURE"),
        ("never", "NEVER"),
    ):
        response = client.post(
            "/api/v1/resource-registry",
            headers=headers,
            json={
                "project_id": project_id,
                "run_id": "run-policy",
                "resource_type": "TEST",
                "resource_id": resource_id,
                "cleanup": {**_api_cleanup(), "policy": policy},
            },
        )
        entries.append(response.json()["id"])

    user = CurrentUser(id="dev-admin", username="admin", display_name="Admin", roles=["ADMIN"])
    with session_factory() as session:
        success_plan = plan_cleanup(
            session, user, project_id, "run-policy", CleanupOutcome.SUCCESS
        )
        decisions = {
            item.resource.resource_id: item.action for item in success_plan.items
        }
        assert decisions == {
            "never": "SKIP",
            "failure": "SKIP",
            "success": "CLEAN",
            "always": "CLEAN",
        }
        for outcome in (
            CleanupOutcome.FAILURE,
            CleanupOutcome.CANCELLED,
            CleanupOutcome.TIMEOUT,
        ):
            failure_plan = plan_cleanup(session, user, project_id, "run-policy", outcome)
            failure_decisions = {
                item.resource.resource_id: item.action for item in failure_plan.items
            }
            assert failure_decisions == {
                "never": "SKIP",
                "success": "SKIP",
                "failure": "CLEAN",
                "always": "CLEAN",
            }

        calls: list[str] = []

        def handler(entry: ResourceRegistryEntry) -> CleanupExecutionResult:
            calls.append(entry.resource_id)
            if entry.resource_id == "success":
                return CleanupExecutionResult(
                    status=CleanupStatus.FAILED,
                    error_code="MOCK_FAILED",
                    message="mock failure with token=never-store",
                )
            return CleanupExecutionResult(status=CleanupStatus.CLEANED, message="ok")

        result = execute_cleanup(
            session, user, project_id, "run-policy", CleanupOutcome.SUCCESS, handler=handler
        )
        assert calls == ["success", "always"]
        assert [item.status for item in result.items] == [
            CleanupStatus.SKIPPED,
            CleanupStatus.SKIPPED,
            CleanupStatus.FAILED,
            CleanupStatus.CLEANED,
        ]
        failed = session.scalar(
            select(ResourceRegistryEntry).where(ResourceRegistryEntry.resource_id == "success")
        )
        assert failed is not None
        assert failed.status == CleanupStatus.FAILED.value
        assert "token=never-store" not in (failed.last_error or "")

        before_retry_calls = list(calls)
        execute_cleanup(
            session, user, project_id, "run-policy", CleanupOutcome.SUCCESS, handler=handler
        )
        assert calls == before_retry_calls

        retry_plan = plan_cleanup(
            session,
            user,
            project_id,
            "run-policy",
            CleanupOutcome.SUCCESS,
            retry_failed=True,
        )
        assert [
            item.resource.resource_id for item in retry_plan.items if item.action == "CLEAN"
        ] == ["success"]
        execute_cleanup(
            session,
            user,
            project_id,
            "run-policy",
            CleanupOutcome.SUCCESS,
            retry_failed=True,
            handler=lambda _: CleanupExecutionResult(status=CleanupStatus.CLEANED),
        )
        cleaned = session.scalar(
            select(ResourceRegistryEntry).where(ResourceRegistryEntry.resource_id == "success")
        )
        assert cleaned is not None
        assert cleaned.status == CleanupStatus.CLEANED.value


def test_cleanup_dsl_rejects_sensitive_bypasses_and_accepts_templates() -> None:
    invalid = [
        {**_api_cleanup(), "connection_id": 1},
        {**_api_cleanup(), "sql": "DELETE FROM users WHERE id=%(id)s"},
        {**_api_cleanup(), "params": {"id": "1"}},
        {
            "cleanup_type": "SQL",
            "connection_id": 1,
            "sql": "DELETE FROM users WHERE id=%(id)s",
            "method": "DELETE",
        },
        {
            "cleanup_type": "SQL",
            "connection_id": 1,
            "sql": "DELETE FROM users WHERE id=%(id)s",
            "headers": [{"name": "X-Test", "value": "x"}],
        },
        {
            "cleanup_type": "SQL",
            "connection_id": 1,
            "sql": "DELETE FROM users WHERE id=%(id)s",
            "auth": {"type": "API_KEY", "secret_id": 1, "key_name": "X-Key"},
        },
        {
            "cleanup_type": "SQL",
            "connection_id": 1,
            "sql": "INSERT INTO users(id) VALUES (%(id)s)",
        },
        {**_api_cleanup(), "query_params": [{"name": "access_token", "value": "plain"}]},
        {
            **_api_cleanup(),
            "headers": [{"name": "X-Anything", "value": "Authorization: Bearer plain-token"}],
        },
        {**_api_cleanup(), "headers": [{"name": "X-Anything", "value": "Bearer x"}]},
        {
            **_api_cleanup(),
            "cookies": [{"name": "Set-Cookie", "value": "session=plain-cookie"}],
        },
        {
            **_api_cleanup(),
            "body": {"type": "JSON", "content": {"nested": [{"token": "plain"}]}},
        },
        {
            "cleanup_type": "SQL",
            "connection_id": 1,
            "sql": "DELETE FROM users WHERE id=%(resource_id)s",
            "params": {"api_key": "plain"},
        },
        {
            **_api_cleanup(),
            "body": {"type": "JSON", "content": {"note": "password=plain"}},
        },
    ]
    for payload in invalid:
        with pytest.raises((ValidationError, ValueError)):
            CleanupConfig.model_validate(payload)

    accepted = CleanupConfig.model_validate(
        {
            **_api_cleanup(),
            "query_params": [{"name": "access_token", "value": "{{access_token}}"}],
            "headers": [{"name": "Authorization", "value": "{{authorization}}"}],
            "url": "{{base_url}}/orders/{{resource_id}}",
        }
    )
    assert accepted.method.value == "DELETE"

    auth_invalid = [
        {"type": "NONE", "key_name": "X-Key"},
        {"type": "NONE", "placement": "QUERY"},
        {"type": "BEARER", "secret_id": 1, "key_name": "X-Key"},
        {"type": "BASIC", "credential_ref": "{{credential}}", "placement": "QUERY"},
        {"type": "BEARER", "secret_id": 1, "credential_ref": "{{credential}}"},
        {"type": "API_KEY", "secret_id": 1},
    ]
    for auth in auth_invalid:
        with pytest.raises((ValidationError, ValueError)):
            CleanupConfig.model_validate({**_api_cleanup(), "auth": auth})
    valid_bearer = CleanupConfig.model_validate(
        {**_api_cleanup(), "auth": {"type": "BEARER", "credential_ref": "{{access_token}}"}}
    )
    assert valid_bearer.auth.type == "BEARER"
    assert CleanupConfig.model_validate(
        {
            **_api_cleanup(),
            "body": {"type": "JSON", "content": {"description": "token count is 2"}},
        }
    )

    for invalid_url in (
        "ftp://example.test/resource",
        "https://example.test/{{resource_id}",
        "https://example.test/{{}}",
        "https://example.test/{{bad name}}",
        "https://example.test/{{resource_id}}}",
    ):
        with pytest.raises((ValidationError, ValueError)):
            CleanupConfig.model_validate({**_api_cleanup(), "url": invalid_url})

    with pytest.raises((ValidationError, ValueError)):
        CleanupConfig.model_validate(
            {
                **_api_cleanup(),
                "body": {"type": "JSON", "content": {"payload": "x" * 70000}},
            }
        )


def test_registry_project_boundary_permission_state_and_db_checks(
    registry_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = registry_context
    headers, project_id = _scope(client)
    created = client.post(
        "/api/v1/resource-registry",
        headers=headers,
        json={
            "project_id": project_id,
            "run_id": "run-boundary",
            "resource_type": "USER",
            "resource_id": "user-boundary",
            "cleanup": _api_cleanup(),
        },
    )
    assert created.status_code == 201
    resource_id = created.json()["id"]

    other_project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "Other Registry", "code": "OTHER_REGISTRY"},
    ).json()
    assert client.get(
        f"/api/v1/resource-registry/{resource_id}",
        headers=headers,
        params={"project_id": other_project["id"]},
    ).status_code == 404
    assert client.get(
        "/api/v1/resource-registry",
        headers=headers,
        params={"project_id": other_project["id"], "run_id": "run-boundary"},
    ).json()["total"] == 0

    viewer = CurrentUser(
        id="viewer-1", username="viewer", display_name="Viewer", roles=["VIEWER"]
    )
    admin = CurrentUser(id="dev-admin", username="admin", display_name="Admin", roles=["ADMIN"])
    with session_factory() as session:
        session.add(ProjectMember(project_id=project_id, user_id=viewer.id, role="VIEWER"))
        session.commit()
        with pytest.raises(AuthorizationError):
            register_resource(
                session,
                viewer,
                ResourceRegisterRequest(
                    project_id=project_id,
                    run_id="run-boundary",
                    resource_type="ORDER",
                    resource_id="order-boundary",
                    cleanup=CleanupConfig.model_validate(_api_cleanup()),
                ),
            )
        with pytest.raises(ResourceConflictError):
            mark_cleanup_result(
                session,
                admin,
                resource_id,
                CleanupExecutionResult(status=CleanupStatus.CLEANED),
            )
        invalid = ResourceRegistryEntry(
            project_id=project_id,
            run_id="run-invalid",
            resource_type="TEST",
            resource_id="invalid",
            cleanup_type="INVALID",
            cleanup_config=CleanupConfig.model_validate(_api_cleanup()).model_dump(mode="json"),
            registration_sequence=0,
            attempt_count=-1,
        )
        session.add(invalid)
        with pytest.raises(IntegrityError):
            session.commit()


def test_register_action_metadata_and_preview_are_secret_free_and_nonpersistent() -> None:
    with pytest.raises(ValueError):
        RegisterResourceAction(
            name="user", resource_type="USER", value="id-1", metadata={"token": "plain"}
        )
    cleanup = CleanupConfig.model_validate(_api_cleanup())
    payload = RuntimePreviewRequest(
        request=ApiRequestTemplate(method="GET", url="https://example.test"),
        post_actions=[
            RegisterResourceAction(
                name="user",
                resource_type="USER",
                value="id-1",
                cleanup=cleanup,
            )
        ],
    )
    result = preview_runtime(payload)
    assert result.context["__resources"]["user"]["preview_only"] is True
    assert result.context["__resources"]["user"]["cleanup"]["cleanup_type"] == "API"
    assert result.traces[-1].detail["persisted"] is False


def test_register_sequence_unique_conflict_retries(
    registry_context: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session_factory = registry_context
    headers, project_id = _scope(client)
    from app.modules.auth.schemas import CurrentUser

    user = CurrentUser(id="dev-admin", username="admin", display_name="Admin", roles=["ADMIN"])
    original = registry_service._next_sequence
    calls = 0

    def conflict_once(session: Session, scoped_project_id: int, run_id: str) -> int:
        nonlocal calls
        calls += 1
        return 1 if calls <= 2 else original(session, scoped_project_id, run_id)

    monkeypatch.setattr(registry_service, "_next_sequence", conflict_once)
    with session_factory() as session:
        register_resource(
            session,
            user,
            ResourceRegisterRequest(
                project_id=project_id,
                run_id="run-retry",
                resource_type="USER",
                resource_id="user-1",
                cleanup=CleanupConfig.model_validate(_api_cleanup()),
            ),
        )
        response = register_resource(
            session,
            user,
            ResourceRegisterRequest(
                project_id=project_id,
                run_id="run-retry",
                resource_type="ORDER",
                resource_id="order-1",
                cleanup=CleanupConfig.model_validate(_api_cleanup()),
            ),
        )
    assert calls >= 3
    assert response.registration_sequence == 2


def test_cleaning_is_not_replayed(
    registry_context: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = registry_context
    headers, project_id = _scope(client)
    registered = client.post(
        "/api/v1/resource-registry",
        headers=headers,
        json={
            "project_id": project_id,
            "run_id": "run-cleaning",
            "resource_type": "USER",
            "resource_id": "user-1",
            "cleanup": _api_cleanup(),
        },
    ).json()
    from app.modules.auth.schemas import CurrentUser

    user = CurrentUser(id="dev-admin", username="admin", display_name="Admin", roles=["ADMIN"])
    with session_factory() as session:
        entry = session.get(ResourceRegistryEntry, registered["id"])
        assert entry is not None
        entry.status = CleanupStatus.CLEANING.value
        session.commit()
        called = False

        def handler(_: ResourceRegistryEntry) -> CleanupExecutionResult:
            nonlocal called
            called = True
            return CleanupExecutionResult(status=CleanupStatus.CLEANED)

        result = execute_cleanup(
            session, user, project_id, "run-cleaning", CleanupOutcome.SUCCESS, handler=handler
        )
        assert called is False
        assert result.items[0].status == CleanupStatus.CLEANING


def test_sql_cleanup_rejects_insert_but_sql_execute_keeps_insert_semantics() -> None:
    from app.modules.resource_registry.security import validate_sql_cleanup, validate_sql_execute

    with pytest.raises(ResourceConflictError):
        validate_sql_cleanup("INSERT INTO users(id) VALUES (%(id)s)")
    assert validate_sql_execute("INSERT INTO users(id) VALUES (%(id)s)")
