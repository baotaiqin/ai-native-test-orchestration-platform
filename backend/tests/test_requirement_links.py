import hashlib
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user, get_db_session
from app.main import app
from app.modules.api_definitions.models import ApiDefinition
from app.modules.auth.schemas import CurrentUser
from app.modules.projects.models import Project, ProjectMember
from app.modules.requirements.models import Requirement, RequirementVersion
from app.modules.test_cases.models import RequirementCaseLink
from app.modules.test_cases.models import TestCase as CaseModel
from app.modules.test_cases.models import TestCaseVersion as CaseVersionModel
from app.modules.web_cases.models import WebCase, WebCaseVersion


@dataclass
class LinkClient:
    client: TestClient
    session_factory: sessionmaker[Session]


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _identity(user_id: str, roles: list[str] | None = None) -> CurrentUser:
    return CurrentUser(
        id=user_id,
        username=user_id,
        display_name=user_id,
        roles=roles or [],
    )


def _as(link_client: LinkClient, identity: CurrentUser) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: identity
    return link_client.client


@pytest.fixture
def requirement_link_client() -> Generator[LinkClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tables = (
        Project.__table__,
        ProjectMember.__table__,
        Requirement.__table__,
        RequirementVersion.__table__,
        ApiDefinition.__table__,
        CaseModel.__table__,
        CaseVersionModel.__table__,
        WebCase.__table__,
        WebCaseVersion.__table__,
        RequirementCaseLink.__table__,
    )
    for table in tables:
        table.create(engine)
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")

    with factory() as session:
        session.add_all(
            [
                Project(id=1, name="关联项目", code="LINKS", status="ACTIVE", owner_id="owner"),
                Project(id=2, name="其他项目", code="OTHER", status="ACTIVE", owner_id="other"),
                ProjectMember(project_id=1, user_id="owner", role="PROJECT_OWNER"),
                ProjectMember(project_id=1, user_id="viewer", role="VIEWER"),
                ProjectMember(project_id=2, user_id="other", role="PROJECT_OWNER"),
            ]
        )
        session.flush()
        requirement = Requirement(
            id=1,
            project_id=1,
            code="REQ-0001",
            title="登录需求",
            type="FEATURE",
            order_index=0,
            status="ACTIVE",
            current_version_id=103,
            created_by="owner",
        )
        other_requirement = Requirement(
            id=2,
            project_id=2,
            code="REQ-0002",
            title="其他需求",
            type="FEATURE",
            order_index=0,
            status="ACTIVE",
            current_version_id=201,
            created_by="other",
        )
        v1_content = "alpha\n-- old literal line\nstable"
        v2_content = "beta\n++ new literal line\nstable"
        session.add_all(
            [
                requirement,
                other_requirement,
                RequirementVersion(
                    id=101,
                    requirement_id=1,
                    version_no=1,
                    markdown_content=v1_content,
                    content_hash=_hash(v1_content),
                    source_type="MANUAL",
                    created_by="owner",
                ),
                RequirementVersion(
                    id=102,
                    requirement_id=1,
                    version_no=2,
                    markdown_content=v2_content,
                    content_hash=_hash(v2_content),
                    source_type="MANUAL",
                    created_by="owner",
                ),
                RequirementVersion(
                    id=103,
                    requirement_id=1,
                    version_no=3,
                    markdown_content=v2_content,
                    content_hash=_hash(v2_content),
                    source_type="MANUAL",
                    created_by="owner",
                ),
                RequirementVersion(
                    id=201,
                    requirement_id=2,
                    version_no=1,
                    markdown_content="other",
                    content_hash=_hash("other"),
                    source_type="MANUAL",
                    created_by="other",
                ),
            ]
        )
        session.add_all(
            [
                CaseModel(
                    id=1,
                    project_id=1,
                    code="TC-00001",
                    name="API 登录",
                    case_type="API",
                    status="ACTIVE",
                    source="MANUAL",
                    current_version_id=1001,
                    created_by="owner",
                ),
                CaseModel(
                    id=2,
                    project_id=1,
                    code="TC-00002",
                    name="旧 WEB TestCase",
                    case_type="WEB",
                    status="ACTIVE",
                    source="MANUAL",
                    current_version_id=1002,
                    created_by="owner",
                ),
                CaseModel(
                    id=3,
                    project_id=2,
                    code="TC-00003",
                    name="跨项目用例",
                    case_type="API",
                    status="ACTIVE",
                    source="MANUAL",
                    current_version_id=1003,
                    created_by="other",
                ),
                CaseModel(
                    id=4,
                    project_id=1,
                    code="TC-00004",
                    name="归档用例",
                    case_type="API",
                    status="ARCHIVED",
                    source="MANUAL",
                    current_version_id=1004,
                    created_by="owner",
                ),
                CaseVersionModel(
                    id=1001,
                    case_id=1,
                    version_no=1,
                    content={},
                    created_by="owner",
                ),
                CaseVersionModel(
                    id=1002,
                    case_id=2,
                    version_no=1,
                    content={},
                    created_by="owner",
                ),
                CaseVersionModel(
                    id=1003,
                    case_id=3,
                    version_no=1,
                    content={},
                    created_by="other",
                ),
                CaseVersionModel(
                    id=1004,
                    case_id=4,
                    version_no=1,
                    content={},
                    created_by="owner",
                ),
            ]
        )
        web_case = WebCase(
            id=1,
            project_id=1,
            code="WC-00001",
            name="独立 WebCase",
            status="DRAFT",
            current_version_id=None,
            created_by="owner",
        )
        archived_web_case = WebCase(
            id=2,
            project_id=1,
            code="WC-00002",
            name="归档 WebCase",
            status="ARCHIVED",
            current_version_id=None,
            created_by="owner",
        )
        session.add_all([web_case, archived_web_case])
        session.flush()
        session.add_all(
            [
                WebCaseVersion(
                    id=2001,
                    web_case_id=1,
                    version_no=1,
                    content={},
                    status="DRAFT",
                    created_by="owner",
                ),
                WebCaseVersion(
                    id=2002,
                    web_case_id=2,
                    version_no=1,
                    content={},
                    status="RETIRED",
                    created_by="owner",
                ),
            ]
        )
        session.flush()
        web_case.current_version_id = 2001
        archived_web_case.current_version_id = 2002
        session.flush()
        session.add_all(
            [
                RequirementCaseLink(
                    id=1,
                    requirement_id=1,
                    requirement_version_id=None,
                    asset_type="TEST_CASE",
                    case_type="API",
                    case_id=1,
                    case_version_id=None,
                    relation_type="TRACEABILITY",
                    source="MANUAL",
                    confidence=1,
                    status="ACTIVE",
                    active_slot=1,
                ),
                RequirementCaseLink(
                    id=2,
                    requirement_id=1,
                    requirement_version_id=101,
                    asset_type="TEST_CASE",
                    case_type="WEB",
                    case_id=2,
                    case_version_id=None,
                    relation_type="COVERAGE",
                    source="MANUAL",
                    confidence=1,
                    status="ACTIVE",
                    active_slot=1,
                ),
            ]
        )
        session.commit()

    def override_session() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    try:
        with TestClient(app) as client:
            yield LinkClient(client=client, session_factory=factory)
    finally:
        app.dependency_overrides.clear()
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        for table in reversed(tables):
            table.drop(engine)
        engine.dispose()


def _link_payload(
    asset_type: str,
    asset_id: int,
    requirement_version_id: int,
    asset_version_id: int,
    relation_type: str = "COVERAGE",
) -> dict[str, Any]:
    return {
        "asset_type": asset_type,
        "asset_id": asset_id,
        "requirement_version_id": requirement_version_id,
        "asset_version_id": asset_version_id,
        "relation_type": relation_type,
        "confidence": 1,
    }


def test_explicit_asset_types_versions_reverse_queries_and_legacy_api(
    requirement_link_client: LinkClient,
) -> None:
    client = _as(requirement_link_client, _identity("owner"))
    test_link = client.post(
        "/api/v1/requirements/1/links",
        json=_link_payload("TEST_CASE", 1, 101, 1001),
    )
    web_link = client.post(
        "/api/v1/requirements/1/links",
        json=_link_payload("WEB_CASE", 1, 102, 2001),
    )
    assert test_link.status_code == 201, test_link.text
    assert web_link.status_code == 201, web_link.text
    assert test_link.json()["asset"]["code"] == "TC-00001"
    assert web_link.json()["asset"]["code"] == "WC-00001"
    assert test_link.json()["asset_version"]["id"] == 1001
    assert web_link.json()["asset_version"]["status"] == "DRAFT"
    assert test_link.json()["historical_scope"] == "CURRENT_ASSOCIATION_NOT_RUN_SNAPSHOT"
    assert test_link.json()["created_at_time_basis"] == "UTC"
    created_at = datetime.fromisoformat(test_link.json()["created_at"].replace("Z", "+00:00"))
    assert created_at.utcoffset() == timedelta(0)

    links = client.get("/api/v1/requirements/1/links", params={"page_size": 100}).json()
    assert links["total"] == 4
    assert {(item["asset_type"], item["asset_id"]) for item in links["items"]} == {
        ("TEST_CASE", 1),
        ("TEST_CASE", 2),
        ("WEB_CASE", 1),
    }
    old_web = next(item for item in links["items"] if item["asset"]["code"] == "TC-00002")
    assert old_web["asset_type"] == "TEST_CASE" and old_web["asset"]["case_type"] == "WEB"
    assert old_web["asset_version_known"] is False
    legacy = next(item for item in links["items"] if item["id"] == 1)
    assert legacy["created_at_time_basis"] == "LEGACY_UNKNOWN"
    assert datetime.fromisoformat(legacy["created_at"]).tzinfo is None

    test_reverse = client.get("/api/v1/test-cases/1/requirements").json()
    web_reverse = client.get("/api/v1/web-cases/1/requirements").json()
    assert all(item["asset_type"] == "TEST_CASE" for item in test_reverse["items"])
    assert all(item["asset_type"] == "WEB_CASE" for item in web_reverse["items"])
    assert {item["asset"]["code"] for item in test_reverse["items"]} == {"TC-00001"}
    assert {item["asset"]["code"] for item in web_reverse["items"]} == {"WC-00001"}

    legacy = client.get("/api/v1/test-cases/requirements/1/links")
    assert legacy.status_code == 200
    assert all(item["asset_type"] == "TEST_CASE" for item in legacy.json())
    assert all(item["status"] == "ACTIVE" for item in legacy.json())
    assert not any(item["case_id"] == 1 and item["case_type"] is None for item in legacy.json())


def test_precise_bindings_do_not_follow_current_versions_and_history_is_immutable(
    requirement_link_client: LinkClient,
) -> None:
    client = _as(requirement_link_client, _identity("owner"))
    created = client.post(
        "/api/v1/requirements/1/links",
        json=_link_payload("TEST_CASE", 1, 101, 1001),
    )
    assert created.status_code == 201
    first = created.json()

    with requirement_link_client.session_factory() as session:
        session.add(
            CaseVersionModel(
                id=1005,
                case_id=1,
                version_no=2,
                content={"changed": True},
                created_by="owner",
            )
        )
        session.get(CaseModel, 1).current_version_id = 1005
        session.get(Requirement, 1).current_version_id = 103
        session.commit()

    stable = client.get("/api/v1/requirements/1/links", params={"page_size": 100}).json()
    stable_link = next(item for item in stable["items"] if item["id"] == first["id"])
    assert stable_link["requirement_version"]["id"] == 101
    assert stable_link["asset_version"]["id"] == 1001
    assert stable_link["asset"]["current_version_id"] == 1005

    removed = client.delete(f"/api/v1/requirements/1/links/{first['id']}")
    assert removed.status_code == 200
    assert removed.json()["status"] == "REMOVED"
    assert removed.json()["removed_at_time_basis"] == "UTC"
    removed_at = datetime.fromisoformat(removed.json()["removed_at"].replace("Z", "+00:00"))
    assert removed_at.utcoffset() == timedelta(0)
    replacement = client.post(
        "/api/v1/requirements/1/links",
        json=_link_payload("TEST_CASE", 1, 102, 1005),
    )
    assert replacement.status_code == 201, replacement.text
    assert replacement.json()["supersedes_link_id"] == first["id"]
    assert replacement.json()["requirement_version_id"] == 102

    history = client.get(
        "/api/v1/requirements/1/links",
        params={"include_removed": True, "page_size": 100},
    ).json()["items"]
    old = next(item for item in history if item["id"] == first["id"])
    new = next(item for item in history if item["id"] == replacement.json()["id"])
    assert old["status"] == "REMOVED" and old["requirement_version_id"] == 101
    assert old["asset_version_id"] == 1001 and old["removed_by"] == "owner"
    assert new["status"] == "ACTIVE" and new["requirement_version_id"] == 102
    duplicate = client.post(
        "/api/v1/requirements/1/links",
        json=_link_payload("TEST_CASE", 1, 102, 1005),
    )
    assert duplicate.status_code == 409


def test_write_permissions_project_version_scope_and_archive_gates(
    requirement_link_client: LinkClient,
) -> None:
    viewer = _as(requirement_link_client, _identity("viewer"))
    readable = viewer.get("/api/v1/requirements/1/links")
    assert readable.status_code == 200
    denied = viewer.post(
        "/api/v1/requirements/1/links",
        json=_link_payload("TEST_CASE", 1, 101, 1001),
    )
    assert denied.status_code == 403

    owner = _as(requirement_link_client, _identity("owner"))
    for payload in (
        _link_payload("TEST_CASE", 3, 101, 1003),
        _link_payload("TEST_CASE", 1, 201, 1001),
        _link_payload("TEST_CASE", 1, 101, 1002),
        _link_payload("TEST_CASE", 4, 101, 1004),
        _link_payload("WEB_CASE", 2, 101, 2002),
    ):
        rejected = owner.post("/api/v1/requirements/1/links", json=payload)
        assert rejected.status_code == 409, rejected.text

    with requirement_link_client.session_factory() as session:
        session.get(Requirement, 1).status = "ARCHIVED"
        session.commit()
    archived_requirement = owner.post(
        "/api/v1/requirements/1/links",
        json=_link_payload("TEST_CASE", 1, 101, 1001),
    )
    assert archived_requirement.status_code == 409
    with requirement_link_client.session_factory() as session:
        session.get(Requirement, 1).status = "ACTIVE"
        session.get(Project, 1).status = "ARCHIVED"
        session.commit()
    archived_project = owner.post(
        "/api/v1/requirements/1/links",
        json=_link_payload("TEST_CASE", 1, 101, 1001),
    )
    assert archived_project.status_code == 409
    historical_read = _as(requirement_link_client, _identity("viewer")).get(
        "/api/v1/requirements/1/links"
    )
    assert historical_read.status_code == 200

    outsider = _as(requirement_link_client, _identity("outsider"))
    hidden = outsider.get("/api/v1/requirements/1/links")
    assert hidden.status_code == 404


def test_impact_separates_selected_diff_from_recorded_baselines_and_diff_is_exact(
    requirement_link_client: LinkClient,
) -> None:
    client = _as(requirement_link_client, _identity("owner"))
    exact_v1 = client.post(
        "/api/v1/requirements/1/links",
        json=_link_payload("TEST_CASE", 1, 101, 1001),
    )
    exact_v2 = client.post(
        "/api/v1/requirements/1/links",
        json=_link_payload("WEB_CASE", 1, 102, 2001),
    )
    assert exact_v1.status_code == exact_v2.status_code == 201

    impact = client.get(
        "/api/v1/requirements/1/impact",
        params={"from_version_id": 101, "to_version_id": 102, "page_size": 100},
    )
    assert impact.status_code == 200, impact.text
    body = impact.json()
    assert body["comparison"] == {
        "requirement_id": 1,
        "from_version": {
            "id": 101,
            "version_no": 1,
            "content_hash": _hash("alpha\n-- old literal line\nstable"),
        },
        "to_version": {
            "id": 102,
            "version_no": 2,
            "content_hash": _hash("beta\n++ new literal line\nstable"),
        },
        "content_changed": True,
        "additions": 2,
        "deletions": 2,
    }
    assert all(item["selected_scope_status"] == "POSSIBLY_OUTDATED" for item in body["items"])
    by_id = {item["id"]: item for item in body["items"]}
    assert by_id[1]["recorded_baseline_status"] == "BASELINE_UNKNOWN"
    assert by_id[exact_v1.json()["id"]]["recorded_baseline_status"] == "POSSIBLY_OUTDATED"
    assert by_id[exact_v2.json()["id"]]["recorded_baseline_status"] == "NO_CHANGE"

    same_content = client.get(
        "/api/v1/requirements/1/impact",
        params={"from_version_id": 102, "to_version_id": 103, "page_size": 100},
    ).json()
    assert same_content["comparison"]["content_changed"] is False
    assert same_content["comparison"]["additions"] == 0
    assert all(item["selected_scope_status"] == "NO_CHANGE" for item in same_content["items"])
    same_content_by_id = {item["id"]: item for item in same_content["items"]}
    assert same_content_by_id[exact_v1.json()["id"]]["recorded_baseline_status"] == (
        "POSSIBLY_OUTDATED"
    )

    diff = client.get(
        "/api/v1/requirements/1/diff",
        params={"from_version": 1, "to_version": 2},
    ).json()
    reverse = client.get(
        "/api/v1/requirements/1/diff",
        params={"from_version": 2, "to_version": 1},
    ).json()
    unchanged = client.get(
        "/api/v1/requirements/1/diff",
        params={"from_version": 2, "to_version": 2},
    ).json()
    assert (diff["additions"], diff["deletions"]) == (2, 2)
    assert (reverse["additions"], reverse["deletions"]) == (2, 2)
    assert unchanged["content_changed"] is False and unchanged["unified_diff"] == ""
    wrong_requirement = client.get(
        "/api/v1/requirements/1/impact",
        params={"from_version_id": 101, "to_version_id": 201},
    )
    assert wrong_requirement.status_code == 404


def test_impact_pagination_is_complete_stable_bounded_and_not_n_plus_one(
    requirement_link_client: LinkClient,
) -> None:
    with requirement_link_client.session_factory() as session:
        for sequence in range(10, 135):
            session.add(
                CaseModel(
                    id=sequence,
                    project_id=1,
                    code=f"TC-{sequence:05d}",
                    name=f"Case {sequence}",
                    case_type="API",
                    status="ACTIVE",
                    source="MANUAL",
                    current_version_id=10_000 + sequence,
                    created_by="owner",
                )
            )
            session.add(
                CaseVersionModel(
                    id=10_000 + sequence,
                    case_id=sequence,
                    version_no=1,
                    content={},
                    created_by="owner",
                )
            )
        session.flush()
        for sequence in range(10, 135):
            session.add(
                RequirementCaseLink(
                    requirement_id=1,
                    requirement_version_id=101 if sequence % 2 else 102,
                    asset_type="TEST_CASE",
                    case_type="API",
                    case_id=sequence,
                    case_version_id=10_000 + sequence,
                    relation_type="COVERAGE",
                    source="MANUAL",
                    confidence=1,
                    status="ACTIVE",
                    active_slot=1,
                    created_by="owner",
                )
            )
        session.commit()

    client = _as(requirement_link_client, _identity("viewer"))
    engine = requirement_link_client.session_factory.kw["bind"]
    statements: list[str] = []

    def record_sql(*args: Any) -> None:
        statements.append(str(args[2]))

    event.listen(engine, "before_cursor_execute", record_sql)
    try:
        first = client.get(
            "/api/v1/requirements/1/impact",
            params={"from_version_id": 101, "to_version_id": 102, "page_size": 100},
        )
    finally:
        event.remove(engine, "before_cursor_execute", record_sql)
    assert first.status_code == 200
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert len(statements) <= 12
    first_body = first.json()
    second_body = client.get(
        "/api/v1/requirements/1/impact",
        params={
            "from_version_id": 101,
            "to_version_id": 102,
            "page": 2,
            "page_size": 100,
        },
    ).json()
    ids = [item["id"] for item in first_body["items"] + second_body["items"]]
    assert first_body["total"] == 127
    assert len(ids) == 127 and ids == sorted(ids) and len(ids) == len(set(ids))
    assert first_body["has_more"] is True and second_body["has_more"] is False

    too_large = client.get(
        "/api/v1/requirements/1/impact",
        params={"from_version_id": 101, "to_version_id": 102, "page_size": 101},
    )
    huge_page = client.get(
        "/api/v1/requirements/1/links",
        params={"page": 100_001},
    )
    huge_id = client.get("/api/v1/requirements/2147483648/links")
    assert too_large.status_code == huge_page.status_code == huge_id.status_code == 422
