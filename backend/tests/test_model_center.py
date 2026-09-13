import hashlib
from collections.abc import Generator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user, get_db_session
from app.main import app
from app.modules.ai_gateway import service as ai_gateway_service
from app.modules.auth.schemas import CurrentUser
from app.modules.model_center.models import (
    ModelConfiguration,
    ModelProviderConnection,
    ProjectModelBinding,
)
from app.modules.model_center.schemas import AiTaskType
from app.modules.projects.models import Project, ProjectMember
from app.modules.prompt_center.models import AiCallLog
from app.modules.secrets.models import Secret
from tests.auth_helpers import install_test_auth, uninstall_test_auth


@pytest.fixture
def model_client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tables = (
        Project.__table__, ProjectMember.__table__, Secret.__table__,
        ModelProviderConnection.__table__,
        ModelConfiguration.__table__, ProjectModelBinding.__table__,
        AiCallLog.__table__,
    )
    for table in tables:
        table.create(engine)
    install_test_auth(engine)

    def override() -> Generator[Session, None, None]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_db_session] = override
    try:
        with TestClient(app) as client:
            client.testing_session_factory = testing_session
            yield client
    finally:
        app.dependency_overrides.clear()
        uninstall_test_auth(engine)
        for table in reversed(tables):
            table.drop(engine)
        engine.dispose()


def _scope(client: TestClient) -> tuple[dict[str, str], int]:
    login = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    project = client.post(
        "/api/v1/projects", headers=headers,
        json={"name": "模型绑定项目", "code": "MODEL_CENTER_TEST"},
    )
    connection = client.post(
        "/api/v1/model-center/connections",
        headers=headers,
        json={
            "name": "默认模型渠道",
            "access_type": "DIRECT",
            "provider": "OPENAI",
            "protocol_type": "OPENAI_COMPATIBLE",
            "base_url": "http://127.0.0.1:11434/v1",
        },
    )
    assert connection.status_code == 201
    assert connection.json()["id"] == 1
    return headers, project.json()["id"]


def _model_payload(name: str, model_name: str) -> dict:
    return {
        "name": name, "connection_id": 1, "model_vendor": "OPENAI",
        "model_name": model_name,
        "model_type": "TEXT", "supports_structured_output": True,
        "max_context": 32768, "timeout_seconds": 30,
        "input_price": "0", "output_price": "0",
    }


def _connection_payload(name: str, *, api_key: str | None = None) -> dict:
    payload = {
        "name": name,
        "access_type": "DIRECT",
        "provider": "OPENAI",
        "protocol_type": "OPENAI_COMPATIBLE",
        "base_url": "http://127.0.0.1:11434/v1",
    }
    if api_key is not None:
        payload["api_key"] = api_key
    return payload


