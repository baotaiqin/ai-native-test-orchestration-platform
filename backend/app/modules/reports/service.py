from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlencode

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceNotFoundError
from app.core.redaction import redact_text, redact_value
from app.core.time import to_utc_aware, utc_now_aware
from app.modules.auth.schemas import CurrentUser
from app.modules.environments.models import Environment
from app.modules.evidence import service as evidence_service
from app.modules.evidence.models import EvidenceArtifact
from app.modules.projects.models import Project
from app.modules.projects.service import get_project
from app.modules.run_requirement_snapshots.models import (
    RunRequirementCapture,
    RunRequirementSource,
)
from app.modules.runners.models import Runner
from app.modules.runs.enums import RunNodeStatus, RunType
from app.modules.runs.models import (
    CaseRun,
    RunApiExecutionResult,
    RunScenarioExecutionResult,
    RunWebExecutionResult,
    StepRun,
    TestRun,
)
from app.modules.scenarios.models import Scenario, ScenarioVersion
from app.modules.test_cases.models import TestCase, TestCaseVersion
from app.modules.web_cases.models import WebCase, WebCaseVersion
from app.modules.web_failure_analysis.models import WebFailureAnalysis
from app.modules.web_healing.models import WebHealingProposal

from .schemas import (
    DurationKind,
    RecordingAvailability,
    ReportAiAuditGroup,
    ReportAiAuditPreview,
    ReportCase,
    ReportCasePage,
    ReportCaseStatusCounts,
    ReportDataField,
    ReportDetailResponse,
    ReportDuration,
    ReportEnvironmentReference,
    ReportEvidence,
    ReportEvidencePage,
    ReportEvidencePageQuery,
    ReportExecutionResult,
    ReportListQuery,
    ReportListResponse,
    ReportPageQuery,
    ReportProjectReference,
    ReportRate,
    ReportRecordedCounts,
    ReportRelatedAi,
    ReportRequirementCapture,
    ReportRequirementSource,
    ReportRequirementSourcePage,
    ReportRequirementSourcePageQuery,
    ReportRunnerReference,
    ReportStep,
    ReportStepPage,
    ReportStepPageQuery,
    ReportSummary,
    ReportTargetReference,
    RequirementCaptureStatus,
)

REPORT_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
DETAIL_CASE_PAGE_SIZE = 20
DETAIL_STEP_PAGE_SIZE = 50
DETAIL_EVIDENCE_PAGE_SIZE = 50
DETAIL_REQUIREMENT_SOURCE_PAGE_SIZE = 50


@dataclass
class _ReferenceContext:
    environments: dict[int, Environment]
    runners: dict[str, Runner]
    api_cases: dict[int, TestCase]
    api_versions: dict[int, TestCaseVersion]
    scenarios: dict[int, Scenario]
    scenario_versions: dict[int, ScenarioVersion]
    web_cases: dict[int, WebCase]
    web_versions: dict[int, WebCaseVersion]


def _mapping_by_id(items: Iterable[Any]) -> dict[Any, Any]:
    return {item.id: item for item in items}


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _local_day_start(value: date) -> datetime:
    local = datetime.combine(value, time.min, tzinfo=REPORT_TIMEZONE)
    return local.astimezone(UTC).replace(tzinfo=None)


def _load_reference_context(
    session: Session, records: Iterable[TestRun | CaseRun]
) -> _ReferenceContext:
    record_list = list(records)
    environment_ids = {
        item.environment_id
        for item in record_list
        if isinstance(item, TestRun) and item.environment_id is not None
    }
    runner_ids = {
        item.runner_id
        for item in record_list
        if isinstance(item, TestRun) and item.runner_id is not None
    }
    api_case_ids = {item.case_id for item in record_list if item.case_id is not None}
    api_version_ids = {
        item.case_version_id for item in record_list if item.case_version_id is not None
    }
    scenario_ids = {
        item.scenario_id for item in record_list if item.scenario_id is not None
    }
    scenario_version_ids = {
        item.scenario_version_id
        for item in record_list
        if item.scenario_version_id is not None
    }
    web_case_ids = {
        item.web_case_id for item in record_list if item.web_case_id is not None
    }
    web_version_ids = {
        item.web_case_version_id
        for item in record_list
        if item.web_case_version_id is not None
    }

    def load(model: Any, ids: set[Any]) -> dict[Any, Any]:
        if not ids:
            return {}
        return _mapping_by_id(session.scalars(select(model).where(model.id.in_(ids))).all())

    return _ReferenceContext(
        environments=load(Environment, environment_ids),
        runners=load(Runner, runner_ids),
        api_cases=load(TestCase, api_case_ids),
        api_versions=load(TestCaseVersion, api_version_ids),
        scenarios=load(Scenario, scenario_ids),
        scenario_versions=load(ScenarioVersion, scenario_version_ids),
        web_cases=load(WebCase, web_case_ids),
        web_versions=load(WebCaseVersion, web_version_ids),
    )


