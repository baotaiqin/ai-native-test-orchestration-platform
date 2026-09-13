import hashlib
import json
from copy import deepcopy
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from test_ai_case_generation import (
    CASES,
    REQUEST_TEMPLATE,
    _scope,
)
from test_ai_case_generation import case_client as _shared_case_client  # noqa: F401
from test_runs import (
    FakeRunHeartbeatStore,
    _create_run,
    _headers,
    _payload,
    _prepare_claimed_run,
)
from test_runs import run_context as _shared_run_context  # noqa: F401

from app.modules.runs.models import (
    CaseRun,
    RunApiExecutionResult,
    RunDispatchOutbox,
    StepRun,
)
from app.modules.runs.models import (
    TestRun as RunModel,
)
from app.modules.test_cases.models import TestCase as ApiCaseModel
from app.modules.test_cases.models import TestCaseVersion as CaseVersionModel
from app.modules.test_cases.retry_limit import (
    API_STEP_RETRY_LIMIT_ERROR_CODE,
    API_STEP_RETRY_LIMIT_ERROR_MESSAGE,
)


@pytest.fixture(name="_run_context_fixture")
def imported_run_context(request: pytest.FixtureRequest) -> Any:
    return request.getfixturevalue("_shared_run_context")


@pytest.fixture(name="_case_client_fixture")
def imported_case_client(request: pytest.FixtureRequest) -> Any:
    return request.getfixturevalue("_shared_case_client")


def _case_content(max_retries: Any, *, title: str = "V1 retry case") -> dict[str, Any]:
    request = deepcopy(REQUEST_TEMPLATE)
    request["retry_policy"] = {"max_retries": max_retries}
    return {
        **deepcopy(CASES["cases"][0]),
        "title": title,
        "request": request,
    }


def _set_version_retry_limit(
    session_factory: sessionmaker[Session],
    version_id: int,
    max_retries: int,
) -> None:
    with session_factory() as session:
        version = session.get(CaseVersionModel, version_id)
        assert version is not None
        content = deepcopy(version.content)
        request = deepcopy(content["request"])
        request["retry_policy"] = {
            "max_retries": max_retries,
            "backoff_ms": 500,
            "retry_on": ["TARGET_NETWORK_ERROR", "TARGET_TIMEOUT", "HTTP_5XX"],
        }
        content["request"] = request
        version.content = content
        session.commit()


def _assert_retry_limit_error(response: Any) -> None:
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == API_STEP_RETRY_LIMIT_ERROR_CODE
    assert body["message"] == API_STEP_RETRY_LIMIT_ERROR_MESSAGE
    assert body["details"] == {
        "maximum": 1,
        "remediation": "CREATE_NEW_CASE_VERSION",
    }