def _create_connection(
    client: TestClient,
    headers: dict[str, str],
    name: str,
    *,
    api_key: str | None = None,
) -> dict:
    response = client.post(
        "/api/v1/model-center/connections",
        headers=headers,
        json=_connection_payload(name, api_key=api_key),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _mock_provider(
    monkeypatch: pytest.MonkeyPatch,
    handler,
) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(
        ai_gateway_service,
        "_build_client",
        lambda timeout: httpx.Client(
            transport=httpx.MockTransport(wrapped), timeout=timeout
        ),
    )
    return requests


def _probe_model(client: TestClient, headers: dict[str, str], name: str) -> dict:
    response = client.post(
        "/api/v1/model-center",
        headers=headers,
        json=_model_payload(name, f"{name}-model"),
    )
    assert response.status_code == 201
    return response.json()


def test_model_crud_and_project_fallback_binding(model_client: TestClient) -> None:
    headers, project_id = _scope(model_client)
    primary = model_client.post(
        "/api/v1/model-center", headers=headers,
        json=_model_payload("主模型", "local-primary"),
    )
    fallback = model_client.post(
        "/api/v1/model-center", headers=headers,
        json=_model_payload("备用模型", "local-fallback"),
    )
    assert primary.status_code == fallback.status_code == 201

    binding = model_client.put(
        "/api/v1/model-center/bindings/project", headers=headers,
        json={
            "project_id": project_id, "task_type": "REQUIREMENT_REVIEW",
            "primary_model_id": primary.json()["id"],
            "fallback_model_id": fallback.json()["id"], "max_fallback": 1,
        },
    )
    assert binding.status_code == 200
    assert binding.json()["max_fallback"] == 1

    listed = model_client.get(
        f"/api/v1/model-center/bindings/project?project_id={project_id}",
        headers=headers,
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    disabled = model_client.patch(
        f"/api/v1/model-center/{fallback.json()['id']}",
        headers=headers, json={"enabled": False},
    )
    assert disabled.status_code == 200
    rejected = model_client.put(
        "/api/v1/model-center/bindings/project", headers=headers,
        json={
            "project_id": project_id, "task_type": "API_CASE_GENERATE",
            "primary_model_id": primary.json()["id"],
            "fallback_model_id": fallback.json()["id"], "max_fallback": 1,
        },
    )
    assert rejected.status_code == 409


def test_bulk_apply_model_binding_updates_all_tasks_atomically(
    model_client: TestClient,
) -> None:
    headers, project_id = _scope(model_client)
    primary = _probe_model(model_client, headers, "bulk-primary")
    fallback = _probe_model(model_client, headers, "bulk-fallback")

    applied_primary = model_client.put(
        "/api/v1/model-center/bindings/project/bulk-apply",
        headers=headers,
        json={
            "project_id": project_id,
            "model_id": primary["id"],
            "role": "PRIMARY",
        },
    )
    assert applied_primary.status_code == 200
    assert applied_primary.json()["applied_count"] == len(AiTaskType)
    assert applied_primary.json()["skipped_count"] == 0

    applied_fallback = model_client.put(
        "/api/v1/model-center/bindings/project/bulk-apply",
        headers=headers,
        json={
            "project_id": project_id,
            "model_id": fallback["id"],
            "role": "FALLBACK",
        },
    )
    assert applied_fallback.status_code == 200
    assert applied_fallback.json()["applied_count"] == len(AiTaskType)
    bindings = model_client.get(
        f"/api/v1/model-center/bindings/project?project_id={project_id}",
        headers=headers,
    ).json()["items"]
    assert len(bindings) == len(AiTaskType)
    assert all(item["primary_model_id"] == primary["id"] for item in bindings)
    assert all(item["fallback_model_id"] == fallback["id"] for item in bindings)
    assert all(item["max_fallback"] == 1 for item in bindings)

    promoted_fallback = model_client.put(
        "/api/v1/model-center/bindings/project/bulk-apply",
        headers=headers,
        json={
            "project_id": project_id,
            "model_id": fallback["id"],
            "role": "PRIMARY",
        },
    )
    assert promoted_fallback.status_code == 200
    assert promoted_fallback.json()["applied_count"] == len(AiTaskType)
    promoted_bindings = model_client.get(
        f"/api/v1/model-center/bindings/project?project_id={project_id}",
        headers=headers,
    ).json()["items"]
    assert all(item["primary_model_id"] == fallback["id"] for item in promoted_bindings)
    assert all(item["fallback_model_id"] is None for item in promoted_bindings)
    assert all(item["max_fallback"] == 0 for item in promoted_bindings)


def test_bulk_fallback_reports_tasks_without_compatible_primary(
    model_client: TestClient,
) -> None:
    headers, project_id = _scope(model_client)
    model = _probe_model(model_client, headers, "bulk-same-model")
    bound = model_client.put(
        "/api/v1/model-center/bindings/project",
        headers=headers,
        json={
            "project_id": project_id,
            "task_type": "REQUIREMENT_REVIEW",
            "primary_model_id": model["id"],
        },
    )
    assert bound.status_code == 200

    result = model_client.put(
        "/api/v1/model-center/bindings/project/bulk-apply",
        headers=headers,
        json={
            "project_id": project_id,
            "model_id": model["id"],
            "role": "FALLBACK",
        },
    )
    assert result.status_code == 200
    assert result.json()["applied_count"] == 0
    assert result.json()["skipped_count"] == len(AiTaskType)
    messages = {item["message"] for item in result.json()["items"]}
    assert "所选模型已是主模型，不能同时作为备用模型" in messages
    assert "尚未设置主模型，不能单独设置备用模型" in messages


def test_binding_rejects_models_with_insufficient_input_capacity(
    model_client: TestClient,
) -> None:
    headers, project_id = _scope(model_client)
    payload = _model_payload("小上下文专用模型", "specialized-small-context")
    payload["max_context"] = 4096
    model = model_client.post(
        "/api/v1/model-center", headers=headers, json=payload
    ).json()

    single = model_client.put(
        "/api/v1/model-center/bindings/project",
        headers=headers,
        json={
            "project_id": project_id,
            "task_type": "API_TEST_DESIGN",
            "primary_model_id": model["id"],
        },
    )
    assert single.status_code == 409
    assert "最大输入 4096 Token" in single.json()["message"]
    assert "至少需要 16384 Token" in single.json()["message"]

    bulk = model_client.put(
        "/api/v1/model-center/bindings/project/bulk-apply",
        headers=headers,
        json={
            "project_id": project_id,
            "model_id": model["id"],
            "role": "PRIMARY",
        },
    )
    assert bulk.status_code == 200
    assert bulk.json()["applied_count"] == 0
    assert bulk.json()["skipped_count"] == len(AiTaskType)
    assert all(
        "模型最大输入 4096 Token" in item["message"]
        for item in bulk.json()["items"]
    )


def test_delete_unreferenced_manual_model_returns_no_content(
    model_client: TestClient,
) -> None:
    headers, _ = _scope(model_client)
    model = _probe_model(model_client, headers, "delete-manual")

    deleted = model_client.delete(
        f"/api/v1/model-center/{model['id']}", headers=headers
    )

    assert deleted.status_code == 204
    assert deleted.content == b""
    assert model_client.get("/api/v1/model-center", headers=headers).json()["total"] == 0
    missing = model_client.delete(
        f"/api/v1/model-center/{model['id']}", headers=headers
    )
    assert missing.status_code == 404


def test_delete_model_unbinds_primary_and_clears_fallback_across_projects(
    model_client: TestClient,
) -> None:
    headers, first_project_id = _scope(model_client)
    second_project = model_client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "模型删除第二项目", "code": "MODEL_DELETE_TEST_2"},
    )
    assert second_project.status_code == 201
    second_project_id = second_project.json()["id"]
    primary_model = _probe_model(model_client, headers, "delete-primary")
    fallback_model = _probe_model(model_client, headers, "delete-fallback")

    def bind(project_id: int, task_type: str, primary_id: int, fallback_id: int | None = None):
        payload = {
            "project_id": project_id,
            "task_type": task_type,
            "primary_model_id": primary_id,
        }
        if fallback_id is not None:
            payload.update({"fallback_model_id": fallback_id, "max_fallback": 1})
        response = model_client.put(
            "/api/v1/model-center/bindings/project", headers=headers, json=payload
        )
        assert response.status_code == 200, response.text

    bind(first_project_id, "REQUIREMENT_REVIEW", primary_model["id"], fallback_model["id"])
    bind(first_project_id, "API_DOC_REVIEW", fallback_model["id"], primary_model["id"])
    bind(second_project_id, "REQUIREMENT_REVIEW", primary_model["id"])

    deleted = model_client.delete(
        f"/api/v1/model-center/{primary_model['id']}", headers=headers
    )
    assert deleted.status_code == 204

    first_bindings = model_client.get(
        f"/api/v1/model-center/bindings/project?project_id={first_project_id}",
        headers=headers,
    )
    assert first_bindings.status_code == 200
    assert first_bindings.json()["total"] == 1
    remaining = first_bindings.json()["items"][0]
    assert remaining["task_type"] == "API_DOC_REVIEW"
    assert remaining["primary_model_id"] == fallback_model["id"]
    assert remaining["fallback_model_id"] is None
    assert remaining["max_fallback"] == 0

    second_bindings = model_client.get(
        f"/api/v1/model-center/bindings/project?project_id={second_project_id}",
        headers=headers,
    )
    assert second_bindings.status_code == 200
    assert second_bindings.json()["total"] == 0