def _target_kind(record: TestRun | CaseRun) -> RunType:
    if record.case_id is not None:
        return RunType.API_CASE
    if record.scenario_id is not None:
        return RunType.SCENARIO
    return RunType.WEB_CASE


def _target_reference(
    record: TestRun | CaseRun, context: _ReferenceContext
) -> ReportTargetReference:
    kind = _target_kind(record)
    if kind == RunType.API_CASE:
        asset_id = record.case_id
        version_id = record.case_version_id
        asset = context.api_cases.get(asset_id)
        version = context.api_versions.get(version_id)
        version_matches = version is not None and version.case_id == asset_id
        version_status = None
    elif kind == RunType.SCENARIO:
        asset_id = record.scenario_id
        version_id = record.scenario_version_id
        asset = context.scenarios.get(asset_id)
        version = context.scenario_versions.get(version_id)
        version_matches = version is not None and version.scenario_id == asset_id
        version_status = None
    else:
        asset_id = record.web_case_id
        version_id = record.web_case_version_id
        asset = context.web_cases.get(asset_id)
        version = context.web_versions.get(version_id)
        version_matches = version is not None and version.web_case_id == asset_id
        version_status = version.status if version_matches else None
    if asset_id is None or version_id is None:
        raise ValueError("Run target reference is incomplete")
    current_version_id = asset.current_version_id if asset is not None else None
    return ReportTargetReference(
        kind=kind,
        asset_id=asset_id,
        version_id=version_id,
        asset_name=(redact_text(asset.name) if asset is not None else None),
        asset_code=(redact_text(asset.code) if asset is not None else None),
        asset_status=(asset.status if asset is not None else None),
        current_version_id=current_version_id,
        version_no=(version.version_no if version_matches else None),
        version_status=version_status,
        version_available=version_matches,
        is_current_version=(
            current_version_id == version_id if asset is not None else None
        ),
    )


def _environment_reference(
    run: TestRun, context: _ReferenceContext
) -> ReportEnvironmentReference | None:
    if run.environment_id is None:
        return None
    environment = context.environments.get(run.environment_id)
    return ReportEnvironmentReference(
        id=run.environment_id,
        name=redact_text(environment.name) if environment is not None else None,
        enabled=environment.enabled if environment is not None else None,
        available=environment is not None,
    )


def _runner_reference(
    run: TestRun, context: _ReferenceContext
) -> ReportRunnerReference | None:
    if run.runner_id is None:
        return None
    runner = context.runners.get(run.runner_id)
    return ReportRunnerReference(
        id=run.runner_id,
        name=redact_text(runner.name) if runner is not None else None,
        status=runner.status if runner is not None else None,
        available=runner is not None,
    )


def _case_counts(
    status_counts: dict[str, int] | None = None,
) -> tuple[ReportCaseStatusCounts, ReportRate]:
    counts = status_counts or {}
    created = counts.get(RunNodeStatus.CREATED.value, 0)
    assigned = counts.get(RunNodeStatus.ASSIGNED.value, 0)
    running = counts.get(RunNodeStatus.RUNNING.value, 0)
    cancelling = counts.get(RunNodeStatus.CANCELLING.value, 0)
    success = counts.get(RunNodeStatus.SUCCESS.value, 0)
    failed = counts.get(RunNodeStatus.FAILED.value, 0)
    review = counts.get(RunNodeStatus.REVIEW.value, 0)
    timeout = counts.get(RunNodeStatus.TIMEOUT.value, 0)
    cancelled = counts.get(RunNodeStatus.CANCELLED.value, 0)
    skipped = counts.get(RunNodeStatus.SKIPPED.value, 0)
    unfinished = created + assigned + running + cancelling
    total = sum(
        (
            unfinished,
            success,
            failed,
            review,
            timeout,
            cancelled,
            skipped,
        )
    )
    denominator = success + failed + review + timeout
    return (
        ReportCaseStatusCounts(
            created=created,
            assigned=assigned,
            running=running,
            cancelling=cancelling,
            success=success,
            failed=failed,
            review=review,
            timeout=timeout,
            cancelled=cancelled,
            skipped=skipped,
            unfinished=unfinished,
            total=total,
        ),
        ReportRate(
            numerator=success,
            denominator=denominator,
            value=(success / denominator if denominator else None),
            definition=(
                "成功用例数 /（成功 + 失败 + 待复核 + 超时用例数）；"
                "已跳过、已取消与未完成用例不进入分母"
            ),
        ),
    )


def _load_case_counts(
    session: Session, run_ids: list[str]
) -> dict[str, dict[str, int]]:
    if not run_ids:
        return {}
    rows = session.execute(
        select(CaseRun.run_id, CaseRun.status, func.count(CaseRun.id))
        .where(CaseRun.run_id.in_(run_ids))
        .group_by(CaseRun.run_id, CaseRun.status)
    ).all()
    grouped: dict[str, dict[str, int]] = defaultdict(dict)
    for run_id, status, count in rows:
        grouped[run_id][status] = int(count)
    return dict(grouped)


