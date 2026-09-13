from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_session
from app.infrastructure.db.base import Base
from app.infrastructure.object_store.client import get_object_store
from app.main import app
from app.modules.ai_gateway.service import ModelConnectionProbeResult
from app.modules.api_definitions.models import (
    ApiDefinition,
    ApiDefinitionImport,
    ApiDesignSuggestion,
    ApiScenarioPlan,
)
from app.modules.auth.models import AuthAdminGuard
from app.modules.demo_bootstrap.service import PROMPTS
from app.modules.environments.models import Environment
from app.modules.evidence.models import EvidenceArtifact
from app.modules.model_center.models import (
    ModelConfiguration,
    ModelProviderConnection,
    ProjectModelBinding,
)
from app.modules.projects.models import Project
from app.modules.prompt_center.models import PromptDefinition, PromptVersion
from app.modules.prompt_center.service import resolve_effective_prompt
from app.modules.requirement_reviews.models import RequirementReview
from app.modules.requirements.models import Requirement, RequirementDocumentVersion
from app.modules.runs.models import CaseRun
from app.modules.runs.models import TestRun as RunModel
from app.modules.scenarios.models import Scenario
from app.modules.secrets.models import Secret
from app.modules.test_cases.models import AiCaseGeneration
from app.modules.test_cases.models import TestCase as CaseModel
from app.modules.test_cases.models import TestCaseVersion as CaseVersionModel
from tests.auth_helpers import seed_test_user


class FakeObjectStore:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []

    def put_object(
        self, *, bucket: str, key: str, content: bytes, content_type: str
    ) -> None:
        del bucket, key, content, content_type

    def get_object(self, *, bucket: str, key: str) -> bytes:
        del bucket, key
        return b""

    def delete_object(self, *, bucket: str, key: str) -> None:
        self.deleted.append((bucket, key))


@pytest.fixture
def demo_client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with session_factory() as session:
        session.add(AuthAdminGuard(id=1))
        seed_test_user(
            session,
            user_id="demo-admin",
            username="admin",
            display_name="Demo 管理员",
            platform_role="ADMIN",
        )
        session.commit()

    def override_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    previous = dict(app.dependency_overrides)
    object_store = FakeObjectStore()
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_object_store] = lambda: object_store
    try:
        with TestClient(app) as client:
            client.demo_session_factory = session_factory
            client.demo_object_store = object_store
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        Base.metadata.drop_all(engine)
        engine.dispose()


def _headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _create_demo_model(
    client: TestClient,
    headers: dict[str, str],
    *,
    api_key: str,
) -> int:
    connection = client.post(
        "/api/v1/model-center/connections",
        headers=headers,
        json={
            "name": "测试模型渠道",
            "provider": "OPENAI_COMPATIBLE",
            "base_url": "https://provider.example.test/v1",
            "api_key": api_key,
        },
    )
    assert connection.status_code == 201, connection.text
    model = client.post(
        "/api/v1/model-center",
        headers=headers,
        json={
            "name": "测试主模型",
            "connection_id": connection.json()["id"],
            "model_vendor": "OPENAI_COMPATIBLE",
            "model_name": "demo-model",
            "supports_structured_output": False,
            "timeout_seconds": 180,
        },
    )
    assert model.status_code == 201, model.text
    return model.json()["id"]


