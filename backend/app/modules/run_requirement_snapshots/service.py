from dataclasses import dataclass
from typing import Any

from fastapi import status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError, ResourceConflictError
from app.core.time import utc_now_naive
from app.modules.requirements.models import Requirement, RequirementVersion
from app.modules.runs.enums import RunType
from app.modules.runs.models import CaseRun, TestRun
from app.modules.test_cases.models import RequirementCaseLink

from .models import RunRequirementCapture, RunRequirementSource

MAX_REQUIREMENT_SOURCES_PER_CASE_RUN = 1_000
_PROBE_LIMIT = MAX_REQUIREMENT_SOURCES_PER_CASE_RUN + 1


@dataclass(frozen=True)
class _Target:
    run_type: RunType
    asset_type: str | None
    asset_id: int
    version_id: int
    asset_id_column: Any | None
    version_id_column: Any | None


def _target(case_run: CaseRun) -> _Target:
    if case_run.case_id is not None and case_run.case_version_id is not None:
        return _Target(
            RunType.API_CASE,
            "TEST_CASE",
            case_run.case_id,
            case_run.case_version_id,
            RequirementCaseLink.case_id,
            RequirementCaseLink.case_version_id,
        )
    if case_run.web_case_id is not None and case_run.web_case_version_id is not None:
        return _Target(
            RunType.WEB_CASE,
            "WEB_CASE",
            case_run.web_case_id,
            case_run.web_case_version_id,
            RequirementCaseLink.web_case_id,
            RequirementCaseLink.web_case_version_id,
        )
    if case_run.scenario_id is not None and case_run.scenario_version_id is not None:
        return _Target(
            RunType.SCENARIO,
            None,
            case_run.scenario_id,
            case_run.scenario_version_id,
            None,
            None,
        )
    raise ResourceConflictError("CaseRun 运行目标不完整，无法记录需求来源快照")


def _capture_error(message: str, *, details: dict[str, Any] | None = None) -> AppError:
    return AppError(
        "RUN_REQUIREMENT_SNAPSHOT_INVALID",
        message,
        status_code=status.HTTP_409_CONFLICT,
        details=details,
    )