def _duration(run: TestRun, observed_at: datetime) -> ReportDuration:
    if run.started_at is None:
        return ReportDuration(
            milliseconds=None,
            kind=DurationKind.UNAVAILABLE,
            observed_at=to_utc_aware(observed_at),
        )
    end = run.ended_at
    if end is not None:
        kind = DurationKind.COMPLETED
    else:
        end = _utc_naive(observed_at)
        kind = DurationKind.OBSERVED
    milliseconds = max(0, int((end - run.started_at).total_seconds() * 1000))
    return ReportDuration(
        milliseconds=milliseconds,
        kind=kind,
        observed_at=to_utc_aware(observed_at),
    )


def _report_summary(
    run: TestRun,
    project: Project,
    context: _ReferenceContext,
    counts_by_run: dict[str, dict[str, int]],
    observed_at: datetime,
) -> ReportSummary:
    case_counts, case_rate = _case_counts(counts_by_run.get(run.id))
    return ReportSummary(
        run_id=run.id,
        run_code=redact_text(run.run_code),
        run_type=run.run_type,
        project=ReportProjectReference(
            id=project.id,
            name=redact_text(project.name),
            status=project.status,
        ),
        environment=_environment_reference(run, context),
        runner=_runner_reference(run, context),
        target=_target_reference(run, context),
        status=run.status,
        trigger_type=run.trigger_type,
        started_at=to_utc_aware(run.started_at),
        ended_at=to_utc_aware(run.ended_at),
        created_at=to_utc_aware(run.created_at),
        updated_at=to_utc_aware(run.updated_at),
        duration=_duration(run, observed_at),
        error_type=redact_text(run.error_type) if run.error_type else None,
        error_message=redact_text(run.error_message) if run.error_message else None,
        recorded_counts=ReportRecordedCounts(
            total=run.total,
            passed=run.pass_count,
            failed=run.fail_count,
            review=run.review_count,
            timeout=run.timeout_count,
        ),
        case_status_counts=case_counts,
        case_success_rate=case_rate,
    )


def _load_run_visible(
    session: Session, user: CurrentUser, run_id: str
) -> tuple[TestRun, Project]:
    run = session.get(TestRun, run_id)
    if run is None:
        raise ResourceNotFoundError("报告不存在")
    project = get_project(session, user, run.project_id)
    return run, project


def list_reports(
    session: Session,
    user: CurrentUser,
    query: ReportListQuery,
    *,
    observed_at: datetime | None = None,
) -> ReportListResponse:
    project = get_project(session, user, query.project_id)
    filters: list[Any] = [TestRun.project_id == project.id]
    if query.run_type is not None:
        filters.append(TestRun.run_type == query.run_type.value)
    if query.status is not None:
        filters.append(TestRun.status == query.status.value)
    if query.environment_id is not None:
        filters.append(TestRun.environment_id == query.environment_id)
    if query.created_from is not None:
        filters.append(TestRun.created_at >= _local_day_start(query.created_from))
    if query.created_to is not None:
        filters.append(
            TestRun.created_at < _local_day_start(query.created_to + timedelta(days=1))
        )
    total = int(
        session.scalar(select(func.count(TestRun.id)).where(*filters)) or 0
    )
    runs = list(
        session.scalars(
            select(TestRun)
            .where(*filters)
            .order_by(TestRun.created_at.desc(), TestRun.id.desc())
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        ).all()
    )
    context = _load_reference_context(session, runs)
    counts = _load_case_counts(session, [run.id for run in runs])
    now = observed_at or utc_now_aware()
    return ReportListResponse(
        items=[
            _report_summary(run, project, context, counts, now) for run in runs
        ],
        total=total,
        page=query.page,
        page_size=query.page_size,
        has_more=query.page * query.page_size < total,
    )


def _field(
    availability: RecordingAvailability,
    note: str,
    value: Any = None,
) -> ReportDataField:
    return ReportDataField(
        availability=availability,
        value=redact_value(value) if availability == RecordingAvailability.RECORDED else None,
        note=note,
    )


def _locked_api_request_snapshot(
    case_run: CaseRun,
    context: _ReferenceContext,
) -> Mapping[str, Any] | None:
    if case_run.case_id is None or case_run.case_version_id is None:
        return None
    version = context.api_versions.get(case_run.case_version_id)
    if version is None or version.case_id != case_run.case_id:
        return None
    content = version.content
    if not isinstance(content, Mapping):
        return None
    request = content.get("request")
    return request if isinstance(request, Mapping) else None


def _request_field(
    kind: RunType,
    request_snapshot: Mapping[str, Any] | None,
) -> ReportDataField:
    if kind != RunType.API_CASE:
        return _field(
            RecordingAvailability.NOT_APPLICABLE,
            "该执行类型不使用单一 API 请求配置",
        )
    if request_snapshot is None:
        return _field(
            RecordingAvailability.NOT_RECORDED,
            "执行锁定版本未保存请求配置",
        )
    return _field(
        RecordingAvailability.RECORDED,
        "执行锁定版本中的请求配置快照；敏感值已脱敏，动态变量和 Secret "
        "保留引用，不代表解析后的网络报文",
        request_snapshot,
    )