def test_bootstrap_is_idempotent_and_never_returns_api_key(
    demo_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.demo_bootstrap import service as demo_service

    monkeypatch.setattr(
        demo_service,
        "verify_model_connection",
        lambda *_: ModelConnectionProbeResult(
            success=True,
            status="CONNECTED",
            duration_ms=12,
            summary="模型连接正常",
        ),
    )
    headers = _headers(demo_client)
    api_key = "test-only-api-key-never-return"
    model_id = _create_demo_model(demo_client, headers, api_key=api_key)
    payload = {"model_id": model_id}

    first = demo_client.post("/api/v1/demo/bootstrap", headers=headers, json=payload)
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["ready"] is True
    assert result["demo_url"] == "http://127.0.0.1:8765"
    assert result["probe"]["success"] is True
    assert result["assets"]["prompts"] == len(PROMPTS)
    assert result["assets"]["bindings"] == len(PROMPTS)
    assert result["assets"]["requirement"] is True
    assert result["assets"]["requirement_count"] == 20
    assert result["assets"]["openapi"] is True
    assert result["assets"]["api_definition_count"] == 14
    assert result["assets"]["runtime_secret"] is True
    assert api_key not in first.text
    assert "api_key" not in result

    second = demo_client.post(
        "/api/v1/demo/bootstrap",
        headers=headers,
        json=payload,
    )
    assert second.status_code == 200, second.text
    assert second.json()["project_id"] == result["project_id"]

    session_factory = demo_client.demo_session_factory
    with session_factory() as session:
        assert session.scalar(select(func.count(Project.id))) == 1
        assert session.scalar(select(func.count(PromptDefinition.id))) == len(PROMPTS)
        assert session.scalar(select(func.count(ProjectModelBinding.id))) == len(PROMPTS)
        environment = session.scalar(select(Environment))
        assert environment is not None
        assert environment.name == "在线智能商城 Demo"
        assert environment.base_url.rstrip("/") == result["demo_url"]
        assert session.scalar(select(func.count(RequirementReview.id))) == 0
        assert session.scalar(select(func.count(RequirementDocumentVersion.id))) == 1
        routed_requirements = {
            item.title: (item.verification_type, item.automation_readiness)
            for item in session.scalars(select(Requirement)).all()
        }
        assert routed_requirements["5.1 慢响应与超时"] == ("PERFORMANCE", "READY")
        assert routed_requirements["5.2 受控重试"] == ("PLATFORM", "READY")
        assert routed_requirements["6.1 登录页面"] == ("WEB", "READY")
        assert routed_requirements["7. 演示验收边界"] == (
            "PLATFORM",
            "MANUAL_ONLY",
        )
        assert session.scalar(select(func.count(AiCaseGeneration.id))) == 0
        assert session.scalar(select(func.count(CaseModel.id))) == 0
        assert session.scalar(select(func.count(Scenario.id))) == 0
        login_definition = session.scalar(
            select(ApiDefinition).where(ApiDefinition.path == "/api/login")
        )
        assert login_definition is not None
        username_schema = login_definition.request_schema["properties"]["username"]
        assert username_schema["example"] == "demo"
        assert username_schema["enum"] == ["demo"]
        runtime_secret = session.scalar(
            select(Secret).where(Secret.name == "DEMO_PASSWORD")
        )
        assert runtime_secret is not None
        assert runtime_secret.enabled is True
        assert runtime_secret.secret_type == "PASSWORD"
        assert runtime_secret.encrypted_value != "demo-pass"
        connection = session.scalar(select(ModelProviderConnection))
        assert connection is not None
        assert connection.api_key_encrypted_value != api_key
        model = session.scalar(select(ModelConfiguration))
        assert model is not None
        assert model.supports_structured_output is False

    status = demo_client.get("/api/v1/demo/status", headers=headers)
    assert status.status_code == 200
    assert status.json()["ready"] is True
    assert status.json()["has_api_key"] is True
    assert api_key not in status.text


def test_project_prompt_override_versions_and_restore_system_default(
    demo_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.demo_bootstrap import service as demo_service

    monkeypatch.setattr(
        demo_service,
        "verify_model_connection",
        lambda *_: ModelConnectionProbeResult(
            success=True,
            status="CONNECTED",
            duration_ms=5,
            summary="模型连接正常",
        ),
    )
    headers = _headers(demo_client)
    model_id = _create_demo_model(
        demo_client, headers, api_key="project-prompt-test-key"
    )
    bootstrap = demo_client.post(
        "/api/v1/demo/bootstrap",
        headers=headers,
        json={"model_id": model_id},
    ).json()
    project_id = bootstrap["project_id"]
    templates = demo_client.get(
        "/api/v1/prompt-center/project-templates",
        headers=headers,
        params={"project_id": project_id},
    )
    assert templates.status_code == 200, templates.text
    base = templates.json()["items"][0]
    assert base["using_system_default"] is True

    saved = demo_client.post(
        f"/api/v1/prompt-center/project-templates/{base['base_prompt_id']}/versions",
        headers=headers,
        params={"project_id": project_id},
        json={
            "system_prompt": base["system_prompt"] + "\n项目专属约束",
            "user_template": base["user_template"],
            "output_schema_id": base["output_schema_id"],
            "change_note": "项目首次定制",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["using_system_default"] is False
    assert saved.json()["project_version_no"] == 1
    with demo_client.demo_session_factory() as session:
        base_prompt = session.get(PromptDefinition, base["base_prompt_id"])
        assert base_prompt is not None
        effective = resolve_effective_prompt(session, project_id, base_prompt)
        assert effective.id == saved.json()["project_prompt_id"]

    restored = demo_client.post(
        f"/api/v1/prompt-center/project-templates/{base['base_prompt_id']}/restore",
        headers=headers,
        params={"project_id": project_id},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["using_system_default"] is True
    with demo_client.demo_session_factory() as session:
        base_prompt = session.get(PromptDefinition, base["base_prompt_id"])
        assert base_prompt is not None
        assert resolve_effective_prompt(session, project_id, base_prompt).id == base_prompt.id
    versions = demo_client.get(
        f"/api/v1/prompt-center/project-templates/{base['base_prompt_id']}/versions",
        headers=headers,
        params={"project_id": project_id},
    )
    assert [item["version_no"] for item in versions.json()] == [1]


def test_failed_probe_saves_only_model_setup(
    demo_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.demo_bootstrap import service as demo_service

    monkeypatch.setattr(
        demo_service,
        "verify_model_connection",
        lambda *_: ModelConnectionProbeResult(
            success=False,
            status="FAILED",
            duration_ms=8,
            summary="鉴权失败",
            error_type="AUTHENTICATION_ERROR",
        ),
    )
    headers = _headers(demo_client)
    model_id = _create_demo_model(
        demo_client, headers, api_key="invalid-test-key"
    )
    response = demo_client.post(
        "/api/v1/demo/bootstrap",
        headers=headers,
        json={"model_id": model_id},
    )
    assert response.status_code == 200
    assert response.json()["ready"] is False
    assert response.json()["assets"]["model"] is False
    assert response.json()["assets"]["project"] is False
    assert "invalid-test-key" not in response.text


def test_reset_rejects_bad_confirmation_and_restores_only_builtin_project_assets(
    demo_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.demo_bootstrap import service as demo_service

    monkeypatch.setattr(
        demo_service,
        "verify_model_connection",
        lambda *_: ModelConnectionProbeResult(
            success=True,
            status="CONNECTED",
            duration_ms=5,
            summary="模型连接正常",
        ),
    )
    headers = _headers(demo_client)
    model_id = _create_demo_model(demo_client, headers, api_key="reset-test-key")
    bootstrap = demo_client.post(
        "/api/v1/demo/bootstrap",
        headers=headers,
        json={"model_id": model_id},
    )
    assert bootstrap.status_code == 200, bootstrap.text
    project_id = bootstrap.json()["project_id"]

    session_factory = demo_client.demo_session_factory
    with session_factory() as session:
        connection_id = session.scalar(select(ModelProviderConnection.id))
        prompt_version_count = session.scalar(select(func.count(PromptVersion.id)))
        test_case = CaseModel(
            project_id=project_id,
            code="TC-RESET-ME",
            name="待重置用例",
            case_type="API",
            status="ACTIVE",
            source="MANUAL",
            created_by="demo-admin",
        )
        session.add_all(
            [
                Scenario(
                    project_id=project_id,
                    code="SC-RESET-ME",
                    name="待重置场景",
                    status="DRAFT",
                    created_by="demo-admin",
                ),
                test_case,
                Secret(
                    project_id=project_id,
                    environment_id=None,
                    name="CUSTOM_RESET_ME",
                    secret_type="TOKEN",
                    encrypted_value="encrypted-test-value",
                    fingerprint="1" * 64,
                    enabled=True,
                ),
            ]
        )
        session.flush()
        case_version = CaseVersionModel(
            case_id=test_case.id,
            version_no=1,
            content={},
            created_by="demo-admin",
        )
        session.add(case_version)
        session.flush()
        test_case.current_version_id = case_version.id
        run = RunModel(
            id="RUN-RESET-ME",
            run_code="RUN-RESET-ME",
            run_type="API_CASE",
            project_id=project_id,
            case_id=test_case.id,
            case_version_id=case_version.id,
            status="SUCCESS",
            trigger_type="MANUAL",
            required_capabilities=["API"],
            required_tags=[],
            required_slot_type="API",
            required_slot_count=1,
            total=1,
            pass_count=1,
            fail_count=0,
            review_count=0,
            timeout_count=0,
            created_by="demo-admin",
        )
        session.add(run)
        session.flush()
        case_run = CaseRun(
            run_id=run.id,
            sequence_no=1,
            case_id=test_case.id,
            case_version_id=case_version.id,
            status="SUCCESS",
            duration=10,
            retry_count=0,
        )
        session.add(case_run)
        session.flush()
        session.add(
            EvidenceArtifact(
                id="ART-RESET-ME",
                project_id=project_id,
                run_id=run.id,
                case_run_id=case_run.id,
                artifact_type="RESPONSE",
                file_name="response.json",
                mime="application/json",
                size=2,
                sha256="2" * 64,
                minio_bucket="test-evidence",
                minio_key="ai-demo/RUN-RESET-ME/response.json",
            )
        )
        session.commit()

    preview = demo_client.get("/api/v1/demo/reset-preview", headers=headers)
    assert preview.status_code == 200, preview.text
    preview_result = preview.json()
    assert preview_result["can_reset"] is True
    assert preview_result["delete_counts"]["scenarios"] == 1
    assert preview_result["delete_counts"]["test_cases"] == 1
    assert preview_result["delete_counts"]["runs"] == 1
    assert preview_result["delete_counts"]["evidence_files"] == 1
    assert preview_result["delete_counts"]["secrets"] == 2

    rejected = demo_client.post(
        "/api/v1/demo/reset",
        headers=headers,
        json={"confirmation": "ai_demo"},
    )
    assert rejected.status_code == 409

    reset = demo_client.post(
        "/api/v1/demo/reset",
        headers=headers,
        json={"confirmation": "AI_DEMO"},
    )
    assert reset.status_code == 200, reset.text
    result = reset.json()
    assert result["reset"] is True
    assert result["status"]["ready"] is True
    assert result["status"]["project_id"] == project_id
    assert result["deleted"]["scenarios"] == 1
    assert result["deleted"]["test_cases"] == 1
    assert result["deleted"]["runs"] == 1
    assert result["deleted"]["evidence_files"] == 1
    assert demo_client.demo_object_store.deleted == [
        ("test-evidence", "ai-demo/RUN-RESET-ME/response.json")
    ]

    with session_factory() as session:
        assert session.scalar(select(func.count(Project.id))) == 1
        assert session.scalar(select(func.count(Scenario.id))) == 0
        assert session.scalar(select(func.count(CaseModel.id))) == 0
        assert session.scalar(select(func.count(Environment.id))) == 1
        assert session.scalar(select(func.count(ProjectModelBinding.id))) == len(PROMPTS)
        assert session.scalar(select(func.count(RequirementDocumentVersion.id))) == 1
        assert session.scalar(select(func.count(Secret.id))) == 1
        assert session.scalar(select(Secret.name)) == "DEMO_PASSWORD"
        assert session.scalar(select(ModelProviderConnection.id)) == connection_id
        assert session.scalar(select(func.count(PromptVersion.id))) == prompt_version_count


def test_reset_is_blocked_while_ai_generation_is_active(
    demo_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.demo_bootstrap import service as demo_service

    monkeypatch.setattr(
        demo_service,
        "verify_model_connection",
        lambda *_: ModelConnectionProbeResult(
            success=True,
            status="CONNECTED",
            duration_ms=5,
            summary="模型连接正常",
        ),
    )
    headers = _headers(demo_client)
    model_id = _create_demo_model(demo_client, headers, api_key="reset-blocker-key")
    bootstrap = demo_client.post(
        "/api/v1/demo/bootstrap",
        headers=headers,
        json={"model_id": model_id},
    )
    project_id = bootstrap.json()["project_id"]
    session_factory = demo_client.demo_session_factory
    with session_factory() as session:
        import_id = session.scalar(select(ApiDefinitionImport.id))
        plan_prompt_id = session.scalar(
            select(PromptDefinition.id).where(
                PromptDefinition.task_type == "API_SCENARIO_PLAN"
            )
        )
        session.add(
            ApiScenarioPlan(
                project_id=project_id,
                import_id=import_id,
                prompt_id=plan_prompt_id,
                generation_status="RUNNING",
                active_key="ACTIVE",
                source_snapshot={},
                source_snapshot_sha256="1" * 64,
                source_snapshot_size=2,
                created_by="demo-admin",
            )
        )
        session.add(
            ApiDesignSuggestion(
                project_id=project_id,
                import_id=import_id,
                kind="SCENARIO",
                status="DRAFT",
                generation_status="RUNNING",
                draft_key="DRAFT",
                source_snapshot={},
                source_snapshot_sha256="0" * 64,
                source_snapshot_size=2,
                created_by="demo-admin",
            )
        )
        session.commit()

    preview = demo_client.get("/api/v1/demo/reset-preview", headers=headers)
    assert preview.status_code == 200
    assert preview.json()["can_reset"] is False
    assert preview.json()["active_operations"] == 2
    assert any("AI 场景方案规划" in item for item in preview.json()["blockers"])
    assert any("API 场景编排" in item for item in preview.json()["blockers"])

    reset = demo_client.post(
        "/api/v1/demo/reset",
        headers=headers,
        json={"confirmation": "AI_DEMO"},
    )
    assert reset.status_code == 409
    with session_factory() as session:
        assert session.scalar(select(func.count(ApiScenarioPlan.id))) == 1
        assert session.scalar(select(func.count(ApiDesignSuggestion.id))) == 1