def test_delete_model_with_ai_history_is_blocked_before_unbinding(
    model_client: TestClient,
) -> None:
    headers, project_id = _scope(model_client)
    model = _probe_model(model_client, headers, "delete-with-history")
    fallback = _probe_model(model_client, headers, "delete-history-fallback")
    binding = model_client.put(
        "/api/v1/model-center/bindings/project",
        headers=headers,
        json={
            "project_id": project_id,
            "task_type": "REQUIREMENT_REVIEW",
            "primary_model_id": model["id"],
            "fallback_model_id": fallback["id"],
            "max_fallback": 1,
        },
    )
    assert binding.status_code == 200

    with model_client.testing_session_factory() as session:
        session.add(
            AiCallLog(
                project_id=project_id,
                task_type="REQUIREMENT_REVIEW",
                entity_type="test",
                entity_id="history-1",
                model_config_id=model["id"],
                actual_model=model["model_name"],
                prompt_version_id=1,
                input_token=1,
                output_token=1,
                total_token=2,
                estimated_cost=0,
                latency_ms=1,
                success=True,
                raw_response="{}",
            )
        )
        session.commit()

    blocked = model_client.delete(
        f"/api/v1/model-center/{model['id']}", headers=headers
    )
    assert blocked.status_code == 409
    assert "AI 调用历史" in blocked.json()["message"]
    unchanged = model_client.get(
        f"/api/v1/model-center/bindings/project?project_id={project_id}",
        headers=headers,
    )
    assert unchanged.status_code == 200
    item = unchanged.json()["items"][0]
    assert item["primary_model_id"] == model["id"]
    assert item["fallback_model_id"] == fallback["id"]
    assert item["max_fallback"] == 1