def _missing_result(
    kind: RunType,
    request_snapshot: Mapping[str, Any] | None = None,
) -> ReportExecutionResult:
    applicable_response = kind == RunType.API_CASE
    return ReportExecutionResult(
        kind=kind,
        availability=RecordingAvailability.NOT_RECORDED,
        message_id=None,
        outcome=None,
        status=None,
        retry_count=None,
        error_type=None,
        error_message=None,
        completed_at=None,
        actual_request=_request_field(kind, request_snapshot),
        response=_field(
            RecordingAvailability.NOT_RECORDED
            if applicable_response
            else RecordingAvailability.NOT_APPLICABLE,
            "没有持久化的响应摘要"
            if applicable_response
            else "该执行类型没有 API 响应字段",
        ),
        extractions=_field(
            RecordingAvailability.NOT_RECORDED
            if applicable_response
            else RecordingAvailability.NOT_APPLICABLE,
            "执行时提取结果未持久化"
            if applicable_response
            else "该执行类型没有独立提取结果字段",
        ),
        assertions=_field(
            RecordingAvailability.NOT_RECORDED
            if applicable_response
            else RecordingAvailability.NOT_APPLICABLE,
            "没有持久化的断言结果"
            if applicable_response
            else "断言结果包含在类型化执行轨迹和步骤运行记录中",
        ),
        traces=_field(
            RecordingAvailability.NOT_RECORDED,
            "没有持久化的执行轨迹",
        ),
    )


def _load_execution_results(
    session: Session, case_ids: list[int]
) -> dict[int, RunApiExecutionResult | RunScenarioExecutionResult | RunWebExecutionResult]:
    if not case_ids:
        return {}
    result: dict[
        int, RunApiExecutionResult | RunScenarioExecutionResult | RunWebExecutionResult
    ] = {}
    for model in (
        RunApiExecutionResult,
        RunScenarioExecutionResult,
        RunWebExecutionResult,
    ):
        records = session.scalars(
            select(model).where(model.case_run_id.in_(case_ids))
        ).all()
        for record in records:
            result[record.case_run_id] = record
    return result


def _execution_result(
    case_run: CaseRun,
    result: RunApiExecutionResult | RunScenarioExecutionResult | RunWebExecutionResult | None,
    request_snapshot: Mapping[str, Any] | None = None,
) -> ReportExecutionResult:
    kind = _target_kind(case_run)
    if result is None:
        return _missing_result(kind, request_snapshot)
    common = {
        "kind": kind,
        "availability": RecordingAvailability.RECORDED,
        "message_id": result.message_id,
        "outcome": result.outcome,
        "status": result.status,
        "error_type": redact_text(result.error_type) if result.error_type else None,
        "error_message": redact_text(result.error_message) if result.error_message else None,
        "completed_at": to_utc_aware(result.completed_at),
        "actual_request": _request_field(kind, request_snapshot),
    }
    if isinstance(result, RunApiExecutionResult):
        response_availability = (
            RecordingAvailability.RECORDED
            if result.response_summary is not None
            else RecordingAvailability.NOT_RECORDED
        )
        return ReportExecutionResult(
            **common,
            retry_count=result.retry_count,
            response=_field(
                response_availability,
                "执行时保存的安全响应摘要"
                if result.response_summary is not None
                else "HTTP 响应摘要未记录",
                result.response_summary,
            ),
            extractions=_field(
                RecordingAvailability.NOT_RECORDED,
                "执行时提取结果未持久化",
            ),
            assertions=_field(
                RecordingAvailability.RECORDED,
                "执行时保存的断言结果",
                result.assertion_results,
            ),
            traces=_field(
                RecordingAvailability.RECORDED,
                "执行时保存的 API 动作级执行轨迹（仅元数据）",
                result.action_traces,
            ),
        )
    if isinstance(result, RunScenarioExecutionResult):
        trace_note = "执行时保存的编排场景执行轨迹"
        assertions = _field(
            RecordingAvailability.RECORDED,
            "执行时保存的 Scenario AI 断言结果",
            result.assertion_results or [],
        )
    else:
        trace_note = "执行时保存的 Web 执行轨迹"
        assertions = _field(
            RecordingAvailability.NOT_APPLICABLE,
            "断言结果包含在类型化执行轨迹和步骤运行记录中",
        )
    return ReportExecutionResult(
        **common,
        retry_count=None,
        response=_field(
            RecordingAvailability.NOT_APPLICABLE,
            "该执行类型没有 API 响应字段",
        ),
        extractions=_field(
            RecordingAvailability.NOT_APPLICABLE,
            "该执行类型没有独立提取结果字段",
        ),
        assertions=assertions,
        traces=_field(RecordingAvailability.RECORDED, trace_note, result.traces),
    )


