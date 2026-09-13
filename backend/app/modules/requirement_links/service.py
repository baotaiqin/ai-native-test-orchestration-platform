from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urlencode

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceConflictError, ResourceNotFoundError
from app.core.redaction import redact_text
from app.core.time import to_utc_aware, utc_now_naive
from app.modules.auth.schemas import CurrentUser
from app.modules.evidence.models import EvidenceArtifact
from app.modules.projects.schemas import ProjectStatus
from app.modules.projects.service import ensure_project_writable, get_project
from app.modules.requirements.models import Requirement, RequirementVersion
from app.modules.requirements.service import calculate_version_diff
from app.modules.run_requirement_snapshots.models import (
    RunRequirementCapture,
    RunRequirementSource,
)
from app.modules.runs.models import CaseRun, TestRun
from app.modules.test_cases.models import (
    RequirementCaseLink,
    TestCase,
    TestCaseVersion,
)
from app.modules.web_cases.models import WebCase, WebCaseVersion

from .schemas import (
    LinkedAssetReference,
    LinkedAssetVersionReference,
    RequirementAssetType,
    RequirementAuditTimeBasis,
    RequirementImpactComparison,
    RequirementImpactItem,
    RequirementImpactPage,
    RequirementImpactQuery,
    RequirementImpactStatus,
    RequirementLinkCreate,
    RequirementLinkPage,
    RequirementLinkQuery,
    RequirementLinkResponse,
    RequirementLinkStatus,
    RequirementTraceItem,
    RequirementTracePage,
    RequirementTraceQuery,
    RequirementVersionReference,
)

_SCOPE_NOTE = "确定性关联范围，尚未经人工确认实际受影响"
_MYSQL_LOCK_CONFLICT_CODES = frozenset({1205, 1213})
_LOCK_CONFLICT_MESSAGE = "需求关联写入遇到数据库锁冲突，请稍后重试"


def trace_requirement_runs(
    session: Session,
    user: CurrentUser,
    requirement_id: int,
    query: RequirementTraceQuery,
) -> RequirementTracePage:
    requirement = _get_requirement(session, user, requirement_id)
    evidence_count = (
        select(func.count(EvidenceArtifact.id))
        .where(EvidenceArtifact.case_run_id == CaseRun.id)
        .correlate(CaseRun)
        .scalar_subquery()
    )
    joins = (
        RunRequirementSource.capture_id == RunRequirementCapture.id,
        RunRequirementCapture.case_run_id == CaseRun.id,
        CaseRun.run_id == TestRun.id,
    )
    filters = (
        RunRequirementSource.requirement_id == requirement.id,
        TestRun.project_id == requirement.project_id,
    )
    total = int(
        session.scalar(
            select(func.count(RunRequirementSource.id))
            .join(RunRequirementCapture, joins[0])
            .join(CaseRun, joins[1])
            .join(TestRun, joins[2])
            .where(*filters)
        )
        or 0
    )
    rows = session.execute(
        select(
            RunRequirementSource,
            RunRequirementCapture,
            CaseRun,
            TestRun,
            evidence_count.label("evidence_count"),
        )
        .join(RunRequirementCapture, joins[0])
        .join(CaseRun, joins[1])
        .join(TestRun, joins[2])
        .where(*filters)
        .order_by(
            TestRun.created_at.desc(),
            CaseRun.sequence_no.asc(),
            RunRequirementSource.id.asc(),
        )
        .offset((query.page - 1) * query.page_size)
        .limit(query.page_size)
    ).all()
    items: list[RequirementTraceItem] = []
    for source, capture, case_run, run, artifact_count in rows:
        encoded_run_id = quote(run.id, safe="")
        evidence_query = urlencode(
            {
                "project_id": run.project_id,
                "run_id": run.id,
                "case_run_id": case_run.id,
            }
        )
        items.append(
            RequirementTraceItem(
                source_id=source.id,
                capture_id=capture.id,
                requirement_id=source.requirement_id,
                requirement_version_id=source.requirement_version_id,
                requirement_version_no=source.requirement_version_no,
                requirement_version_binding=source.requirement_version_binding,
                target_type=source.target_type,
                target_asset_id=source.target_asset_id,
                target_version_id=source.target_version_id,
                asset_version_binding=source.asset_version_binding,
                run_id=run.id,
                run_code=redact_text(run.run_code),
                run_type=run.run_type,
                run_status=run.status,
                run_created_at=run.created_at,
                run_ended_at=run.ended_at,
                case_run_id=case_run.id,
                case_sequence_no=case_run.sequence_no,
                case_status=case_run.status,
                evidence_count=int(artifact_count or 0),
                captured_at=capture.captured_at,
                report_path=f"/api/v1/reports/{encoded_run_id}",
                evidence_path=f"/api/v1/evidence?{evidence_query}",
            )
        )
    return RequirementTracePage(
        items=items,
        total=total,
        page=query.page,
        page_size=query.page_size,
        has_more=query.page * query.page_size < total,
    )