def capture_case_run_requirement_sources(
    session: Session,
    run: TestRun,
    case_run: CaseRun,
) -> RunRequirementCapture:
    """Capture eligible ACTIVE links in the create-run transaction.

    The eligible association, requirement metadata and optional requirement version
    are read by one joined statement. Under MySQL REPEATABLE READ this statement
    participates in the transaction snapshot; under READ COMMITTED it is still one
    statement-level consistent view, so no row-by-row hydration can mix revisions.
    """

    if case_run.id is None:
        raise RuntimeError("CaseRun must be flushed before capturing requirements")
    target = _target(case_run)
    run_target = {
        RunType.API_CASE: (run.case_id, run.case_version_id),
        RunType.WEB_CASE: (run.web_case_id, run.web_case_version_id),
        RunType.SCENARIO: (run.scenario_id, run.scenario_version_id),
    }[target.run_type]
    if (
        case_run.run_id != run.id
        or run.run_type != target.run_type.value
        or run_target != (target.asset_id, target.version_id)
    ):
        raise _capture_error("Run 与 CaseRun 的运行目标不一致，已拒绝创建 Run")
    captured_at = utc_now_naive()
    capture = RunRequirementCapture(
        case_run_id=case_run.id,
        capture_status=(
            "UNSUPPORTED_TARGET"
            if target.run_type == RunType.SCENARIO
            else "CAPTURED_EMPTY"
        ),
        captured_at=captured_at,
        captured_at_time_basis="UTC",
        target_type=target.run_type.value,
        target_asset_id=target.asset_id,
        target_version_id=target.version_id,
        item_count=0,
        consistency_basis="CREATE_RUN_TRANSACTION",
    )
    if target.run_type == RunType.SCENARIO:
        session.add(capture)
        return capture

    assert target.asset_type is not None
    assert target.asset_id_column is not None
    assert target.version_id_column is not None
    statement = (
        select(
            RequirementCaseLink.id,
            RequirementCaseLink.requirement_id,
            RequirementCaseLink.requirement_version_id,
            RequirementCaseLink.asset_type,
            RequirementCaseLink.case_id,
            RequirementCaseLink.case_version_id,
            RequirementCaseLink.web_case_id,
            RequirementCaseLink.web_case_version_id,
            RequirementCaseLink.relation_type,
            RequirementCaseLink.source,
            RequirementCaseLink.confidence,
            RequirementCaseLink.supersedes_link_id,
            RequirementCaseLink.created_by,
            RequirementCaseLink.created_at_time_basis,
            RequirementCaseLink.created_at,
            Requirement.id,
            Requirement.project_id,
            Requirement.code,
            Requirement.title,
            Requirement.type,
            Requirement.status,
            RequirementVersion.id,
            RequirementVersion.requirement_id,
            RequirementVersion.version_no,
            RequirementVersion.content_hash,
            RequirementVersion.source_type,
        )
        .outerjoin(Requirement, Requirement.id == RequirementCaseLink.requirement_id)
        .outerjoin(
            RequirementVersion,
            RequirementVersion.id == RequirementCaseLink.requirement_version_id,
        )
        .where(
            RequirementCaseLink.status == "ACTIVE",
            RequirementCaseLink.asset_type == target.asset_type,
            target.asset_id_column == target.asset_id,
            or_(
                target.version_id_column == target.version_id,
                target.version_id_column.is_(None),
            ),
        )
        .order_by(RequirementCaseLink.id.asc())
        .limit(_PROBE_LIMIT)
    )
    rows = list(session.execute(statement).all())
    if len(rows) > MAX_REQUIREMENT_SOURCES_PER_CASE_RUN:
        raise AppError(
            "RUN_REQUIREMENT_SOURCE_LIMIT_EXCEEDED",
            "单个 CaseRun 的需求来源超过 1000 条，已拒绝创建 Run",
            status_code=status.HTTP_409_CONFLICT,
            details={
                "case_run_id": case_run.id,
                "limit": MAX_REQUIREMENT_SOURCES_PER_CASE_RUN,
                "observed_at_least": _PROBE_LIMIT,
            },
        )

    sources: list[RunRequirementSource] = []
    for sequence_no, row in enumerate(rows, start=1):
        (
            link_id,
            link_requirement_id,
            link_requirement_version_id,
            link_asset_type,
            link_case_id,
            link_case_version_id,
            link_web_case_id,
            link_web_case_version_id,
            link_relation_type,
            link_source,
            link_confidence,
            link_supersedes_id,
            link_created_by,
            link_created_at_time_basis,
            link_created_at,
            requirement_id,
            requirement_project_id,
            requirement_code,
            requirement_title,
            requirement_type,
            requirement_status,
            version_id,
            version_requirement_id,
            version_no,
            version_content_hash,
            version_source_type,
        ) = row
        if requirement_id is None:
            raise _capture_error("需求关联引用的需求不存在，已拒绝创建 Run")
        if link_requirement_id != requirement_id:
            raise _capture_error("需求关联引用不一致，已拒绝创建 Run")
        if requirement_project_id != run.project_id:
            raise _capture_error("需求关联跨项目，已拒绝创建 Run")
        if link_requirement_version_id is not None:
            if (
                version_id is None
                or version_id != link_requirement_version_id
                or version_requirement_id != requirement_id
            ):
                raise _capture_error("需求关联的版本归属不一致，已拒绝创建 Run")
            requirement_binding = "EXACT_REQUIREMENT_VERSION"
            requirement_version_id = version_id
            requirement_version_no = version_no
            requirement_content_hash = version_content_hash
            requirement_source_type = version_source_type
        else:
            requirement_binding = "REQUIREMENT_VERSION_UNKNOWN"
            requirement_version_id = None
            requirement_version_no = None
            requirement_content_hash = None
            requirement_source_type = None

        if link_asset_type != target.asset_type:
            raise _capture_error("需求关联资产类型不一致，已拒绝创建 Run")
        link_asset_id = (
            link_case_id
            if target.run_type == RunType.API_CASE
            else link_web_case_id
        )
        if link_asset_id != target.asset_id:
            raise _capture_error("需求关联资产归属不一致，已拒绝创建 Run")
        link_asset_version_id = (
            link_case_version_id
            if target.run_type == RunType.API_CASE
            else link_web_case_version_id
        )
        asset_binding = (
            "ASSET_VERSION_UNKNOWN"
            if link_asset_version_id is None
            else "EXACT_EXECUTION_VERSION"
        )
        sources.append(
            RunRequirementSource(
                sequence_no=sequence_no,
                original_link_id=link_id,
                supersedes_link_id=link_supersedes_id,
                requirement_id=link_requirement_id,
                requirement_code=requirement_code,
                requirement_title=requirement_title,
                requirement_type=requirement_type,
                requirement_status=requirement_status,
                requirement_version_id=requirement_version_id,
                requirement_version_no=requirement_version_no,
                requirement_content_hash=requirement_content_hash,
                requirement_source_type=requirement_source_type,
                requirement_version_binding=requirement_binding,
                target_type=target.run_type.value,
                link_asset_type=link_asset_type,
                target_asset_id=target.asset_id,
                target_version_id=target.version_id,
                link_asset_version_id=link_asset_version_id,
                asset_version_binding=asset_binding,
                relation_type=link_relation_type,
                source=link_source,
                confidence=link_confidence,
                link_created_by=link_created_by,
                link_created_at=link_created_at,
                link_created_at_time_basis=link_created_at_time_basis,
                captured_at=captured_at,
                captured_at_time_basis="UTC",
            )
        )

    capture.item_count = len(sources)
    capture.capture_status = "CAPTURED" if sources else "CAPTURED_EMPTY"
    capture.sources.extend(sources)
    session.add(capture)
    return capture