def _report_case(
    case_run: CaseRun,
    context: _ReferenceContext,
    results: dict[
        int, RunApiExecutionResult | RunScenarioExecutionResult | RunWebExecutionResult
    ],
    requirement_captures: dict[int, RunRequirementCapture],
) -> ReportCase:
    return ReportCase(
        id=case_run.id,
        sequence_no=case_run.sequence_no,
        run_id=case_run.run_id,
        target=_target_reference(case_run, context),
        status=case_run.status,
        duration_ms=case_run.duration,
        retry_count=case_run.retry_count,
        started_at=to_utc_aware(case_run.started_at),
        ended_at=to_utc_aware(case_run.ended_at),
        error_type=redact_text(case_run.error_type) if case_run.error_type else None,
        error_message=(
            redact_text(case_run.error_message) if case_run.error_message else None
        ),
        execution_result=_execution_result(
            case_run,
            results.get(case_run.id),
            _locked_api_request_snapshot(case_run, context),
        ),
        requirement_capture=_report_requirement_capture(
            case_run, requirement_captures.get(case_run.id)
        ),
    )


def _case_target_ids(case_run: CaseRun) -> tuple[RunType, int, int]:
    target_type = _target_kind(case_run)
    if target_type == RunType.API_CASE:
        assert case_run.case_id is not None and case_run.case_version_id is not None
        return target_type, case_run.case_id, case_run.case_version_id
    if target_type == RunType.SCENARIO:
        assert (
            case_run.scenario_id is not None
            and case_run.scenario_version_id is not None
        )
        return target_type, case_run.scenario_id, case_run.scenario_version_id
    assert case_run.web_case_id is not None and case_run.web_case_version_id is not None
    return target_type, case_run.web_case_id, case_run.web_case_version_id


def _report_requirement_capture(
    case_run: CaseRun,
    capture: RunRequirementCapture | None,
) -> ReportRequirementCapture:
    target_type, asset_id, version_id = _case_target_ids(case_run)
    if capture is None:
        return ReportRequirementCapture(
            case_run_id=case_run.id,
            status=RequirementCaptureStatus.NOT_RECORDED,
            captured_at=None,
            captured_at_time_basis=None,
            target_type=target_type,
            target_asset_id=asset_id,
            target_version_id=version_id,
            total=0,
            consistency_basis=None,
            note=(
                "该用例运行创建时尚未启用需求来源快照；不会用当前关联回填，"
                "因此不能据此证明运行时没有需求来源"
            ),
        )
    notes = {
        "CAPTURED": "已在创建 Run 的同一数据库事务中记录全部运行需求来源",
        "CAPTURED_EMPTY": (
            "创建 Run 时已完成捕获，但没有符合锁定资产版本或历史版本未知规则的 "
            "ACTIVE 需求关联"
        ),
        "UNSUPPORTED_TARGET": (
            "SCENARIO 目标当前不支持需求来源捕获；此状态不表示已证明不存在需求来源"
        ),
    }
    return ReportRequirementCapture(
        case_run_id=case_run.id,
        status=capture.capture_status,
        captured_at=to_utc_aware(capture.captured_at),
        captured_at_time_basis="UTC",
        target_type=capture.target_type,
        target_asset_id=capture.target_asset_id,
        target_version_id=capture.target_version_id,
        total=capture.item_count,
        consistency_basis="CREATE_RUN_TRANSACTION",
        note=notes[capture.capture_status],
    )


def _load_requirement_captures(
    session: Session, case_run_ids: list[int]
) -> dict[int, RunRequirementCapture]:
    if not case_run_ids:
        return {}
    return {
        capture.case_run_id: capture
        for capture in session.scalars(
            select(RunRequirementCapture).where(
                RunRequirementCapture.case_run_id.in_(case_run_ids)
            )
        ).all()
    }


def _continuation_path(
    run_id: str,
    resource: str,
    *,
    page: int,
    page_size: int,
    extra: dict[str, int | None] | None = None,
) -> str:
    parameters: dict[str, int] = {"page": page, "page_size": page_size}
    for key, value in (extra or {}).items():
        if value is not None:
            parameters[key] = value
    return (
        f"/api/v1/reports/{quote(run_id, safe='')}/{resource}?"
        f"{urlencode(parameters)}"
    )


def list_report_cases(
    session: Session,
    user: CurrentUser,
    run_id: str,
    query: ReportPageQuery,
) -> ReportCasePage:
    run, _ = _load_run_visible(session, user, run_id)
    total = int(
        session.scalar(
            select(func.count(CaseRun.id)).where(CaseRun.run_id == run.id)
        )
        or 0
    )
    case_runs = list(
        session.scalars(
            select(CaseRun)
            .where(CaseRun.run_id == run.id)
            .order_by(CaseRun.sequence_no.asc(), CaseRun.id.asc())
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        ).all()
    )
    context = _load_reference_context(session, case_runs)
    results = _load_execution_results(session, [item.id for item in case_runs])
    requirement_captures = _load_requirement_captures(
        session, [item.id for item in case_runs]
    )
    has_more = query.page * query.page_size < total
    next_page = query.page + 1 if has_more else None
    return ReportCasePage(
        run_id=run.id,
        items=[
            _report_case(item, context, results, requirement_captures)
            for item in case_runs
        ],
        total=total,
        page=query.page,
        page_size=query.page_size,
        has_more=has_more,
        next_page=next_page,
        continuation_path=(
            _continuation_path(
                run.id,
                "cases",
                page=next_page,
                page_size=query.page_size,
            )
            if next_page is not None
            else None
        ),
    )


