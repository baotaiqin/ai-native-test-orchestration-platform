import json
from collections.abc import Generator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_session
from app.core.exceptions import ResourceConflictError
from app.main import app
from app.modules.ai_gateway.schemas import AiGenerateRequest
from app.modules.ai_gateway.service import AiImageInput, _get_enabled_model, generate
from app.modules.auth.schemas import CurrentUser
from app.modules.model_center.models import (
    ModelConfiguration,
    ModelProviderConnection,
    ProjectModelBinding,
)
from app.modules.model_center.schemas import AiTaskType
from app.modules.projects.models import Project, ProjectMember
from app.modules.prompt_center.models import (
    AiCallLog,
    OutputSchema,
    PromptDefinition,
    PromptVersion,
)
from app.modules.secrets.models import Secret
from tests.auth_helpers import install_test_auth, uninstall_test_auth


@pytest.fixture
def gateway_client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tables = (
        Project.__table__, ProjectMember.__table__, Secret.__table__,
        ModelProviderConnection.__table__,
        ModelConfiguration.__table__, ProjectModelBinding.__table__,
        OutputSchema.__table__, PromptDefinition.__table__, PromptVersion.__table__,
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


def _scope(client: TestClient) -> tuple[dict[str, str], dict[str, int]]:
    login = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    project = client.post(
        "/api/v1/projects", headers=headers,
        json={"name": "Gateway 项目", "code": "AI_GATEWAY_TEST"},
    ).json()
    model_ids: list[int] = []
    for name in ("primary-model", "fallback-model"):
        connection = client.post(
            "/api/v1/model-center/connections",
            headers=headers,
            json={
                "name": f"{name}-connection",
                "provider": "OPENAI",
                "protocol_type": "OPENAI_COMPATIBLE",
                "base_url": "http://mock-provider/v1",
            },
        )
        assert connection.status_code == 201
        model = client.post(
            "/api/v1/model-center", headers=headers,
            json={
                "name": name, "connection_id": connection.json()["id"],
                "model_vendor": "OPENAI", "model_name": name,
                "model_type": "TEXT", "supports_structured_output": True,
                "input_price": "2", "output_price": "8",
            },
        ).json()
        model_ids.append(model["id"])
    binding = client.put(
        "/api/v1/model-center/bindings/project", headers=headers,
        json={
            "project_id": project["id"], "task_type": "REQUIREMENT_REVIEW",
            "primary_model_id": model_ids[0], "fallback_model_id": model_ids[1],
            "max_fallback": 1,
        },
    )
    assert binding.status_code == 200
    schema = client.post(
        "/api/v1/ai/output-schemas", headers=headers,
        json={
            "name": "GatewayReview",
            "schema_json": {
                "type": "object", "required": ["summary", "risks"],
                "properties": {
                    "summary": {"type": "string"},
                    "risks": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    ).json()
    prompt = client.post(
        "/api/v1/prompt-center", headers=headers,
        json={
            "name": "Gateway Review Prompt", "code": "GATEWAY_REVIEW_PROMPT",
            "task_type": "REQUIREMENT_REVIEW", "system_prompt": "输出 JSON",
            "user_template": "评审 {{ requirement }}", "output_schema_id": schema["id"],
        },
    ).json()
    return headers, {
        "project_id": project["id"], "primary_id": model_ids[0],
        "fallback_id": model_ids[1], "prompt_id": prompt["id"],
    }


def _request(ids: dict[str, int]) -> dict:
    return {
        "project_id": ids["project_id"], "task_type": "REQUIREMENT_REVIEW",
        "prompt_id": ids["prompt_id"], "variables": {"requirement": "用户登录"},
        "entity_type": "REQUIREMENT", "entity_id": "1",
    }


def _completion(model: str, content: str, response_id: str = "resp-1") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": response_id,
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
            "model": model,
        },
    )


def _mock_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    from app.modules.ai_gateway import service

    monkeypatch.setattr(
        service,
        "_build_client",
        lambda timeout: httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout),
    )


def test_internal_multimodal_generation_requires_visual_model_and_builds_image_message(
    gateway_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, ids = _scope(gateway_client)
    session_factory = gateway_client.testing_session_factory
    request = AiGenerateRequest(
        project_id=ids["project_id"],
        task_type=AiTaskType.REQUIREMENT_REVIEW,
        prompt_id=ids["prompt_id"],
        variables={"requirement": "masked screenshot"},
    )
    image = AiImageInput(mime="image/png", content=b"\x89PNG\r\n\x1a\nmasked")
    user = CurrentUser(
        id="dev-admin", username="admin", display_name="Admin", roles=["ADMIN"]
    )
    with session_factory() as session:
        with pytest.raises(ResourceConflictError, match="不支持图片输入"):
            generate(session, user, request, image_input=image)
        model = session.get(ModelConfiguration, ids["primary_id"])
        assert model is not None
        model.model_type = "VISION"
        session.commit()

    captured: dict = {}

    def handler(http_request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(http_request.content))
        return _completion(
            "primary-model",
            json.dumps({"summary": "ok", "risks": []}),
        )

    _mock_client(monkeypatch, handler)
    with session_factory() as session:
        response = generate(session, user, request, image_input=image)
    assert response.success is True
    content = captured["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "评审 masked screenshot"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_internal_generation_can_use_task_specific_timeout(
    gateway_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, ids = _scope(gateway_client)
    session_factory = gateway_client.testing_session_factory
    request = AiGenerateRequest(
        project_id=ids["project_id"],
        task_type=AiTaskType.REQUIREMENT_REVIEW,
        prompt_id=ids["prompt_id"],
        variables={"requirement": "慢模型后台任务"},
    )
    user = CurrentUser(
        id="dev-admin", username="admin", display_name="Admin", roles=["ADMIN"]
    )
    captured_timeouts: list[int] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        body = json.loads(http_request.content)
        return _completion(body["model"], '{"summary":"ok","risks":[]}')

    from app.modules.ai_gateway import service

    monkeypatch.setattr(
        service,
        "_build_client",
        lambda timeout: (
            captured_timeouts.append(timeout)
            or httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)
        ),
    )
    with session_factory() as session:
        response = generate(session, user, request, timeout_seconds=180)
    assert response.success is True
    assert captured_timeouts == [180]


def test_internal_generation_adds_trusted_system_instruction(
    gateway_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, ids = _scope(gateway_client)
    session_factory = gateway_client.testing_session_factory
    request = AiGenerateRequest(**_request(ids))
    user = CurrentUser(
        id="dev-admin", username="admin", display_name="Admin", roles=["ADMIN"]
    )
    captured_system_messages: list[str] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        body = json.loads(http_request.content)
        captured_system_messages.append(body["messages"][0]["content"])
        return _completion(body["model"], '{"summary":"ok","risks":[]}')

    _mock_client(monkeypatch, handler)
    with session_factory() as session:
        response = generate(
            session,
            user,
            request,
            trusted_system_instruction="每个业务接口必须关联至少一个检查点。",
        )
    assert response.success is True
    assert "输出 JSON" in captured_system_messages[0]
    assert "平台不可由项目提示词覆盖的输出契约" in captured_system_messages[0]
    assert "每个业务接口必须关联至少一个检查点" in captured_system_messages[0]


def test_retryable_primary_uses_fallback_once(
    gateway_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, ids = _scope(gateway_client)
    models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        models.append(body["model"])
        if body["model"] == "primary-model":
            return httpx.Response(503, json={"error": "unavailable"})
        return _completion(
            body["model"], '{"summary":"已评审","risks":["锁定策略"]}'
        )

    _mock_client(monkeypatch, handler)
    response = gateway_client.post(
        "/api/v1/ai/generate", headers=headers, json=_request(ids)
    )
    assert response.status_code == 200
    assert response.json()["actual_model"] == "fallback-model"
    assert response.json()["fallback_used"] is True
    assert response.json()["repair_used"] is False
    assert models == ["primary-model", "fallback-model"]

    log = gateway_client.get(
        f"/api/v1/ai/calls?project_id={ids['project_id']}", headers=headers
    ).json()["items"][0]
    assert log["model_config_id"] == ids["fallback_id"]
    assert log["retry_count"] == 1


def test_invalid_quality_repairs_same_model_without_fallback(
    gateway_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, ids = _scope(gateway_client)
    calls: list[tuple[str, int]] = []
    repair_feedback: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((body["model"], len(body["messages"])))
        if len(calls) == 1:
            return _completion(body["model"], '{"summary":"缺少 risks"}')
        repair_feedback.append(body["messages"][-1]["content"])
        return _completion(
            body["model"], '{"summary":"已修复","risks":["并发"]}', "resp-repair"
        )

    _mock_client(monkeypatch, handler)
    response = gateway_client.post(
        "/api/v1/ai/generate", headers=headers, json=_request(ids)
    )
    assert response.status_code == 200
    assert response.json()["actual_model"] == "primary-model"
    assert response.json()["fallback_used"] is False
    assert response.json()["repair_used"] is True
    assert response.json()["total_token"] == 3000
    assert calls == [("primary-model", 2), ("primary-model", 4)]
    assert repair_feedback[0].startswith("上一次输出不符合 JSON Schema。")


def test_domain_validator_feedback_identifies_exact_repair_action(
    gateway_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, ids = _scope(gateway_client)
    session_factory = gateway_client.testing_session_factory
    request = AiGenerateRequest(**_request(ids))
    user = CurrentUser(
        id="dev-admin", username="admin", display_name="Admin", roles=["ADMIN"]
    )
    repair_feedback: list[str] = []
    calls = 0

    def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(http_request.content)
        if calls == 1:
            return _completion(
                body["model"], '{"summary":"待修复","risks":[]}'
            )
        repair_feedback.append(body["messages"][-1]["content"])
        return _completion(
            body["model"], '{"summary":"已修复","risks":[]}', "resp-domain-repair"
        )

    _mock_client(monkeypatch, handler)
    with session_factory() as session:
        response = generate(
            session,
            user,
            request,
            result_validator=lambda value: (
                []
                if value.get("summary") == "已修复"
                else ["$.summary: 必须说明已完成可执行性修复"]
            ),
        )

    assert response.success is True
    assert response.repair_used is True
    assert calls == 2
    assert repair_feedback[0].startswith(
        "上一次输出不符合结构化输出或可执行性约束。"
    )
    assert "$.summary: 必须说明已完成可执行性修复" in repair_feedback[0]


def test_native_json_schema_rejection_retries_once_in_compatible_mode(
    gateway_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, ids = _scope(gateway_client)
    response_formats: list[dict | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        response_formats.append(body.get("response_format"))
        if body.get("response_format") is not None:
            return httpx.Response(400, json={"error": "json_schema unsupported"})
        return _completion(
            body["model"], '{"summary":"兼容模式成功","risks":[]}'
        )

    _mock_client(monkeypatch, handler)
    response = gateway_client.post(
        "/api/v1/ai/generate", headers=headers, json=_request(ids)
    )
    assert response.status_code == 200
    assert response.json()["actual_model"] == "primary-model"
    assert response.json()["fallback_used"] is False
    assert response_formats[0] is not None
    assert response_formats[1] is None

    log = gateway_client.get(
        f"/api/v1/ai/calls?project_id={ids['project_id']}", headers=headers
    ).json()["items"][0]
    assert log["retry_count"] == 1
    assert log["success"] is True


def test_compatible_model_receives_explicit_schema_contract(
    gateway_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, ids = _scope(gateway_client)
    with gateway_client.testing_session_factory() as session:
        model = session.get(ModelConfiguration, ids["primary_id"])
        assert model is not None
        model.supports_structured_output = False
        session.commit()
    captured_messages: list[list[dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured_messages.append(body["messages"])
        assert "response_format" not in body
        return _completion(
            body["model"], '{"summary":"兼容模型成功","risks":[]}'
        )

    _mock_client(monkeypatch, handler)
    response = gateway_client.post(
        "/api/v1/ai/generate", headers=headers, json=_request(ids)
    )
    assert response.status_code == 200
    schema_instruction = captured_messages[0][0]["content"]
    assert "必须严格符合以下结构" in schema_instruction
    assert '"required":["summary","risks"]' in schema_instruction


def test_non_retryable_error_does_not_fallback(
    gateway_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, ids = _scope(gateway_client)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "bad credentials"})

    _mock_client(monkeypatch, handler)
    response = gateway_client.post(
        "/api/v1/ai/generate", headers=headers, json=_request(ids)
    )
    assert response.status_code == 503
    assert response.json()["details"]["error_type"] == "PROVIDER_REQUEST_ERROR"
    assert calls == 1
    log = gateway_client.get(
        f"/api/v1/ai/calls?project_id={ids['project_id']}", headers=headers
    ).json()["items"][0]
    assert log["fallback_used"] is False
    assert log["error_type"] == "PROVIDER_REQUEST_ERROR"


def test_disabled_connection_is_rejected_before_provider_call(
    gateway_client: TestClient,
) -> None:
    _headers, ids = _scope(gateway_client)
    with gateway_client.testing_session_factory() as session:
        model = session.get(ModelConfiguration, ids["primary_id"])
        assert model is not None and model.connection is not None
        model.connection.enabled = False
        session.commit()
        with pytest.raises(ResourceConflictError, match="接入渠道"):
            _get_enabled_model(session, model.id)
