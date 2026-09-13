from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, update
from sqlalchemy.orm import Session, sessionmaker
from test_reports import _as, _identity
from test_reports import report_client as _shared_report_client  # noqa: F401
from test_runs import (
    FakeRunHeartbeatStore,
    _enable_web_runner,
    _headers,
    _payload,
)
from test_runs import run_context as _shared_run_context  # noqa: F401

from app.api.deps import get_current_user
from app.main import app
from app.modules.projects.models import Project
from app.modules.requirements.models import Requirement, RequirementVersion
from app.modules.run_requirement_snapshots.models import (
    RunRequirementCapture,
    RunRequirementSource,
)
from app.modules.run_requirement_snapshots.service import (
    capture_case_run_requirement_sources,
)
from app.modules.runs import service as runs_service
from app.modules.runs.models import CaseRun
from app.modules.runs.models import TestRun as RunModel
from app.modules.test_cases.models import RequirementCaseLink
from app.modules.test_cases.models import TestCase as ApiCase
from app.modules.test_cases.models import TestCaseVersion as ApiCaseVersion
from app.modules.web_cases.models import WebCase, WebCaseVersion


@pytest.fixture(name="snapshot_context")
def imported_run_context(request: pytest.FixtureRequest) -> Any:
    return request.getfixturevalue("_shared_run_context")


@pytest.fixture(name="legacy_report_client")
def imported_report_client(request: pytest.FixtureRequest) -> Any:
    return request.getfixturevalue("_shared_report_client")


def _requirement(
    session: Session,
    project_id: int,
    code: str,
    title: str,
    *,
    with_version: bool,
) -> tuple[Requirement, RequirementVersion | None]:
    requirement = Requirement(
        project_id=project_id,
        code=code,
        title=title,
        type="FEATURE",
        status="ACTIVE",
        created_by="dev-admin",
    )
    session.add(requirement)
    session.flush()
    if not with_version:
        return requirement, None
    version = RequirementVersion(
        requirement_id=requirement.id,
        version_no=1,
        markdown_content="private requirement token=must-not-escape",
        content_hash=f"{requirement.id:064x}"[-64:],
        source_type="MANUAL",
        created_by="dev-admin",
    )
    session.add(version)
    session.flush()
    requirement.current_version_id = version.id
    return requirement, version


def _link(
    *,
    requirement: Requirement,
    requirement_version: RequirementVersion | None,
    case_id: int,
    case_version_id: int | None,
    relation_type: str,
) -> RequirementCaseLink:
    return RequirementCaseLink(
        requirement_id=requirement.id,
        requirement_version_id=(
            requirement_version.id if requirement_version is not None else None
        ),
        asset_type="TEST_CASE",
        case_type="API",
        case_id=case_id,
        case_version_id=case_version_id,
        relation_type=relation_type,
        source="MANUAL",
        confidence=0.875,
        status="ACTIVE",
        active_slot=1,
        created_by="dev-admin",
        created_at_time_basis="UTC",
        created_at=datetime(2026, 9, 10, 8),
    )


def _web_link(
    *,
    requirement: Requirement,
    requirement_version: RequirementVersion | None,
    web_case_id: int,
    web_case_version_id: int | None,
    relation_type: str,
) -> RequirementCaseLink:
    return RequirementCaseLink(
        requirement_id=requirement.id,
        requirement_version_id=(
            requirement_version.id if requirement_version is not None else None
        ),
        asset_type="WEB_CASE",
        case_type=None,
        web_case_id=web_case_id,
        web_case_version_id=web_case_version_id,
        relation_type=relation_type,
        source="MANUAL",
        confidence=0.75,
        status="ACTIVE",
        active_slot=1,
        created_by="dev-admin",
        created_at_time_basis="UTC",
        created_at=datetime(2026, 9, 10, 8),
    )