def _report_requirement_source(
    source: RunRequirementSource,
    capture: RunRequirementCapture,
) -> ReportRequirementSource:
    if source.asset_version_binding == "ASSET_VERSION_UNKNOWN":
        asset_note = (
            "原始关联没有记录资产版本；仅能确认资产级历史关联，"
            "不能声称精确绑定本次运行版本"
        )
    else:
        asset_note = "原始关联精确绑定本次运行锁定的资产版本"
    if source.requirement_version_binding == "REQUIREMENT_VERSION_UNKNOWN":
        requirement_note = (
            "原始关联没有记录需求版本；需求版本号与内容哈希未知"
        )
    else:
        requirement_note = "原始关联精确绑定已快照的需求版本"
    return ReportRequirementSource(
        id=source.id,
        case_run_id=capture.case_run_id,
        sequence_no=source.sequence_no,
        original_link_id=source.original_link_id,
        supersedes_link_id=source.supersedes_link_id,
        requirement_id=source.requirement_id,
        requirement_code=redact_text(source.requirement_code),
        requirement_title=redact_text(source.requirement_title),
        requirement_type=source.requirement_type,
        requirement_status=source.requirement_status,
        requirement_version_id=source.requirement_version_id,
        requirement_version_no=source.requirement_version_no,
        requirement_content_hash=source.requirement_content_hash,
        requirement_source_type=source.requirement_source_type,
        requirement_version_binding=source.requirement_version_binding,
        target_type=source.target_type,
        link_asset_type=source.link_asset_type,
        target_asset_id=source.target_asset_id,
        target_version_id=source.target_version_id,
        link_asset_version_id=source.link_asset_version_id,
        asset_version_binding=source.asset_version_binding,
        binding_note=f"{asset_note}；{requirement_note}",
        relation_type=source.relation_type,
        source=source.source,
        confidence=float(source.confidence),
        link_created_by=(
            redact_text(source.link_created_by) if source.link_created_by else None
        ),
        link_created_at=(
            to_utc_aware(source.link_created_at)
            if source.link_created_at_time_basis == "UTC"
            else source.link_created_at
        ),
        link_created_at_time_basis=source.link_created_at_time_basis,
        captured_at=to_utc_aware(source.captured_at),
        captured_at_time_basis="UTC",
    )


def list_report_requirement_sources(
    session: Session,
    user: CurrentUser,
    run_id: str,
    query: ReportRequirementSourcePageQuery,
) -> ReportRequirementSourcePage:
    run, _ = _load_run_visible(session, user, run_id)
    case_filters: list[Any] = [CaseRun.run_id == run.id]
    if query.case_run_id is not None:
        case_filters.append(CaseRun.id == query.case_run_id)
    case_runs = list(
        session.scalars(
            select(CaseRun)
            .where(*case_filters)
            .order_by(CaseRun.sequence_no.asc(), CaseRun.id.asc())
        ).all()
    )
    case_run_ids = [item.id for item in case_runs]
    capture_by_case = _load_requirement_captures(session, case_run_ids)
    captures = [
        _report_requirement_capture(item, capture_by_case.get(item.id))
        for item in case_runs
    ]

    if not case_run_ids:
        total = 0
        rows: list[tuple[RunRequirementSource, RunRequirementCapture]] = []
    else:
        source_filter = RunRequirementCapture.case_run_id.in_(case_run_ids)
        total = int(
            session.scalar(
                select(func.count(RunRequirementSource.id))
                .join(
                    RunRequirementCapture,
                    RunRequirementCapture.id == RunRequirementSource.capture_id,
                )
                .where(source_filter)
            )
            or 0
        )
        rows = list(
            session.execute(
                select(RunRequirementSource, RunRequirementCapture)
                .join(
                    RunRequirementCapture,
                    RunRequirementCapture.id == RunRequirementSource.capture_id,
                )
                .join(CaseRun, CaseRun.id == RunRequirementCapture.case_run_id)
                .where(source_filter)
                .order_by(
                    CaseRun.sequence_no.asc(),
                    RunRequirementSource.sequence_no.asc(),
                    RunRequirementSource.id.asc(),
                )
                .offset((query.page - 1) * query.page_size)
                .limit(query.page_size)
            ).all()
        )
    has_more = query.page * query.page_size < total
    next_page = query.page + 1 if has_more else None
    return ReportRequirementSourcePage(
        run_id=run.id,
        case_run_id=query.case_run_id,
        captures=captures,
        items=[_report_requirement_source(source, capture) for source, capture in rows],
        total=total,
        page=query.page,
        page_size=query.page_size,
        has_more=has_more,
        next_page=next_page,
        continuation_path=(
            _continuation_path(
                run.id,
                "requirement-sources",
                page=next_page,
                page_size=query.page_size,
                extra={"case_run_id": query.case_run_id},
            )
            if next_page is not None
            else None
        ),
    )