def test_formal_case_writes_accept_only_zero_or_one_and_preserve_historical_versions(
    _run_context_fixture: tuple[
        TestClient, sessionmaker[Session], FakeRunHeartbeatStore, dict[str, Any]
    ],
) -> None:
    client, session_factory, _, ids = _run_context_fixture
    headers = _headers(client)

    created_cases = []
    for max_retries in (0, 1):
        created = client.post(
            "/api/v1/test-cases",
            headers=headers,
            json={
                "project_id": ids["project_id"],
                "content": _case_content(max_retries, title=f"retry {max_retries}"),
            },
        )
        assert created.status_code == 201, created.text
        assert (
            created.json()["current_version"]["content"]["request"]["retry_policy"][
                "max_retries"
            ]
            == max_retries
        )
        created_cases.append(created.json())

    with session_factory() as session:
        before_rejections = session.scalar(select(func.count(ApiCaseModel.id)))
    for max_retries in (2, 3):
        rejected = client.post(
            "/api/v1/test-cases",
            headers=headers,
            json={
                "project_id": ids["project_id"],
                "content": _case_content(max_retries),
            },
        )
        _assert_retry_limit_error(rejected)
    for malformed in (True, -1, 4):
        rejected = client.post(
            "/api/v1/test-cases",
            headers=headers,
            json={
                "project_id": ids["project_id"],
                "content": _case_content(malformed),
            },
        )
        assert rejected.status_code == 422, rejected.text
    with session_factory() as session:
        assert session.scalar(select(func.count(ApiCaseModel.id))) == before_rejections

    case_id = created_cases[0]["id"]
    current_version_id = created_cases[0]["current_version"]["id"]
    for max_retries in (2, 3):
        rejected = client.post(
            f"/api/v1/test-cases/{case_id}/versions",
            headers=headers,
            json={
                "content": _case_content(max_retries),
                "change_note": "legacy retry must be rejected",
            },
        )
        _assert_retry_limit_error(rejected)
        detail = client.get(f"/api/v1/test-cases/{case_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["current_version_id"] == current_version_id

    valid_version = client.post(
        f"/api/v1/test-cases/{case_id}/versions",
        headers=headers,
        json={
            "content": _case_content(1, title="compliant replacement"),
            "change_note": "V1-compliant replacement",
        },
    )
    assert valid_version.status_code == 201, valid_version.text
    historical_version_id = valid_version.json()["id"]
    _set_version_retry_limit(session_factory, historical_version_id, 3)

    with session_factory() as session:
        historical = session.get(CaseVersionModel, historical_version_id)
        assert historical is not None
        raw_before = json.dumps(
            historical.content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    detail = client.get(f"/api/v1/test-cases/{case_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert (
        detail.json()["current_version"]["content"]["request"]["retry_policy"]["max_retries"]
        == 3
    )
    versions = client.get(f"/api/v1/test-cases/{case_id}/versions", headers=headers)
    assert versions.status_code == 200, versions.text
    historical = next(item for item in versions.json() if item["id"] == historical_version_id)
    assert historical["content"]["request"]["retry_policy"]["max_retries"] == 3
    with session_factory() as session:
        unchanged = session.get(CaseVersionModel, historical_version_id)
        assert unchanged is not None
        raw_after = json.dumps(
            unchanged.content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    assert hashlib.sha256(raw_after.encode()).digest() == hashlib.sha256(
        raw_before.encode()
    ).digest()


def test_ai_suggestion_acceptance_rejects_legacy_retry_without_partial_bulk_writes(
    _case_client_fixture: TestClient,
) -> None:
    case_client = _case_client_fixture
    headers, ids = _scope(case_client)
    generated = case_client.post(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/generations",
        headers=headers,
        json={"prompt_id": ids["prompt_id"]},
    )
    assert generated.status_code == 201, generated.text
    suggestions = generated.json()["suggestions"]
    first_id, second_id = (item["id"] for item in suggestions)

    for suggestion_id, max_retries in ((first_id, 2), (second_id, 3)):
        edited = case_client.patch(
            f"/api/v1/test-cases/suggestions/{suggestion_id}",
            headers=headers,
            json={"human_result": _case_content(max_retries)},
        )
        assert edited.status_code == 200, edited.text

    single = case_client.post(
        f"/api/v1/test-cases/suggestions/{first_id}/decision",
        headers=headers,
        json={"action": "ACCEPT"},
    )
    _assert_retry_limit_error(single)
    before = case_client.get(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/generations",
        headers=headers,
    )
    assert before.status_code == 200, before.text
    before_suggestions = before.json()["items"][0]["suggestions"]
    assert [item["status"] for item in before_suggestions] == ["DRAFT", "DRAFT"]
    assert [item["test_case_id"] for item in before_suggestions] == [None, None]

    first_compliant = case_client.patch(
        f"/api/v1/test-cases/suggestions/{first_id}",
        headers=headers,
        json={"human_result": _case_content(1, title="bulk first")},
    )
    assert first_compliant.status_code == 200, first_compliant.text
    snapshot = case_client.get(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/generations",
        headers=headers,
    ).json()["items"][0]["suggestions"]
    snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    bulk = case_client.post(
        "/api/v1/test-cases/suggestions/bulk-decision",
        headers=headers,
        json={"suggestion_ids": [first_id, second_id], "action": "ACCEPT"},
    )
    _assert_retry_limit_error(bulk)
    after_rejection = case_client.get(
        f"/api/v1/test-cases/requirements/{ids['requirement_id']}/generations",
        headers=headers,
    ).json()["items"][0]["suggestions"]
    assert json.dumps(after_rejection, ensure_ascii=False, sort_keys=True) == snapshot_json
    cases = case_client.get(
        "/api/v1/test-cases", headers=headers, params={"project_id": ids["project_id"]}
    )
    assert cases.status_code == 200
    assert cases.json() == []

    second_compliant = case_client.patch(
        f"/api/v1/test-cases/suggestions/{second_id}",
        headers=headers,
        json={"human_result": _case_content(0, title="bulk second")},
    )
    assert second_compliant.status_code == 200, second_compliant.text
    accepted = case_client.post(
        "/api/v1/test-cases/suggestions/bulk-decision",
        headers=headers,
        json={"suggestion_ids": [first_id, second_id], "action": "ACCEPT"},
    )
    assert accepted.status_code == 200, accepted.text
    assert [item["status"] for item in accepted.json()] == ["ACCEPTED", "ACCEPTED"]


def test_legacy_retry_versions_are_readable_but_validate_and_create_are_rejected(
    _run_context_fixture: tuple[
        TestClient, sessionmaker[Session], FakeRunHeartbeatStore, dict[str, Any]
    ],
) -> None:
    client, session_factory, store, ids = _run_context_fixture
    headers = _headers(client)

    for max_retries in (2, 3):
        _set_version_retry_limit(session_factory, ids["case_version_id"], max_retries)
        detail = client.get(f"/api/v1/test-cases/{ids['case_id']}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert (
            detail.json()["current_version"]["content"]["request"]["retry_policy"][
                "max_retries"
            ]
            == max_retries
        )
        validation = client.post(
            "/api/v1/runs/validate", headers=headers, json=_payload(ids)
        )
        assert validation.status_code == 200, validation.text
        assert validation.json()["valid"] is False
        issue = next(
            item
            for item in validation.json()["issues"]
            if item["code"] == API_STEP_RETRY_LIMIT_ERROR_CODE
        )
        assert issue == {
            "code": API_STEP_RETRY_LIMIT_ERROR_CODE,
            "message": API_STEP_RETRY_LIMIT_ERROR_MESSAGE,
            "field": "request.retry_policy.max_retries",
        }
        rejected = client.post("/api/v1/runs", headers=headers, json=_payload(ids))
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["code"] == "RUN_VALIDATION_FAILED"
        assert any(
            item["code"] == API_STEP_RETRY_LIMIT_ERROR_CODE
            for item in rejected.json()["details"]
        )

    with session_factory() as session:
        assert session.scalar(select(func.count(RunModel.id))) == 0
    assert store.publisher is not None and store.publisher.calls == []

    with session_factory() as session:
        legacy = session.get(CaseVersionModel, ids["case_version_id"])
        assert legacy is not None
        compliant = deepcopy(legacy.content)
    compliant["title"] = "V1-compliant replacement"
    compliant["request"]["retry_policy"]["max_retries"] = 1
    replacement = client.post(
        f"/api/v1/test-cases/{ids['case_id']}/versions",
        headers=headers,
        json={"content": compliant, "change_note": "replace legacy retry policy"},
    )
    assert replacement.status_code == 201, replacement.text
    assert replacement.json()["content"]["request"]["retry_policy"]["max_retries"] == 1
    validation = client.post("/api/v1/runs/validate", headers=headers, json=_payload(ids))
    assert validation.status_code == 200 and validation.json()["valid"] is True
    created = client.post("/api/v1/runs", headers=headers, json=_payload(ids))
    assert created.status_code == 201, created.text
    assert created.json()["case_version_id"] == replacement.json()["id"]
    versions = client.get(
        f"/api/v1/test-cases/{ids['case_id']}/versions", headers=headers
    )
    legacy = next(item for item in versions.json() if item["id"] == ids["case_version_id"])
    assert legacy["content"]["request"]["retry_policy"]["max_retries"] == 3


def test_dispatch_rechecks_retry_limit_before_mq_publish(
    _run_context_fixture: tuple[
        TestClient, sessionmaker[Session], FakeRunHeartbeatStore, dict[str, Any]
    ],
) -> None:
    client, session_factory, store, ids = _run_context_fixture
    headers = _headers(client)
    _set_version_retry_limit(session_factory, ids["case_version_id"], 1)
    run = _create_run(client, ids, headers)
    _set_version_retry_limit(session_factory, ids["case_version_id"], 2)

    rejected = client.post(f"/api/v1/runs/{run['id']}/dispatch", headers=headers)
    _assert_retry_limit_error(rejected)
    assert store.publisher is not None and store.publisher.calls == []
    with session_factory() as session:
        persisted = session.get(RunModel, run["id"])
        assert persisted is not None and persisted.status == "CREATED"
        assert session.scalar(
            select(func.count(RunDispatchOutbox.id)).where(
                RunDispatchOutbox.run_id == run["id"]
            )
        ) == 0


def test_claim_rechecks_retry_limit_before_assignment(
    _run_context_fixture: tuple[
        TestClient, sessionmaker[Session], FakeRunHeartbeatStore, dict[str, Any]
    ],
) -> None:
    client, session_factory, store, ids = _run_context_fixture
    headers = _headers(client)
    _set_version_retry_limit(session_factory, ids["case_version_id"], 1)
    run = _create_run(client, ids, headers)
    dispatched = client.post(f"/api/v1/runs/{run['id']}/dispatch", headers=headers)
    assert dispatched.status_code == 200, dispatched.text
    _set_version_retry_limit(session_factory, ids["case_version_id"], 2)

    rejected = client.post(
        f"/api/v1/runs/{run['id']}/claim",
        headers={"Authorization": "Bearer rc_run_test_credential"},
        json={"message_id": dispatched.json()["message_id"]},
    )
    _assert_retry_limit_error(rejected)
    with session_factory() as session:
        persisted = session.get(RunModel, run["id"])
        outbox = session.scalar(
            select(RunDispatchOutbox).where(RunDispatchOutbox.run_id == run["id"])
        )
        assert persisted is not None and persisted.status == "QUEUED"
        assert outbox is not None and outbox.claimed_runner_id is None
        assert outbox.claimed_at is None
    assert store.publisher is not None and len(store.publisher.calls) == 1


def test_plan_and_start_recheck_retry_limit_but_allow_compliant_execution(
    _run_context_fixture: tuple[
        TestClient, sessionmaker[Session], FakeRunHeartbeatStore, dict[str, Any]
    ],
) -> None:
    client, session_factory, store, ids = _run_context_fixture
    headers = _headers(client)
    _set_version_retry_limit(session_factory, ids["case_version_id"], 1)
    run, message_id = _prepare_claimed_run(client, store, ids, headers)
    case_run_id = run["case_runs"][0]["id"]
    runner_headers = {"Authorization": "Bearer rc_run_test_credential"}
    _set_version_retry_limit(session_factory, ids["case_version_id"], 3)

    plan = client.get(
        f"/api/v1/runs/{run['id']}/execution-plan",
        headers=runner_headers,
        params={"message_id": message_id, "case_run_id": case_run_id},
    )
    _assert_retry_limit_error(plan)
    assert "https://example.test" not in plan.text
    started = client.post(
        f"/api/v1/runs/{run['id']}/execution-start",
        headers=runner_headers,
        json={"message_id": message_id, "case_run_id": case_run_id},
    )
    _assert_retry_limit_error(started)
    with session_factory() as session:
        persisted = session.get(RunModel, run["id"])
        persisted_case = session.get(CaseRun, case_run_id)
        persisted_step = session.scalar(
            select(StepRun).where(StepRun.case_run_id == case_run_id)
        )
        assert persisted is not None and persisted.status == "ASSIGNED"
        assert persisted_case is not None and persisted_case.status == "CREATED"
        assert persisted_step is not None and persisted_step.status == "CREATED"
        assert session.scalar(
            select(func.count(RunApiExecutionResult.id)).where(
                RunApiExecutionResult.run_id == run["id"]
            )
        ) == 0
    assert store.evidence_store.put_calls == []

    _set_version_retry_limit(session_factory, ids["case_version_id"], 1)
    plan = client.get(
        f"/api/v1/runs/{run['id']}/execution-plan",
        headers=runner_headers,
        params={"message_id": message_id, "case_run_id": case_run_id},
    )
    assert plan.status_code == 200, plan.text
    assert plan.json()["request"]["retry_policy"]["max_retries"] == 1
    started = client.post(
        f"/api/v1/runs/{run['id']}/execution-start",
        headers=runner_headers,
        json={"message_id": message_id, "case_run_id": case_run_id},
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "RUNNING"