def test_new_api_case_version_inherits_requirement_link_and_run_captures_it(
    snapshot_context: tuple[
        TestClient,
        sessionmaker[Session],
        FakeRunHeartbeatStore,
        dict[str, Any],
    ],
) -> None:
    client, session_factory, _, ids = snapshot_context
    headers = _headers(client)
    with session_factory() as session:
        requirement, requirement_version = _requirement(
            session,
            ids["project_id"],
            "REQ-INHERIT",
            "Inherited requirement",
            with_version=True,
        )
        assert requirement_version is not None
        original_link = _link(
            requirement=requirement,
            requirement_version=requirement_version,
            case_id=ids["case_id"],
            case_version_id=ids["case_version_id"],
            relation_type="COVERAGE",
        )
        session.add(original_link)
        session.commit()
        original_link_id = original_link.id
        requirement_id = requirement.id

    case_detail = client.get(
        f"/api/v1/test-cases/{ids['case_id']}", headers=headers
    )
    assert case_detail.status_code == 200, case_detail.text
    created_version = client.post(
        f"/api/v1/test-cases/{ids['case_id']}/versions",
        headers=headers,
        json={
            "content": case_detail.json()["current_version"]["content"],
            "change_note": "验证需求关联继承",
        },
    )
    assert created_version.status_code == 201, created_version.text
    new_version_id = created_version.json()["id"]

    with session_factory() as session:
        links = list(
            session.scalars(
                select(RequirementCaseLink)
                .where(RequirementCaseLink.requirement_id == requirement_id)
                .order_by(RequirementCaseLink.id.asc())
            )
        )
        assert len(links) == 2
        assert links[0].id == original_link_id
        assert links[0].status == "REMOVED"
        assert links[0].case_version_id == ids["case_version_id"]
        assert links[1].status == "ACTIVE"
        assert links[1].case_version_id == new_version_id
        assert links[1].requirement_version_id == requirement_version.id
        assert links[1].supersedes_link_id == original_link_id

    created_run = client.post("/api/v1/runs", headers=headers, json=_payload(ids))
    assert created_run.status_code == 201, created_run.text
    assert created_run.json()["case_version_id"] == new_version_id
    report = client.get(
        f"/api/v1/reports/{created_run.json()['id']}", headers=headers
    )
    assert report.status_code == 200, report.text
    sources = report.json()["requirement_sources"]
    assert sources["captures"][0]["status"] == "CAPTURED"
    assert sources["total"] == 1
    assert sources["items"][0]["requirement_code"] == "REQ-INHERIT"
    assert sources["items"][0]["link_asset_version_id"] == new_version_id