def _report_step(step: StepRun) -> ReportStep:
    return ReportStep(
        id=step.id,
        case_run_id=step.case_run_id,
        sequence_no=step.sequence_no,
        node_id=redact_text(step.node_id),
        name=redact_text(step.step_name),
        type=redact_text(step.step_type),
        status=step.status,
        duration_ms=step.duration,
        retry_count=step.retry_count,
        started_at=to_utc_aware(step.started_at),
        ended_at=to_utc_aware(step.ended_at),
        error_type=redact_text(step.error_type) if step.error_type else None,
        error_message=redact_text(step.error_message) if step.error_message else None,
    )


def list_report_steps(
    session: Session,
    user: CurrentUser,
    run_id: str,
    query: ReportStepPageQuery,
) -> ReportStepPage:
    run, _ = _load_run_visible(session, user, run_id)
    filters: list[Any] = [CaseRun.run_id == run.id]
    if query.case_run_id is not None:
        filters.append(CaseRun.id == query.case_run_id)
    total = int(
        session.scalar(
            select(func.count(StepRun.id))
            .join(CaseRun, CaseRun.id == StepRun.case_run_id)
            .where(*filters)
        )
        or 0
    )
    steps = list(
        session.scalars(
            select(StepRun)
            .join(CaseRun, CaseRun.id == StepRun.case_run_id)
            .where(*filters)
            .order_by(
                CaseRun.sequence_no.asc(),
                StepRun.sequence_no.asc(),
                StepRun.id.asc(),
            )
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        ).all()
    )
    has_more = query.page * query.page_size < total
    next_page = query.page + 1 if has_more else None
    return ReportStepPage(
        run_id=run.id,
        case_run_id=query.case_run_id,
        items=[_report_step(item) for item in steps],
        total=total,
        page=query.page,
        page_size=query.page_size,
        has_more=has_more,
        next_page=next_page,
        continuation_path=(
            _continuation_path(
                run.id,
                "steps",
                page=next_page,
                page_size=query.page_size,
                extra={"case_run_id": query.case_run_id},
            )
            if next_page is not None
            else None
        ),
    )


def _report_evidence(artifact: EvidenceArtifact) -> ReportEvidence:
    public_metadata = evidence_service._public_metadata(artifact)
    return ReportEvidence(
        id=artifact.id,
        run_id=artifact.run_id,
        case_run_id=artifact.case_run_id,
        step_run_id=artifact.step_run_id,
        artifact_type=artifact.artifact_type,
        file_name=redact_text(artifact.file_name),
        mime=redact_text(artifact.mime),
        size=artifact.size,
        sha256=artifact.sha256,
        metadata=redact_value(public_metadata),
        created_at=to_utc_aware(artifact.created_at),
        download_path=f"/api/v1/evidence/{quote(artifact.id, safe='')}/download",
    )


def list_report_evidence(
    session: Session,
    user: CurrentUser,
    run_id: str,
    query: ReportEvidencePageQuery,
) -> ReportEvidencePage:
    run, _ = _load_run_visible(session, user, run_id)
    filters: list[Any] = [
        EvidenceArtifact.project_id == run.project_id,
        EvidenceArtifact.run_id == run.id,
    ]
    if query.case_run_id is not None:
        filters.append(EvidenceArtifact.case_run_id == query.case_run_id)
    if query.step_run_id is not None:
        filters.append(EvidenceArtifact.step_run_id == query.step_run_id)
    total = int(
        session.scalar(select(func.count(EvidenceArtifact.id)).where(*filters)) or 0
    )
    artifacts = list(
        session.scalars(
            select(EvidenceArtifact)
            .where(*filters)
            .order_by(EvidenceArtifact.created_at.asc(), EvidenceArtifact.id.asc())
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        ).all()
    )
    has_more = query.page * query.page_size < total
    next_page = query.page + 1 if has_more else None
    return ReportEvidencePage(
        run_id=run.id,
        case_run_id=query.case_run_id,
        step_run_id=query.step_run_id,
        items=[_report_evidence(item) for item in artifacts],
        total=total,
        page=query.page,
        page_size=query.page_size,
        has_more=has_more,
        next_page=next_page,
        continuation_path=(
            _continuation_path(
                run.id,
                "evidence",
                page=next_page,
                page_size=query.page_size,
                extra={
                    "case_run_id": query.case_run_id,
                    "step_run_id": query.step_run_id,
                },
            )
            if next_page is not None
            else None
        ),
    )