def _mysql_operational_error_code(exc: OperationalError) -> int | None:
    original = exc.orig
    errno = getattr(original, "errno", None)
    if isinstance(errno, int):
        return errno
    arguments = getattr(original, "args", ())
    if arguments and isinstance(arguments[0], int):
        return arguments[0]
    return None


@contextmanager
def _write_transaction_guard(
    session: Session,
    integrity_message: str,
) -> Iterator[None]:
    """Rollback failed writes and narrowly map known MySQL lock conflicts."""

    try:
        yield
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError(integrity_message) from exc
    except OperationalError as exc:
        session.rollback()
        if _mysql_operational_error_code(exc) in _MYSQL_LOCK_CONFLICT_CODES:
            raise ResourceConflictError(_LOCK_CONFLICT_MESSAGE) from exc
        raise


def _get_requirement(
    session: Session,
    user: CurrentUser,
    requirement_id: int,
    *,
    writable: bool = False,
) -> Requirement:
    if writable:
        requirement = session.scalar(
            select(Requirement)
            .where(Requirement.id == requirement_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    else:
        requirement = session.get(Requirement, requirement_id)
    if requirement is None:
        raise ResourceNotFoundError("需求不存在")
    project = get_project(session, user, requirement.project_id)
    if writable:
        ensure_project_writable(session, project, user)
        if project.status == ProjectStatus.ARCHIVED.value:
            raise ResourceConflictError("归档项目不能修改需求关联")
        if requirement.status != "ACTIVE":
            raise ResourceConflictError("归档需求不能修改关联")
    return requirement


def _requirement_version(
    session: Session,
    requirement: Requirement,
    version_id: int,
) -> RequirementVersion:
    version = session.get(RequirementVersion, version_id)
    if version is None or version.requirement_id != requirement.id:
        raise ResourceConflictError("需求版本不存在或不属于指定需求")
    return version


def _link_target_conditions(
    requirement_id: int,
    asset_type: RequirementAssetType,
    asset_id: int,
    relation_type: str,
) -> tuple[Any, ...]:
    target = (
        RequirementCaseLink.case_id == asset_id
        if asset_type == RequirementAssetType.TEST_CASE
        else RequirementCaseLink.web_case_id == asset_id
    )
    return (
        RequirementCaseLink.requirement_id == requirement_id,
        RequirementCaseLink.asset_type == asset_type.value,
        target,
        RequirementCaseLink.relation_type == relation_type,
    )


def _create_target(
    session: Session,
    requirement: Requirement,
    payload: RequirementLinkCreate,
) -> tuple[TestCase | WebCase, TestCaseVersion | WebCaseVersion, str | None]:
    if payload.asset_type == RequirementAssetType.TEST_CASE:
        asset = session.get(TestCase, payload.asset_id)
        if asset is None or asset.project_id != requirement.project_id:
            raise ResourceConflictError("TestCase 不存在或不属于需求项目")
        if asset.status != "ACTIVE":
            raise ResourceConflictError("归档 TestCase 不能新增关联")
        version = session.get(TestCaseVersion, payload.asset_version_id)
        if version is None or version.case_id != asset.id:
            raise ResourceConflictError("TestCaseVersion 不存在或不属于指定 TestCase")
        return asset, version, asset.case_type

    asset = session.get(WebCase, payload.asset_id)
    if asset is None or asset.project_id != requirement.project_id:
        raise ResourceConflictError("WebCase 不存在或不属于需求项目")
    if asset.status == "ARCHIVED":
        raise ResourceConflictError("归档 WebCase 不能新增关联")
    version = session.get(WebCaseVersion, payload.asset_version_id)
    if version is None or version.web_case_id != asset.id:
        raise ResourceConflictError("WebCaseVersion 不存在或不属于指定 WebCase")
    return asset, version, None


def create_requirement_link(
    session: Session,
    user: CurrentUser,
    requirement_id: int,
    payload: RequirementLinkCreate,
) -> RequirementLinkResponse:
    with _write_transaction_guard(
        session,
        "需求关联已存在或关联数据发生并发冲突",
    ):
        # Every association writer takes the owning Requirement row first.
        # This gives create/remove/re-link a stable common lock order without
        # adding locks to read-only list or impact queries.
        requirement = _get_requirement(session, user, requirement_id, writable=True)
        requirement_version = _requirement_version(
            session,
            requirement,
            payload.requirement_version_id,
        )
        asset, asset_version, case_type = _create_target(session, requirement, payload)
        conditions = _link_target_conditions(
            requirement.id,
            payload.asset_type,
            payload.asset_id,
            payload.relation_type,
        )
        active = session.scalar(
            select(RequirementCaseLink)
            .where(*conditions, RequirementCaseLink.status == "ACTIVE")
            .with_for_update()
        )
        if active is not None:
            raise ResourceConflictError("相同需求、资产和关系类型的活动关联已存在")
        previous = session.scalar(
            select(RequirementCaseLink)
            .where(*conditions, RequirementCaseLink.status == "REMOVED")
            .order_by(RequirementCaseLink.id.desc())
            .limit(1)
            .with_for_update()
        )
        link = RequirementCaseLink(
            requirement_id=requirement.id,
            requirement_version_id=requirement_version.id,
            asset_type=payload.asset_type.value,
            case_type=case_type,
            case_id=(
                asset.id if payload.asset_type == RequirementAssetType.TEST_CASE else None
            ),
            case_version_id=(
                asset_version.id
                if payload.asset_type == RequirementAssetType.TEST_CASE
                else None
            ),
            web_case_id=(
                asset.id if payload.asset_type == RequirementAssetType.WEB_CASE else None
            ),
            web_case_version_id=(
                asset_version.id
                if payload.asset_type == RequirementAssetType.WEB_CASE
                else None
            ),
            relation_type=payload.relation_type,
            source="MANUAL",
            confidence=Decimal(str(payload.confidence)),
            status="ACTIVE",
            active_slot=1,
            supersedes_link_id=previous.id if previous else None,
            created_by=user.id,
            created_at_time_basis=RequirementAuditTimeBasis.UTC.value,
            created_at=utc_now_naive(),
        )
        session.add(link)
        session.commit()
    # Response reads deliberately remain outside the pre-commit error mapping:
    # a failure here must not claim that an already committed write rolled back.
    session.refresh(link)
    return _hydrate_links(session, [link])[0]


def _ensure_target_is_writable(
    session: Session,
    link: RequirementCaseLink,
    project_id: int,
) -> None:
    if link.asset_type == RequirementAssetType.TEST_CASE.value:
        asset = session.get(TestCase, link.case_id)
        if asset is None:
            raise ResourceNotFoundError("关联的 TestCase 不存在")
        if asset.project_id != project_id:
            raise ResourceConflictError("关联的 TestCase 与需求项目不匹配")
        if asset.status != "ACTIVE":
            raise ResourceConflictError("归档 TestCase 的关联不能修改")
        return
    asset = session.get(WebCase, link.web_case_id)
    if asset is None:
        raise ResourceNotFoundError("关联的 WebCase 不存在")
    if asset.project_id != project_id:
        raise ResourceConflictError("关联的 WebCase 与需求项目不匹配")
    if asset.status == "ARCHIVED":
        raise ResourceConflictError("归档 WebCase 的关联不能修改")


def remove_requirement_link(
    session: Session,
    user: CurrentUser,
    requirement_id: int,
    link_id: int,
) -> RequirementLinkResponse:
    with _write_transaction_guard(session, "需求关联移除发生并发冲突"):
        requirement = _get_requirement(session, user, requirement_id, writable=True)
        link = session.scalar(
            select(RequirementCaseLink)
            .where(
                RequirementCaseLink.id == link_id,
                RequirementCaseLink.requirement_id == requirement.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if link is None:
            raise ResourceNotFoundError("需求关联不存在")
        if link.status != RequirementLinkStatus.ACTIVE.value:
            raise ResourceConflictError("需求关联已经移除")
        _ensure_target_is_writable(session, link, requirement.project_id)
        link.status = RequirementLinkStatus.REMOVED.value
        link.active_slot = None
        link.removed_by = user.id
        link.removed_at = utc_now_naive()
        session.commit()
    session.refresh(link)
    return _hydrate_links(session, [link])[0]


def _load_by_ids(session: Session, model: Any, identifiers: set[int]) -> dict[int, Any]:
    if not identifiers:
        return {}
    return {
        item.id: item
        for item in session.scalars(select(model).where(model.id.in_(identifiers))).all()
    }


def _binding_note(requirement_known: bool, asset_known: bool) -> str:
    if requirement_known and asset_known:
        return "EXACT_REQUIREMENT_AND_ASSET_VERSION"
    if not requirement_known and not asset_known:
        return "LEGACY_REQUIREMENT_AND_ASSET_VERSION_UNKNOWN"
    if not requirement_known:
        return "LEGACY_REQUIREMENT_BASELINE_UNKNOWN"
    return "ASSET_LEVEL_LINK_HISTORICAL_VERSION_NOT_RECORDED"


def _hydrate_links(
    session: Session,
    links: list[RequirementCaseLink],
) -> list[RequirementLinkResponse]:
    requirements = _load_by_ids(
        session,
        Requirement,
        {item.requirement_id for item in links},
    )
    requirement_versions = _load_by_ids(
        session,
        RequirementVersion,
        {item.requirement_version_id for item in links if item.requirement_version_id},
    )
    test_cases = _load_by_ids(
        session,
        TestCase,
        {item.case_id for item in links if item.case_id},
    )
    test_case_versions = _load_by_ids(
        session,
        TestCaseVersion,
        {item.case_version_id for item in links if item.case_version_id},
    )
    web_cases = _load_by_ids(
        session,
        WebCase,
        {item.web_case_id for item in links if item.web_case_id},
    )
    web_case_versions = _load_by_ids(
        session,
        WebCaseVersion,
        {item.web_case_version_id for item in links if item.web_case_version_id},
    )

    responses: list[RequirementLinkResponse] = []
    for link in links:
        requirement = requirements.get(link.requirement_id)
        if requirement is None:
            raise ResourceConflictError("需求关联引用的需求不存在")
        requirement_version = requirement_versions.get(link.requirement_version_id)
        requirement_known = bool(
            requirement_version
            and requirement_version.requirement_id == requirement.id
        )
        if link.asset_type == RequirementAssetType.TEST_CASE.value:
            asset = test_cases.get(link.case_id)
            asset_version = test_case_versions.get(link.case_version_id)
            asset_known = bool(asset_version and asset and asset_version.case_id == asset.id)
            version_status = None
            asset_id = link.case_id
            case_type = asset.case_type if asset else link.case_type
        else:
            asset = web_cases.get(link.web_case_id)
            asset_version = web_case_versions.get(link.web_case_version_id)
            asset_known = bool(
                asset_version and asset and asset_version.web_case_id == asset.id
            )
            version_status = asset_version.status if asset_version else None
            asset_id = link.web_case_id
            case_type = None
        if asset is None or asset_id is None:
            raise ResourceConflictError("需求关联引用的测试资产不存在")
        if asset.project_id != requirement.project_id:
            raise ResourceConflictError("需求关联引用的测试资产与需求项目不匹配")

        responses.append(
            RequirementLinkResponse(
                id=link.id,
                requirement_id=requirement.id,
                requirement_code=requirement.code,
                requirement_title=requirement.title,
                requirement_status=requirement.status,
                requirement_version_id=link.requirement_version_id,
                requirement_version=(
                    RequirementVersionReference(
                        id=requirement_version.id,
                        version_no=requirement_version.version_no,
                        content_hash=requirement_version.content_hash,
                    )
                    if requirement_known
                    else None
                ),
                requirement_baseline_known=requirement_known,
                asset_type=link.asset_type,
                asset_id=asset_id,
                asset=LinkedAssetReference(
                    asset_type=link.asset_type,
                    id=asset.id,
                    code=asset.code,
                    name=asset.name,
                    status=asset.status,
                    case_type=case_type,
                    current_version_id=asset.current_version_id,
                ),
                asset_version_id=(
                    link.case_version_id
                    if link.asset_type == RequirementAssetType.TEST_CASE.value
                    else link.web_case_version_id
                ),
                asset_version=(
                    LinkedAssetVersionReference(
                        id=asset_version.id,
                        version_no=asset_version.version_no,
                        status=version_status,
                    )
                    if asset_known
                    else None
                ),
                asset_version_known=asset_known,
                binding_note=_binding_note(requirement_known, asset_known),
                relation_type=link.relation_type,
                source=link.source,
                confidence=float(link.confidence),
                status=link.status,
                is_current_relation=link.status == RequirementLinkStatus.ACTIVE.value,
                supersedes_link_id=link.supersedes_link_id,
                created_by=link.created_by,
                created_at=(
                    to_utc_aware(link.created_at)
                    if link.created_at_time_basis == RequirementAuditTimeBasis.UTC.value
                    else link.created_at
                ),
                created_at_time_basis=link.created_at_time_basis,
                removed_by=link.removed_by,
                removed_at=to_utc_aware(link.removed_at),
                removed_at_time_basis=("UTC" if link.removed_at is not None else None),
            )
        )
    return responses


def _page_links(
    session: Session,
    conditions: tuple[Any, ...],
    query: RequirementLinkQuery,
) -> RequirementLinkPage:
    total = int(
        session.scalar(
            select(func.count()).select_from(RequirementCaseLink).where(*conditions)
        )
        or 0
    )
    links = list(
        session.scalars(
            select(RequirementCaseLink)
            .where(*conditions)
            .order_by(RequirementCaseLink.id.asc())
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        ).all()
    )
    return RequirementLinkPage(
        items=_hydrate_links(session, links),
        total=total,
        page=query.page,
        page_size=query.page_size,
        has_more=query.page * query.page_size < total,
    )


def list_requirement_links(
    session: Session,
    user: CurrentUser,
    requirement_id: int,
    query: RequirementLinkQuery,
) -> RequirementLinkPage:
    requirement = _get_requirement(session, user, requirement_id)
    conditions: list[Any] = [RequirementCaseLink.requirement_id == requirement.id]
    if not query.include_removed:
        conditions.append(RequirementCaseLink.status == RequirementLinkStatus.ACTIVE.value)
    return _page_links(session, tuple(conditions), query)


def list_asset_requirements(
    session: Session,
    user: CurrentUser,
    asset_type: RequirementAssetType,
    asset_id: int,
    query: RequirementLinkQuery,
) -> RequirementLinkPage:
    if asset_type == RequirementAssetType.TEST_CASE:
        asset = session.get(TestCase, asset_id)
        target = RequirementCaseLink.case_id == asset_id
    else:
        asset = session.get(WebCase, asset_id)
        target = RequirementCaseLink.web_case_id == asset_id
    if asset is None:
        raise ResourceNotFoundError("测试资产不存在")
    get_project(session, user, asset.project_id)
    conditions: list[Any] = [
        RequirementCaseLink.asset_type == asset_type.value,
        target,
    ]
    if not query.include_removed:
        conditions.append(RequirementCaseLink.status == RequirementLinkStatus.ACTIVE.value)
    return _page_links(session, tuple(conditions), query)


def _version_reference(version: RequirementVersion) -> RequirementVersionReference:
    return RequirementVersionReference(
        id=version.id,
        version_no=version.version_no,
        content_hash=version.content_hash,
    )


def analyze_requirement_impact(
    session: Session,
    user: CurrentUser,
    requirement_id: int,
    query: RequirementImpactQuery,
) -> RequirementImpactPage:
    requirement = _get_requirement(session, user, requirement_id)
    versions = list(
        session.scalars(
            select(RequirementVersion).where(
                RequirementVersion.id.in_(
                    [query.from_version_id, query.to_version_id]
                ),
                RequirementVersion.requirement_id == requirement.id,
            )
        ).all()
    )
    version_map = {item.id: item for item in versions}
    from_version = version_map.get(query.from_version_id)
    to_version = version_map.get(query.to_version_id)
    if from_version is None or to_version is None:
        raise ResourceNotFoundError("影响分析版本不存在或不属于指定需求")

    _diff_lines, additions, deletions = calculate_version_diff(
        from_version,
        to_version,
    )
    content_changed = from_version.markdown_content != to_version.markdown_content
    link_page = _page_links(
        session,
        (
            RequirementCaseLink.requirement_id == requirement.id,
            RequirementCaseLink.status == RequirementLinkStatus.ACTIVE.value,
        ),
        RequirementLinkQuery(page=query.page, page_size=query.page_size),
    )
    selected_status = (
        RequirementImpactStatus.POSSIBLY_OUTDATED
        if content_changed
        else RequirementImpactStatus.NO_CHANGE
    )
    selected_reason = _SCOPE_NOTE if content_changed else "选定版本内容没有变化"
    items: list[RequirementImpactItem] = []
    for link in link_page.items:
        if not link.requirement_baseline_known or link.requirement_version is None:
            baseline_status = RequirementImpactStatus.BASELINE_UNKNOWN
            baseline_reason = (
                "链接未记录可靠需求来源版本；选定版本比较仅表示潜在关联范围"
            )
        elif link.requirement_version.content_hash == to_version.content_hash:
            baseline_status = RequirementImpactStatus.NO_CHANGE
            baseline_reason = "链接记录的需求基线与目标版本内容相同"
        else:
            baseline_status = RequirementImpactStatus.POSSIBLY_OUTDATED
            baseline_reason = _SCOPE_NOTE
        items.append(
            RequirementImpactItem(
                **link.model_dump(),
                selected_scope_status=selected_status,
                selected_scope_reason=selected_reason,
                recorded_baseline_status=baseline_status,
                recorded_baseline_reason=baseline_reason,
            )
        )

    return RequirementImpactPage(
        comparison=RequirementImpactComparison(
            requirement_id=requirement.id,
            from_version=_version_reference(from_version),
            to_version=_version_reference(to_version),
            content_changed=content_changed,
            additions=additions,
            deletions=deletions,
        ),
        items=items,
        total=link_page.total,
        page=link_page.page,
        page_size=link_page.page_size,
        has_more=link_page.has_more,
    )