def test_delete_model_requires_admin_and_returns_not_found_for_missing_model(
    model_client: TestClient,
) -> None:
    headers, _ = _scope(model_client)
    model = _probe_model(model_client, headers, "delete-permission")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="viewer", username="viewer", display_name="Viewer", roles=["VIEWER"]
    )
    try:
        forbidden = model_client.delete(
            f"/api/v1/model-center/{model['id']}", headers=headers
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert forbidden.status_code == 403

    missing = model_client.delete("/api/v1/model-center/99999", headers=headers)
    assert missing.status_code == 404


def test_connection_metadata_and_disabled_visibility(model_client: TestClient) -> None:
    headers, _ = _scope(model_client)
    listed = model_client.get("/api/v1/model-center/connections", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["provider"] == "OPENAI"
    assert listed.json()["items"][0]["has_api_key"] is False
    assert listed.json()["items"][0]["api_key_masked"] is None

    disabled = model_client.post(
        "/api/v1/model-center/connections",
        headers=headers,
        json={
            "name": "停用渠道",
            "provider": "CUSTOM",
            "base_url": "https://provider.example/v1",
            "enabled": False,
        },
    )
    assert disabled.status_code == 201
    assert model_client.get(
        "/api/v1/model-center/connections", headers=headers
    ).json()["total"] == 1
    assert model_client.get(
        "/api/v1/model-center/connections?include_disabled=true", headers=headers
    ).json()["total"] == 2


def test_disabled_connection_is_excluded_and_blocks_binding_and_probe(
    model_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, project_id = _scope(model_client)
    model = _probe_model(model_client, headers, "disabled-channel-model")
    assert model_client.get("/api/v1/model-center", headers=headers).json()["total"] == 1

    disabled = model_client.patch(
        "/api/v1/model-center/connections/1",
        headers=headers,
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    assert model_client.get("/api/v1/model-center", headers=headers).json()["total"] == 0

    binding = model_client.put(
        "/api/v1/model-center/bindings/project",
        headers=headers,
        json={
            "project_id": project_id,
            "task_type": "REQUIREMENT_REVIEW",
            "primary_model_id": model["id"],
        },
    )
    assert binding.status_code == 409

    _mock_provider(
        monkeypatch,
        lambda _request: pytest.fail("disabled connection must not send a probe"),
    )
    probe = model_client.post(
        f"/api/v1/model-center/{model['id']}/verify-connection", headers=headers
    )
    assert probe.status_code == 200
    assert probe.json()["error_type"] == "CONNECTION_UNAVAILABLE"


def test_duplicate_connection_name_has_channel_error(model_client: TestClient) -> None:
    headers, _ = _scope(model_client)
    duplicate = model_client.post(
        "/api/v1/model-center/connections",
        headers=headers,
        json=_connection_payload("默认模型渠道"),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["message"] == "接入渠道名称已存在"

    second = _create_connection(model_client, headers, "第二模型渠道")
    conflict = model_client.patch(
        "/api/v1/model-center/connections/1",
        headers=headers,
        json={"name": second["name"]},
    )
    assert conflict.status_code == 409
    assert conflict.json()["message"] == "接入渠道名称已存在"


def test_model_api_key_create_preserve_rotate_and_clear(
    model_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, _ = _scope(model_client)
    monkeypatch.setattr(
        "app.modules.model_center.service.encrypt_secret",
        lambda value, _key: f"encrypted:{value}",
    )
    connection = _create_connection(
        model_client, headers, "api-key-connection", api_key="first-api-key"
    )
    model_payload = _model_payload("api-key-model", "api-key-model-name")
    model_payload["connection_id"] = connection["id"]
    created = model_client.post(
        "/api/v1/model-center", headers=headers, json=model_payload
    )
    assert created.status_code == 201
    created_body = created.json()
    assert created_body["connection_id"] == connection["id"]
    assert "first-api-key" not in created.text
    assert "encrypted" not in created.text
    assert not {
        "provider", "base_url", "api_key", "api_key_secret_id", "api_key_fingerprint"
    } & created_body.keys()
    with model_client.testing_session_factory() as session:
        stored = session.get(ModelProviderConnection, connection["id"])
        assert stored is not None
        assert stored.api_key_encrypted_value != "first-api-key"
        assert stored.api_key_fingerprint == hashlib.sha256(b"first-api-key").hexdigest()

    preserved = model_client.patch(
        f"/api/v1/model-center/connections/{connection['id']}",
        headers=headers,
        json={"name": "api-key-connection-preserved"},
    )
    assert preserved.status_code == 200
    assert preserved.json()["has_api_key"] is True
    assert preserved.json()["api_key_masked"] == "••••••••"
    assert preserved.json()["api_key_rotated_at"] is not None

    rotated = model_client.patch(
        f"/api/v1/model-center/connections/{connection['id']}",
        headers=headers,
        json={"api_key": "rotated-api-key"},
    )
    assert rotated.status_code == 200
    assert rotated.json()["has_api_key"] is True
    assert rotated.json()["api_key_rotated_at"] is not None
    assert "rotated-api-key" not in rotated.text

    cleared = model_client.patch(
        f"/api/v1/model-center/connections/{connection['id']}",
        headers=headers,
        json={"api_key": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["has_api_key"] is False
    assert cleared.json()["api_key_masked"] is None
    assert cleared.json()["api_key_rotated_at"] is None


def test_model_api_key_is_bounded_and_legacy_secret_id_is_not_accepted(
    model_client: TestClient,
) -> None:
    headers, _ = _scope(model_client)
    too_long = _connection_payload("api-key-too-long")
    too_long["api_key"] = "x" * 4097
    too_long_response = model_client.post(
        "/api/v1/model-center/connections", headers=headers, json=too_long
    )
    assert too_long_response.status_code == 422
    legacy = _model_payload("legacy-secret-field", "legacy-model")
    legacy["api_key_secret_id"] = 1
    legacy_response = model_client.post(
        "/api/v1/model-center", headers=headers, json=legacy
    )
    assert legacy_response.status_code == 422


def test_one_global_model_can_be_bound_to_two_projects(
    model_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, first_project_id = _scope(model_client)
    second_project = model_client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "第二模型项目", "code": "MODEL_CENTER_TEST_2"},
    )
    assert second_project.status_code == 201
    second_project_id = second_project.json()["id"]
    monkeypatch.setattr(
        "app.modules.model_center.service.encrypt_secret",
        lambda _value, _key: "encrypted-global-model-key",
    )
    model_payload = _model_payload("global-model", "global-model-name")
    model = model_client.post(
        "/api/v1/model-center", headers=headers, json=model_payload
    ).json()

    for project_id in (first_project_id, second_project_id):
        binding = model_client.put(
            "/api/v1/model-center/bindings/project",
            headers=headers,
            json={
                "project_id": project_id,
                "task_type": "REQUIREMENT_REVIEW",
                "primary_model_id": model["id"],
            },
        )
        assert binding.status_code == 200


def test_binding_rejects_same_model(model_client: TestClient) -> None:
    headers, project_id = _scope(model_client)
    model = model_client.post(
        "/api/v1/model-center", headers=headers,
        json=_model_payload("单模型", "only-model"),
    ).json()
    response = model_client.put(
        "/api/v1/model-center/bindings/project", headers=headers,
        json={
            "project_id": project_id, "task_type": "AI_ASSERTION",
            "primary_model_id": model["id"], "fallback_model_id": model["id"],
        },
    )
    assert response.status_code == 422


def test_verify_model_connection_uses_tiny_probe_without_call_log(
    model_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, _ = _scope(model_client)
    model = _probe_model(model_client, headers, "probe-success")
    saved_logs = []
    monkeypatch.setattr(
        ai_gateway_service,
        "_save_log",
        lambda *args, **kwargs: saved_logs.append((args, kwargs)),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["content-type"] == "application/json"
        assert request.headers.get("authorization") is None
        assert request.read() == (
            b'{"model":"probe-success-model","messages":[{"role":"user",'
            b'"content":"Reply with exactly OK."}]}'
        )
        return httpx.Response(
            200,
            json={
                "id": "response-with-secret",
                "choices": [{"message": {"content": "secret-response"}}],
            },
        )

    requests = _mock_provider(monkeypatch, handler)
    response = model_client.post(
        f"/api/v1/model-center/{model['id']}/verify-connection", headers=headers
    )

    assert response.status_code == 200
    assert set(response.json()) == {
        "model_id", "success", "status", "duration_ms", "summary", "error_type"
    }
    assert response.json()["success"] is True
    assert response.json()["status"] == "SUCCESS"
    assert response.json()["error_type"] is None
    assert "secret-response" not in response.text
    assert "response-with-secret" not in response.text
    assert requests
    assert saved_logs == []


def test_verify_model_connection_resolves_secret_but_never_returns_it(
    model_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, _ = _scope(model_client)
    monkeypatch.setattr(
        "app.modules.model_center.service.encrypt_secret",
        lambda value, _key: "encrypted-probe-key",
    )
    connection = _create_connection(
        model_client, headers, "probe-secret-connection", api_key="probe-super-secret"
    )
    model_payload = _model_payload("probe-secret", "secret-model")
    model_payload["connection_id"] = connection["id"]
    model = model_client.post(
        "/api/v1/model-center", headers=headers, json=model_payload
    ).json()
    monkeypatch.setattr(
        ai_gateway_service, "decrypt_secret", lambda *_args: "probe-super-secret"
    )
    captured_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(request.headers))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    _mock_provider(monkeypatch, handler)
    response = model_client.post(
        f"/api/v1/model-center/{model['id']}/verify-connection", headers=headers
    )
    assert response.status_code == 200
    assert captured_headers["authorization"] == "Bearer probe-super-secret"
    assert "probe-super-secret" not in response.text


@pytest.mark.parametrize(
    ("kind", "expected_type"),
    [
        ("http", "PROVIDER_REQUEST_ERROR"),
        ("timeout", "TIMEOUT"),
        ("network", "NETWORK_ERROR"),
        ("invalid", "PROVIDER_RESPONSE_INVALID"),
    ],
)
def test_verify_model_connection_returns_safe_structured_failures(
    model_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    expected_type: str,
) -> None:
    headers, _ = _scope(model_client)
    model = _probe_model(model_client, headers, f"probe-{kind}")

    def handler(_request: httpx.Request) -> httpx.Response:
        if kind == "timeout":
            raise httpx.ReadTimeout("provider secret timeout")
        if kind == "network":
            raise httpx.ConnectError("provider secret network failure")
        if kind == "invalid":
            return httpx.Response(200, json={"error": "response-secret", "choices": []})
        return httpx.Response(401, json={"error": "authorization-secret"})

    _mock_provider(monkeypatch, handler)
    response = model_client.post(
        f"/api/v1/model-center/{model['id']}/verify-connection", headers=headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["status"] == "FAILED"
    assert body["error_type"] == expected_type
    assert body["duration_ms"] >= 0
    assert "secret" not in response.text.lower()
    assert "provider" in body["summary"] or "模型" in body["summary"]


def test_verify_model_connection_rejects_unsafe_url_without_request(
    model_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, _ = _scope(model_client)
    payload = _connection_payload("probe-unsafe-url")
    payload["base_url"] = "https://provider.example/v1?api_key=secret-value"
    create_response = model_client.post(
        "/api/v1/model-center/connections", headers=headers, json=payload
    )
    assert create_response.status_code == 409
    assert "secret-value" not in create_response.text

    connection = _create_connection(
        model_client, headers, "probe-safe-before-update"
    )
    update_response = model_client.patch(
        f"/api/v1/model-center/connections/{connection['id']}", headers=headers,
        json={"base_url": payload["base_url"]},
    )
    assert update_response.status_code == 409
    assert "secret-value" not in update_response.text
    requests = _mock_provider(
        monkeypatch,
        lambda _request: pytest.fail("unsafe endpoint must not be contacted"),
    )
    assert requests == []


def test_verify_model_connection_is_admin_only(model_client: TestClient) -> None:
    headers, _ = _scope(model_client)
    model = _probe_model(model_client, headers, "probe-permission")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="viewer", username="viewer", display_name="Viewer", roles=["VIEWER"]
    )
    try:
        response = model_client.post(
            f"/api/v1/model-center/{model['id']}/verify-connection",
            headers={"Authorization": headers["Authorization"]},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 403