def _failure_analysis_preview(
    analysis: WebFailureAnalysis | None,
) -> ReportAiAuditPreview | None:
    if analysis is None:
        return None
    summary = {
        "actual_model": (
            redact_text(analysis.actual_model) if analysis.actual_model else None
        ),
        "prompt_version_id": analysis.prompt_version_id,
        "output_schema_id": analysis.output_schema_id,
        "fallback_used": analysis.fallback_used,
        "repair_used": analysis.repair_used,
        "confidence": (
            float(analysis.confidence) if analysis.confidence is not None else None
        ),
        "needs_human_review": analysis.needs_human_review,
        "result": redact_value(analysis.structured_result),
    }
    return ReportAiAuditPreview(
        id=analysis.id,
        kind="WEB_FAILURE_ANALYSIS",
        case_run_id=analysis.case_run_id,
        status=analysis.status,
        ai_call_id=analysis.ai_call_id,
        created_at=to_utc_aware(analysis.created_at),
        summary=summary,
    )


def _healing_preview(
    proposal: WebHealingProposal | None,
) -> ReportAiAuditPreview | None:
    if proposal is None:
        return None
    summary = redact_value(
        {
            "run_id": proposal.run_id,
            "web_case_id": proposal.web_case_id,
            "web_case_version_id": proposal.web_case_version_id,
            "node_id": proposal.node_id,
            "confidence": float(proposal.confidence),
            "reason": proposal.reason,
            "created_element_version_id": proposal.created_element_version_id,
            "created_web_case_version_id": proposal.created_web_case_version_id,
        }
    )
    return ReportAiAuditPreview(
        id=proposal.id,
        kind="WEB_HEALING_PROPOSAL",
        case_run_id=proposal.case_run_id,
        status=proposal.status,
        ai_call_id=proposal.ai_call_id,
        created_at=to_utc_aware(proposal.created_at),
        summary=summary,
    )


def _related_ai(
    session: Session, run: TestRun
) -> ReportRelatedAi:
    analysis_filters = (
        WebFailureAnalysis.project_id == run.project_id,
        WebFailureAnalysis.run_id == run.id,
    )
    analysis_total = int(
        session.scalar(
            select(func.count(WebFailureAnalysis.id)).where(*analysis_filters)
        )
        or 0
    )
    analysis = session.scalar(
        select(WebFailureAnalysis)
        .where(*analysis_filters)
        .order_by(WebFailureAnalysis.created_at.desc(), WebFailureAnalysis.id.desc())
        .limit(1)
    )
    healing_filters = (
        WebHealingProposal.project_id == run.project_id,
        WebHealingProposal.run_id == run.id,
    )
    healing_total = int(
        session.scalar(
            select(func.count(WebHealingProposal.id)).where(*healing_filters)
        )
        or 0
    )
    healing = session.scalar(
        select(WebHealingProposal)
        .where(*healing_filters)
        .order_by(WebHealingProposal.created_at.desc(), WebHealingProposal.id.desc())
        .limit(1)
    )
    encoded_run_id = quote(run.id, safe="")
    return ReportRelatedAi(
        failure_analyses=ReportAiAuditGroup(
            total=analysis_total,
            latest=_failure_analysis_preview(analysis),
            has_more=analysis_total > 1,
            continuation_path=(
                f"/api/v1/runs/{encoded_run_id}/web-failure-analyses"
            ),
        ),
        healing_proposals=ReportAiAuditGroup(
            total=healing_total,
            latest=_healing_preview(healing),
            has_more=healing_total > 1,
            continuation_path=(
                f"/api/v1/runs/{encoded_run_id}/web-healing-proposals"
            ),
        ),
    )


def get_report_detail(
    session: Session,
    user: CurrentUser,
    run_id: str,
    *,
    observed_at: datetime | None = None,
) -> ReportDetailResponse:
    run, project = _load_run_visible(session, user, run_id)
    now = observed_at or utc_now_aware()
    context = _load_reference_context(session, [run])
    counts = _load_case_counts(session, [run.id])
    summary = _report_summary(run, project, context, counts, now)
    cases = list_report_cases(
        session,
        user,
        run.id,
        ReportPageQuery(page=1, page_size=DETAIL_CASE_PAGE_SIZE),
    )
    steps = list_report_steps(
        session,
        user,
        run.id,
        ReportStepPageQuery(page=1, page_size=DETAIL_STEP_PAGE_SIZE),
    )
    evidence = list_report_evidence(
        session,
        user,
        run.id,
        ReportEvidencePageQuery(page=1, page_size=DETAIL_EVIDENCE_PAGE_SIZE),
    )
    requirement_sources = list_report_requirement_sources(
        session,
        user,
        run.id,
        ReportRequirementSourcePageQuery(
            page=1, page_size=DETAIL_REQUIREMENT_SOURCE_PAGE_SIZE
        ),
    )
    return ReportDetailResponse(
        summary=summary,
        cases=cases,
        steps=steps,
        evidence=evidence,
        requirement_sources=requirement_sources,
        related_ai=_related_ai(session, run),
        pagination_note=(
            "cases、steps、evidence、requirement_sources 均为首屏有界分页；"
            "has_more=true 时使用 "
            "continuation_path 继续读取，不会静默截断"
        ),
    )
