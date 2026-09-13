import copy
import json
import os
from collections.abc import Generator
from dataclasses import dataclass
from decimal import Decimal

os.environ["APP_RUNNER_DISCONNECT_SCAN_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user, get_db_session
from app.main import app
from app.modules.auth.schemas import CurrentUser
from app.modules.projects.models import Project, ProjectMember
from app.modules.prompt_center.models import AiCallLog

FIRST_CANARY = "synthetic_ai_history_secret_4cf1"
SECOND_CANARY = "synthetic_ai_history_bearer_8a20"


@dataclass
class PrivacyClient:
    client: TestClient
    session_factory: sessionmaker[Session]


@pytest.fixture
def privacy_client() -> Generator[PrivacyClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    tables = (Project.__table__, ProjectMember.__table__, AiCallLog.__table__)
    for table in tables:
        table.create(engine)

    raw_response = json.dumps(
        {
            "summary": "diagnostic retained",
            "apiKey": f"{FIRST_CANARY} with spaces\nand newline",
            "database_dsn": (
                f"mysql+pymysql://reader:{SECOND_CANARY}@db.test:3306/app"
            ),
            "usage": {"input_token": 17, "outputToken": 9, "total-token": 26},
            "nested": json.dumps(
                {"cookie": SECOND_CANARY, "code": "E_SCHEMA", "attempt": 2}
            ),
        }
    )
    repair_response = (
        f"Authorization: Bearer {SECOND_CANARY}\n"
        f"password={FIRST_CANARY} with spaces; code=E_REPAIR "
        f"broker=amqps://worker:{SECOND_CANARY}@mq.test/vhost "
        f"url=https://user:{FIRST_CANARY}@example.test/callback?token="
        f"{SECOND_CANARY}&page=2"
    )
    parsed_result = {
        "summary": "safe result",
        "clientSecret": FIRST_CANARY,
        "usage": {"token_count": 31, "success": True},
        "nested": [{"access-token": SECOND_CANARY, "finding_id": "finding-7"}],
    }
    validation_errors = [
        f'path=$.summary password="{FIRST_CANARY} with spaces" code=required',
        json.dumps(
            {
                "authorization": f"Bearer {SECOND_CANARY}",
                "path": "$.risks",
                "count": 3,
            }
        ),
    ]

    with testing_session() as session:
        session.add(
            Project(
                id=1,
                name="AI history privacy",
                code="AI_HISTORY_PRIVACY",
                owner_id="owner",
            )
        )
        session.add_all(
            [
                ProjectMember(project_id=1, user_id="owner", role="PROJECT_OWNER"),
                ProjectMember(project_id=1, user_id="viewer", role="VIEWER"),
            ]
        )
        session.add(
            AiCallLog(
                id=1,
                project_id=1,
                task_type="WEB_FAILURE_ANALYSIS",
                entity_type="run",
                entity_id="run-privacy-1",
                model_config_id=11,
                actual_model="synthetic-model",
                prompt_version_id=21,
                output_schema_id=None,
                input_token=101,
                output_token=53,
                total_token=154,
                estimated_cost=Decimal("0.01234567"),
                latency_ms=432,
                success=False,
                fallback_used=True,
                retry_count=1,
                repair_used=True,
                error_type="STRUCTURED_OUTPUT_INVALID",
                response_id="response-safe-1",
                raw_response=raw_response,
                repair_response=repair_response,
                parsed_result=parsed_result,
                validation_errors=validation_errors,
            )
        )
        session.commit()

    def database_override() -> Generator[Session, None, None]:
        with testing_session() as session:
            yield session

    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_db_session] = database_override
    try:
        with TestClient(app) as client:
            yield PrivacyClient(client=client, session_factory=testing_session)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        for table in reversed(tables):
            table.drop(engine)
        engine.dispose()


def _identity(user_id: str, *, roles: list[str] | None = None) -> CurrentUser:
    return CurrentUser(
        id=user_id,
        username=user_id,
        display_name=user_id,
        roles=roles or [],
    )


def _storage_snapshot(session_factory: sessionmaker[Session]) -> dict[str, object]:
    with session_factory() as session:
        stored = session.get(AiCallLog, 1)
        assert stored is not None
        return {
            "raw_response": stored.raw_response,
            "repair_response": stored.repair_response,
            "parsed_result": copy.deepcopy(stored.parsed_result),
            "validation_errors": copy.deepcopy(stored.validation_errors),
        }


def test_authorized_ai_call_history_is_redacted_without_mutating_storage(
    privacy_client: PrivacyClient,
) -> None:
    before = _storage_snapshot(privacy_client.session_factory)

    identities = (
        _identity("viewer"),
        _identity("owner"),
        _identity("admin", roles=["ADMIN"]),
    )
    for identity in identities:
        app.dependency_overrides[get_current_user] = lambda identity=identity: identity
        response = privacy_client.client.get(
            "/api/v1/ai/calls",
            params={"project_id": 1, "task_type": "WEB_FAILURE_ANALYSIS"},
        )

        assert response.status_code == 200
        assert FIRST_CANARY not in response.text
        assert SECOND_CANARY not in response.text
        body = response.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["id"] == 1
        assert item["entity_id"] == "run-privacy-1"
        assert item["actual_model"] == "synthetic-model"
        assert item["input_token"] == 101
        assert item["output_token"] == 53
        assert item["total_token"] == 154
        assert Decimal(str(item["estimated_cost"])) == Decimal("0.01234567")
        assert item["latency_ms"] == 432
        assert item["error_type"] == "STRUCTURED_OUTPUT_INVALID"

        raw = json.loads(item["raw_response"])
        assert raw["summary"] == "diagnostic retained"
        assert raw["apiKey"] == "<redacted>"
        assert raw["database_dsn"] == (
            "mysql+pymysql://<redacted>@db.test:3306/app"
        )
        assert raw["usage"] == {
            "input_token": 17,
            "outputToken": 9,
            "total-token": 26,
        }
        nested_raw = json.loads(raw["nested"])
        assert nested_raw["cookie"] == "<redacted>"
        assert nested_raw["code"] == "E_SCHEMA"
        assert nested_raw["attempt"] == 2

        assert "code=E_REPAIR" in item["repair_response"]
        assert "broker=amqps://<redacted>@mq.test/vhost" in item["repair_response"]
        assert "example.test/callback" in item["repair_response"]
        assert "page=2" in item["repair_response"]
        assert item["parsed_result"]["summary"] == "safe result"
        assert item["parsed_result"]["clientSecret"] == "<redacted>"
        assert item["parsed_result"]["usage"] == {
            "token_count": 31,
            "success": True,
        }
        assert item["parsed_result"]["nested"][0]["finding_id"] == "finding-7"
        assert "path=$.summary" in item["validation_errors"][0]
        assert "code=required" in item["validation_errors"][0]
        nested_error = json.loads(item["validation_errors"][1])
        assert nested_error["authorization"] == "<redacted>"
        assert nested_error["path"] == "$.risks"
        assert nested_error["count"] == 3

    after = _storage_snapshot(privacy_client.session_factory)
    assert after == before
    assert FIRST_CANARY in str(after)
    assert SECOND_CANARY in str(after)


def test_ai_call_history_keeps_outsider_project_existence_hidden(
    privacy_client: PrivacyClient,
) -> None:
    before = _storage_snapshot(privacy_client.session_factory)
    app.dependency_overrides[get_current_user] = lambda: _identity("outsider")

    response = privacy_client.client.get(
        "/api/v1/ai/calls", params={"project_id": 1}
    )

    assert response.status_code == 404
    assert FIRST_CANARY not in response.text
    assert SECOND_CANARY not in response.text
    assert _storage_snapshot(privacy_client.session_factory) == before