def test_create_run_freezes_exact_and_unknown_sources_for_report_and_exports(
    snapshot_context: tuple[
        TestClient,
        sessionmaker[Session],
        FakeRunHeartbeatStore,
        dict[str, Any],
    ],
) -> None:
    client, session_factory, store, ids = snapshot_context
    headers = _headers(client)
    with session_factory() as session:
        case = session.get(ApiCase, ids["case_id"])
        assert case is not None
        other_version = ApiCaseVersion(
            case_id=case.id,
            version_no=2,
            content={"title": "not executed"},
            created_by="dev-admin",
        )
        session.add(other_version)
        exact, exact_version = _requirement(
            session, ids["project_id"], "REQ-EXACT", "Exact title", with_version=True
        )
        legacy, _ = _requirement(
            session, ids["project_id"], "REQ-LEGACY", "Legacy title", with_version=False
        )
        wrong_version, wrong_requirement_version = _requirement(
            session, ids["project_id"], "REQ-WRONG", "Wrong version", with_version=True
        )
        same_number, same_number_version = _requirement(
            session, ids["project_id"], "REQ-WEB", "Same numeric Web", with_version=True
        )
        session.flush()

        same_id_web_case = WebCase(
            id=case.id,
            project_id=ids["project_id"],
            code="WEB-SAME-ID",
            name="same numeric id, different asset type",
            status="APPROVED",
            created_by="dev-admin",
        )
        session.add(same_id_web_case)
        session.flush()
        same_id_web_version = WebCaseVersion(
            web_case_id=same_id_web_case.id,
            version_no=1,
            content={"name": "not executed", "actions": [], "assertions": []},
            status="APPROVED",
            approved_by="dev-admin",
            approved_at=datetime(2026, 9, 10, 8),
            created_by="dev-admin",
        )
        session.add(same_id_web_version)
        session.flush()
        same_id_web_case.current_version_id = same_id_web_version.id

        exact_link = _link(
            requirement=exact,
            requirement_version=exact_version,
            case_id=case.id,
            case_version_id=ids["case_version_id"],
            relation_type="COVERAGE",
        )
        legacy_link = _link(
            requirement=legacy,
            requirement_version=None,
            case_id=case.id,
            case_version_id=None,
            relation_type="TRACE",
        )
        session.add_all(
            [
                exact_link,
                legacy_link,
                _link(
                    requirement=wrong_version,
                    requirement_version=wrong_requirement_version,
                    case_id=case.id,
                    case_version_id=other_version.id,
                    relation_type="COVERAGE",
                ),
                RequirementCaseLink(
                    requirement_id=same_number.id,
                    requirement_version_id=same_number_version.id,
                    asset_type="WEB_CASE",
                    case_type=None,
                    web_case_id=same_id_web_case.id,
                    web_case_version_id=same_id_web_version.id,
                    relation_type="COVERAGE",
                    source="MANUAL",
                    confidence=1,
                    status="ACTIVE",
                    active_slot=1,
                    created_by="dev-admin",
                    created_at_time_basis="UTC",
                    created_at=datetime(2026, 9, 10, 8),
                ),
            ]
        )
        session.commit()
        exact_id = exact.id
        exact_version_id = exact_version.id if exact_version is not None else None
        exact_link_id = exact_link.id
        legacy_link_id = legacy_link.id
        other_version_id = other_version.id

    engine = session_factory.kw["bind"]
    statements: list[str] = []

    def record_statement(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if "requirement_case_links" in statement.lower():
            statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        created_response = client.post(
            "/api/v1/runs", headers=headers, json=_payload(ids)
        )
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)
    assert created_response.status_code == 201, created_response.text
    assert len(statements) == 1
    assert "left outer join requirements" in statements[0]
    assert "left outer join requirement_versions" in statements[0]
    assert " limit " in statements[0]
    assert "markdown_content" not in statements[0]
    created = created_response.json()
    case_run_id = created["case_runs"][0]["id"]

    other_created = client.post(
        "/api/v1/runs", headers=headers, json=_payload(ids)
    )
    assert other_created.status_code == 201, other_created.text
    other_case_run_id = other_created.json()["case_runs"][0]["id"]

    with session_factory() as session:
        capture = session.scalar(
            select(RunRequirementCapture).where(
                RunRequirementCapture.case_run_id == case_run_id
            )
        )
        assert capture is not None
        assert capture.capture_status == "CAPTURED"
        assert capture.item_count == 2
        sources = list(
            session.scalars(
                select(RunRequirementSource)
                .where(RunRequirementSource.capture_id == capture.id)
                .order_by(RunRequirementSource.sequence_no)
            )
        )
        assert {item.requirement_code for item in sources} == {
            "REQ-EXACT",
            "REQ-LEGACY",
        }
        exact_snapshot = next(
            item for item in sources if item.requirement_code == "REQ-EXACT"
        )
        legacy_snapshot = next(
            item for item in sources if item.requirement_code == "REQ-LEGACY"
        )
        assert exact_snapshot.asset_version_binding == "EXACT_EXECUTION_VERSION"
        assert exact_snapshot.link_asset_type == "TEST_CASE"
        assert exact_snapshot.requirement_version_binding == "EXACT_REQUIREMENT_VERSION"
        assert legacy_snapshot.asset_version_binding == "ASSET_VERSION_UNKNOWN"
        assert legacy_snapshot.requirement_version_binding == "REQUIREMENT_VERSION_UNKNOWN"

        # Current-domain changes and even source-link deletion cannot rewrite the
        # immutable run snapshot because source rows have no live-domain FKs.
        current_exact = session.get(Requirement, exact_id)
        current_exact_version = session.get(RequirementVersion, exact_version_id)
        assert current_exact is not None and current_exact_version is not None
        current_exact.title = "Renamed after run"
        current_exact.status = "ARCHIVED"
        current_exact_version.content_hash = "f" * 64
        case = session.get(ApiCase, ids["case_id"])
        assert case is not None
        case.current_version_id = other_version_id
        current_exact_link = session.get(RequirementCaseLink, exact_link_id)
        current_legacy_link = session.get(RequirementCaseLink, legacy_link_id)
        assert current_exact_link is not None and current_legacy_link is not None
        session.delete(current_exact_link)
        session.delete(current_legacy_link)
        replacement, replacement_version = _requirement(
            session,
            ids["project_id"],
            "REQ-RELINKED",
            "Linked after run",
            with_version=True,
        )
        session.add(
            _link(
                requirement=replacement,
                requirement_version=replacement_version,
                case_id=case.id,
                case_version_id=ids["case_version_id"],
                relation_type="COVERAGE",
            )
        )
        session.commit()

    dispatched = client.post(
        f"/api/v1/runs/{created['id']}/dispatch", headers=headers
    )
    assert dispatched.status_code == 200, dispatched.text
    claimed = client.post(
        f"/api/v1/runs/{created['id']}/claim",
        headers={"Authorization": "Bearer rc_run_test_credential"},
        json={"message_id": dispatched.json()["message_id"]},
    )
    assert claimed.status_code == 200, claimed.text

    detail = client.get(f"/api/v1/reports/{created['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    requirement_page = detail.json()["requirement_sources"]
    assert requirement_page["total"] == 2
    assert requirement_page["captures"][0]["status"] == "CAPTURED"
    assert requirement_page["captures"][0]["captured_at"].endswith(
        ("+00:00", "Z")
    )
    by_code = {item["requirement_code"]: item for item in requirement_page["items"]}
    assert set(by_code) == {"REQ-EXACT", "REQ-LEGACY"}
    assert by_code["REQ-EXACT"]["requirement_title"] == "Exact title"
    assert by_code["REQ-EXACT"]["requirement_content_hash"] != "f" * 64
    assert by_code["REQ-LEGACY"]["link_asset_version_id"] is None
    assert "不能声称精确绑定" in by_code["REQ-LEGACY"]["binding_note"]
    assert "版本号与内容哈希未知" in by_code["REQ-LEGACY"]["binding_note"]
    assert "must-not-escape" not in detail.text
    assert "REQ-RELINKED" not in detail.text

    foreign_case = client.get(
        f"/api/v1/reports/{created['id']}/requirement-sources",
        headers=headers,
        params={"case_run_id": other_case_run_id},
    )
    assert foreign_case.status_code == 200
    assert foreign_case.json()["captures"] == []
    assert foreign_case.json()["items"] == []
    assert foreign_case.json()["total"] == 0

    first_page = client.get(
        f"/api/v1/reports/{created['id']}/requirement-sources",
        headers=headers,
        params={"case_run_id": case_run_id, "page": 1, "page_size": 1},
    )
    assert first_page.status_code == 200, first_page.text
    assert first_page.json()["has_more"] is True
    assert first_page.json()["next_page"] == 2
    assert "case_run_id=" in first_page.json()["continuation_path"]
    second_page = client.get(
        first_page.json()["continuation_path"], headers=headers
    )
    assert second_page.status_code == 200
    assert {
        first_page.json()["items"][0]["requirement_code"],
        second_page.json()["items"][0]["requirement_code"],
    } == {"REQ-EXACT", "REQ-LEGACY"}

    # The shared in-memory SQLite connection cannot safely open an independent
    # export snapshot after token authentication has started a transaction.
    # Bypass authentication only for the rendering assertions so the endpoint
    # can exercise its dedicated snapshot on this test-only database.
    app.dependency_overrides[get_current_user] = lambda: _identity("dev-admin")
    markdown = client.get(
        f"/api/v1/reports/{created['id']}/export",
        headers=headers,
        params={"format": "markdown"},
    )
    html = client.get(
        f"/api/v1/reports/{created['id']}/export",
        headers=headers,
        params={"format": "html"},
    )
    assert markdown.status_code == html.status_code == 200
    markdown_body = markdown.content.decode("utf-8", errors="strict")
    html_body = html.content.decode("utf-8", errors="strict")
    for value in ("REQ-EXACT", "REQ-LEGACY", "ASSET_VERSION_UNKNOWN"):
        assert value in html_body
    for value in ("REQ\\-EXACT", "REQ\\-LEGACY", "ASSET\\_VERSION\\_UNKNOWN"):
        assert value in markdown_body
    assert "must-not-escape" not in markdown.text
    assert "must-not-escape" not in html.text

    with session_factory() as session:
        project = session.get(Project, ids["project_id"])
        assert project is not None
        project.status = "ARCHIVED"
        project.archived_at = datetime(2026, 9, 10, 9)
        session.commit()
    archived = client.get(f"/api/v1/reports/{created['id']}", headers=headers)
    assert archived.status_code == 200
    assert archived.json()["requirement_sources"]["total"] == 2


def test_create_run_distinguishes_empty_scenario_and_legacy_not_recorded(
    snapshot_context: tuple[
        TestClient,
        sessionmaker[Session],
        FakeRunHeartbeatStore,
        dict[str, Any],
    ],
) -> None:
    client, _, _, ids = snapshot_context
    headers = _headers(client)
    empty = client.post("/api/v1/runs", headers=headers, json=_payload(ids))
    assert empty.status_code == 201, empty.text
    empty_detail = client.get(
        f"/api/v1/reports/{empty.json()['id']}", headers=headers
    ).json()
    empty_capture = empty_detail["requirement_sources"]["captures"][0]
    assert empty_capture["status"] == "CAPTURED_EMPTY"
    assert empty_capture["captured_at"] is not None

    scenario_payload = {
        "project_id": ids["project_id"],
        "environment_id": ids["environment_id"],
        "runner_id": ids["runner_id"],
        "run_type": "SCENARIO",
        "scenario_id": ids["scenario_id"],
        "required_tags": ["Windows"],
        "required_capabilities": ["API"],
        "required_slot_type": "API",
        "required_slot_count": 1,
    }
    scenario = client.post(
        "/api/v1/runs", headers=headers, json=scenario_payload
    )
    assert scenario.status_code == 201, scenario.text
    scenario_detail = client.get(
        f"/api/v1/reports/{scenario.json()['id']}", headers=headers
    ).json()
    scenario_capture = scenario_detail["requirement_sources"]["captures"][0]
    assert scenario_capture["status"] == "UNSUPPORTED_TARGET"
    assert scenario_capture["captured_at"] is not None
    assert "不表示已证明不存在" in scenario_capture["note"]


def test_web_run_captures_only_its_locked_web_version(
    snapshot_context: tuple[
        TestClient,
        sessionmaker[Session],
        FakeRunHeartbeatStore,
        dict[str, Any],
    ],
) -> None:
    client, session_factory, _, ids = snapshot_context
    headers = _headers(client)
    _enable_web_runner(session_factory, ids)
    with session_factory() as session:
        web_case = WebCase(
            id=ids["case_id"],
            project_id=ids["project_id"],
            code="WEB-SNAPSHOT",
            name="Web snapshot",
            status="APPROVED",
            created_by="dev-admin",
        )
        session.add(web_case)
        session.flush()
        locked_version = WebCaseVersion(
            web_case_id=web_case.id,
            version_no=1,
            content={
                "start_url": "https://example.test",
                "actions": [{"type": "GOTO", "url": "https://example.test"}],
                "assertions": [],
            },
            status="APPROVED",
            approved_by="dev-admin",
            approved_at=datetime(2026, 9, 10, 8),
            created_by="dev-admin",
        )
        other_version = WebCaseVersion(
            web_case_id=web_case.id,
            version_no=2,
            content={
                "start_url": "https://other.example.test",
                "actions": [],
                "assertions": [],
            },
            status="APPROVED",
            approved_by="dev-admin",
            approved_at=datetime(2026, 9, 10, 8),
            created_by="dev-admin",
        )
        session.add_all([locked_version, other_version])
        session.flush()
        web_case.current_version_id = locked_version.id
        exact, exact_version = _requirement(
            session, ids["project_id"], "WEB-EXACT", "Web exact", with_version=True
        )
        legacy, _ = _requirement(
            session, ids["project_id"], "WEB-LEGACY", "Web legacy", with_version=False
        )
        wrong, wrong_version = _requirement(
            session, ids["project_id"], "WEB-WRONG", "Web wrong", with_version=True
        )
        api_only, api_only_version = _requirement(
            session, ids["project_id"], "API-SAME-ID", "API only", with_version=True
        )
        session.add_all(
            [
                _web_link(
                    requirement=exact,
                    requirement_version=exact_version,
                    web_case_id=web_case.id,
                    web_case_version_id=locked_version.id,
                    relation_type="COVERAGE",
                ),
                _web_link(
                    requirement=legacy,
                    requirement_version=None,
                    web_case_id=web_case.id,
                    web_case_version_id=None,
                    relation_type="TRACE",
                ),
                _web_link(
                    requirement=wrong,
                    requirement_version=wrong_version,
                    web_case_id=web_case.id,
                    web_case_version_id=other_version.id,
                    relation_type="COVERAGE",
                ),
                _link(
                    requirement=api_only,
                    requirement_version=api_only_version,
                    case_id=ids["case_id"],
                    case_version_id=ids["case_version_id"],
                    relation_type="COVERAGE",
                ),
            ]
        )
        session.commit()
        web_case_id = web_case.id
        locked_version_id = locked_version.id

    created = client.post(
        "/api/v1/runs",
        headers=headers,
        json={
            "project_id": ids["project_id"],
            "environment_id": ids["environment_id"],
            "runner_id": ids["runner_id"],
            "run_type": "WEB_CASE",
            "web_case_id": web_case_id,
            "web_case_version_id": locked_version_id,
            "required_slot_type": "WEB",
        },
    )
    assert created.status_code == 201, created.text
    detail = client.get(
        f"/api/v1/reports/{created.json()['id']}", headers=headers
    )
    assert detail.status_code == 200, detail.text
    sources = detail.json()["requirement_sources"]
    assert sources["captures"][0]["target_type"] == "WEB_CASE"
    assert {item["requirement_code"] for item in sources["items"]} == {
        "WEB-EXACT",
        "WEB-LEGACY",
    }



def test_legacy_run_is_explicitly_not_recorded(legacy_report_client: Any) -> None:
    legacy = _as(legacy_report_client, _identity("viewer"))
    old_detail = legacy.get("/api/v1/reports/run-api")
    assert old_detail.status_code == 200
    old_capture = old_detail.json()["requirement_sources"]["captures"][0]
    assert old_capture["status"] == "NOT_RECORDED"
    assert old_capture["captured_at"] is None
    assert "不会用当前关联回填" in old_capture["note"]
    old_export = legacy.get(
        "/api/v1/reports/run-api/export", params={"format": "markdown"}
    )
    assert old_export.status_code == 200
    old_export_body = old_export.content.decode("utf-8", errors="strict")
    assert "NOT\\_RECORDED" in old_export_body
    assert "Captured At | null" in old_export_body


def test_cross_project_source_rejects_run_without_event_or_partial_rows(
    snapshot_context: tuple[
        TestClient,
        sessionmaker[Session],
        FakeRunHeartbeatStore,
        dict[str, Any],
    ],
) -> None:
    client, session_factory, store, ids = snapshot_context
    headers = _headers(client)
    with session_factory() as session:
        foreign_project = Project(
            name="Foreign", code="FOREIGN", owner_id="other", status="ACTIVE"
        )
        session.add(foreign_project)
        session.flush()
        requirement, version = _requirement(
            session,
            foreign_project.id,
            "REQ-FOREIGN",
            "Foreign requirement",
            with_version=True,
        )
        session.add(
            _link(
                requirement=requirement,
                requirement_version=version,
                case_id=ids["case_id"],
                case_version_id=ids["case_version_id"],
                relation_type="COVERAGE",
            )
        )
        session.commit()

    rejected = client.post("/api/v1/runs", headers=headers, json=_payload(ids))
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "RUN_REQUIREMENT_SNAPSHOT_INVALID"
    with session_factory() as session:
        assert session.scalar(select(func.count(RunModel.id))) == 0
        assert session.scalar(select(func.count(CaseRun.id))) == 0
        assert session.scalar(select(func.count(RunRequirementCapture.id))) == 0
    assert store.event_stream.events == {}


def test_capture_exception_rolls_back_run_and_publishes_no_event(
    snapshot_context: tuple[
        TestClient,
        sessionmaker[Session],
        FakeRunHeartbeatStore,
        dict[str, Any],
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session_factory, store, ids = snapshot_context
    headers = _headers(client)

    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("capture failed")

    monkeypatch.setattr(
        runs_service, "capture_case_run_requirement_sources", explode
    )
    with pytest.raises(RuntimeError, match="capture failed"):
        client.post("/api/v1/runs", headers=headers, json=_payload(ids))
    with session_factory() as session:
        assert session.scalar(select(func.count(RunModel.id))) == 0
        assert session.scalar(select(func.count(CaseRun.id))) == 0
        assert session.scalar(select(func.count(RunRequirementCapture.id))) == 0
    assert store.event_stream.events == {}


def test_source_limit_rejects_instead_of_truncating(
    snapshot_context: tuple[
        TestClient,
        sessionmaker[Session],
        FakeRunHeartbeatStore,
        dict[str, Any],
    ],
) -> None:
    client, session_factory, store, ids = snapshot_context
    headers = _headers(client)
    with session_factory() as session:
        requirements = [
            Requirement(
                project_id=ids["project_id"],
                code=f"REQ-LIMIT-{index:04}",
                title=f"Limit {index}",
                type="FEATURE",
                status="ACTIVE",
                created_by="dev-admin",
            )
            for index in range(1001)
        ]
        session.add_all(requirements)
        session.flush()
        session.add_all(
            [
                _link(
                    requirement=requirement,
                    requirement_version=None,
                    case_id=ids["case_id"],
                    case_version_id=ids["case_version_id"],
                    relation_type="COVERAGE",
                )
                for requirement in requirements
            ]
        )
        session.commit()

    rejected = client.post("/api/v1/runs", headers=headers, json=_payload(ids))
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "RUN_REQUIREMENT_SOURCE_LIMIT_EXCEEDED"
    assert rejected.json()["details"]["observed_at_least"] == 1001
    with session_factory() as session:
        assert session.scalar(select(func.count(RunModel.id))) == 0
        assert session.scalar(select(func.count(RunRequirementSource.id))) == 0
    assert store.event_stream.events == {}


def test_capture_reads_all_link_fields_from_join_not_stale_identity_map(
    snapshot_context: tuple[
        TestClient,
        sessionmaker[Session],
        FakeRunHeartbeatStore,
        dict[str, Any],
    ],
) -> None:
    _, session_factory, _, ids = snapshot_context
    with session_factory() as session:
        requirement, version = _requirement(
            session,
            ids["project_id"],
            "REQ-CACHED-LINK",
            "Title before committed update",
            with_version=True,
        )
        assert version is not None
        link = _link(
            requirement=requirement,
            requirement_version=None,
            case_id=ids["case_id"],
            case_version_id=None,
            relation_type="BEFORE",
        )
        session.add(link)
        session.commit()
        requirement_id = requirement.id
        version_id = version.id
        link_id = link.id

    with session_factory() as session:
        cached_requirement = session.get(Requirement, requirement_id)
        cached_link = session.get(RequirementCaseLink, link_id)
        assert cached_requirement is not None and cached_link is not None
        assert cached_requirement.title == "Title before committed update"
        assert cached_link.requirement_version_id is None
        assert cached_link.case_version_id is None
        session.commit()

        changed_at = datetime(2026, 9, 10, 10, 11, 12)
        with session_factory() as writer:
            writer.execute(
                update(Requirement)
                .where(Requirement.id == requirement_id)
                .values(title="Title from joined database view")
            )
            writer.execute(
                update(RequirementCaseLink)
                .where(RequirementCaseLink.id == link_id)
                .values(
                    requirement_version_id=version_id,
                    case_version_id=ids["case_version_id"],
                    relation_type="AFTER",
                    source="AI",
                    confidence=0.625,
                    created_by="committed-writer",
                    created_at_time_basis="UTC",
                    created_at=changed_at,
                )
            )
            writer.commit()

        assert cached_requirement.title == "Title before committed update"
        assert cached_link.requirement_version_id is None
        assert cached_link.case_version_id is None
        capture = capture_case_run_requirement_sources(
            session,
            RunModel(
                id="run-cached-link",
                run_type="API_CASE",
                project_id=ids["project_id"],
                case_id=ids["case_id"],
                case_version_id=ids["case_version_id"],
            ),
            CaseRun(
                id=90_001,
                run_id="run-cached-link",
                sequence_no=1,
                case_id=ids["case_id"],
                case_version_id=ids["case_version_id"],
            ),
        )
        assert capture.item_count == 1
        source = capture.sources[0]
        assert source.requirement_title == "Title from joined database view"
        assert source.requirement_version_id == version_id
        assert source.requirement_version_binding == "EXACT_REQUIREMENT_VERSION"
        assert source.link_asset_version_id == ids["case_version_id"]
        assert source.asset_version_binding == "EXACT_EXECUTION_VERSION"
        assert source.relation_type == "AFTER"
        assert source.source == "AI"
        assert float(source.confidence) == 0.625
        assert source.link_created_by == "committed-writer"
        assert source.link_created_at == changed_at
        assert source.link_created_at_time_basis == "UTC"
        session.rollback()
