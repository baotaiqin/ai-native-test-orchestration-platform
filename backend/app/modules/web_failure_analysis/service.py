import hashlib
import json
import re
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    AppError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.modules.ai_gateway.schemas import AiGenerateRequest
from app.modules.ai_gateway.service import generate
from app.modules.auth.schemas import CurrentUser
from app.modules.evidence.models import EvidenceArtifact
from app.modules.model_center.schemas import AiTaskType
from app.modules.projects.service import ensure_project_writable, get_project
from app.modules.prompt_center.models import (
    AiCallLog,
    OutputSchema,
    PromptDefinition,
    PromptVersion,
)
from app.modules.runs.enums import RunStatus, RunType
from app.modules.runs.models import CaseRun, RunWebExecutionResult, StepRun, TestRun
from app.modules.runs.schemas import WebExecutionTrace
from app.modules.web_failure_analysis.models import WebFailureAnalysis
from app.modules.web_failure_analysis.schemas import (
    WebFailureAnalysisCreateRequest,
    WebFailureAnalysisListResponse,
    WebFailureAnalysisResponse,
    WebFailureAnalysisResult,
    WebFailureAnalysisStatus,
)

_MAX_SNAPSHOT_BYTES = 128 * 1024
_FAILURE_STATUSES = {RunStatus.FAILED.value, RunStatus.TIMEOUT.value}
_NODE_FAILURE_STATUSES = {"FAILED", "TIMEOUT"}
_URL = re.compile(r"https?://[^\s,;]+", re.IGNORECASE)
_CREDENTIAL = re.compile(
    r"(?i)(?:bearer\s+[^\s,;]+|(?:password|passwd|token|access[_-]?token|"
    r"refresh[_-]?token|authorization|cookie|secret|api[_-]?key|credential)"
    r"\s*[:=]\s*[^\s,;]+)"
)
_NODE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def _is_safe_generated_result(
    value: Any, *, allowed_failure_node_ids: frozenset[str]
) -> bool:
    try:
        parsed = WebFailureAnalysisResult.model_validate(value)
    except (ValidationError, TypeError, ValueError):
        return False
    return set(parsed.evidence_node_ids).issubset(allowed_failure_node_ids)


def _prompt_additional_instructions(
    user_instructions: str | None, *, allowed_failure_node_ids: frozenset[str]
) -> str:
    return json.dumps(
        {
            "service_constraints": {
                "evidence_node_ids": {
                    "allowed_values": sorted(allowed_failure_node_ids),
                    "must_be_unique": True,
                    "pattern": "^[A-Za-z][A-Za-z0-9_-]*$",
                },
                "instruction": (
                    "Return only a result that satisfies every service constraint."
                ),
            },
            "untrusted_user_data": user_instructions or "无",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _conflict(message: str = "Web 失败分析不可用") -> ResourceConflictError:
    return ResourceConflictError(message)


def _safe_source_text(value: object | None, *, maximum: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = _URL.sub("<redacted>", text)
    text = _CREDENTIAL.sub("<redacted>", text)
    return text[:maximum]


def _duration_ms(start: datetime | None, end: datetime | None) -> int:
    if start is None or end is None:
        return 0
    return max(0, round((end - start).total_seconds() * 1000))


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _safe_trace(trace: WebExecutionTrace) -> dict[str, Any]:
    return {
        "node_id": trace.node_id,
        "status": trace.status,
        "duration_ms": trace.duration_ms,
        "error_type": _safe_source_text(trace.error_type, maximum=100),
        "error_message": _safe_source_text(trace.error_message, maximum=500),
        "locator_attempts": [
            {
                "strategy": attempt.strategy,
                "priority": attempt.priority,
                "status": attempt.status,
            }
            for attempt in trace.locator_attempts
        ],
    }


def _source_snapshot(
    session: Session,
    run: TestRun,
    case_run: CaseRun,
    result: RunWebExecutionResult,
    step_runs: list[StepRun],
) -> tuple[dict[str, Any], str, int, set[str]]:
    traces: list[dict[str, Any]] = []
    failure_node_ids: set[str] = set()
    seen_nodes: set[str] = set()
    if not isinstance(result.traces, list):
        raise _conflict("Web 执行 trace 不可用于失败分析")
    for item in result.traces:
        try:
            trace = WebExecutionTrace.model_validate(item)
        except (ValidationError, TypeError, ValueError) as exc:
            raise _conflict("Web 执行 trace 不可用于失败分析") from exc
        if trace.node_id in seen_nodes:
            raise _conflict("Web 执行 trace 节点不唯一")
        seen_nodes.add(trace.node_id)
        if trace.status in _NODE_FAILURE_STATUSES:
            failure_node_ids.add(trace.node_id)
        traces.append(_safe_trace(trace))

    if len(step_runs) > 300:
        raise _conflict("Web Case 节点数量超过失败分析限制")
    step_failure_node_ids: set[str] = set()
    for step in step_runs:
        if not _NODE_ID.fullmatch(step.node_id):
            raise _conflict("Web Case 节点标识不可用于失败分析")
        if step.status in _NODE_FAILURE_STATUSES:
            step_failure_node_ids.add(step.node_id)
    failure_node_ids.update(step_failure_node_ids)
    snapshot: dict[str, Any] = {
        "schema_version": 1,
        "run": {
            "id": run.id,
            "project_id": run.project_id,
            "run_type": run.run_type,
            "status": run.status,
            "duration_ms": _duration_ms(run.started_at, run.ended_at),
            "error_type": _safe_source_text(run.error_type, maximum=100),
            "error_message": _safe_source_text(run.error_message, maximum=1000),
        },
        "case_run": {
            "id": case_run.id,
            "status": case_run.status,
            "duration_ms": case_run.duration,
            "error_type": _safe_source_text(case_run.error_type, maximum=100),
            "error_message": _safe_source_text(case_run.error_message, maximum=1000),
        },
        "step_runs": [
            {
                "id": step.id,
                "node_id": step.node_id,
                "status": step.status,
                "duration_ms": step.duration,
                "error_type": _safe_source_text(step.error_type, maximum=100),
                "error_message": _safe_source_text(step.error_message, maximum=1000),
            }
            for step in step_runs
        ],
        "web_trace": {"status": result.status, "traces": traces},
        "evidence": [
            {
                "artifact_type": artifact.artifact_type,
                "size": artifact.size,
                "sha256": artifact.sha256,
                "step_run_id": artifact.step_run_id,
            }
            for artifact in session.scalars(
                select(EvidenceArtifact)
                .where(
                    EvidenceArtifact.run_id == run.id,
                    EvidenceArtifact.case_run_id == case_run.id,
                )
                .order_by(EvidenceArtifact.id.asc())
                .limit(100)
            ).all()
        ],
    }
    encoded = _canonical_bytes(snapshot)
    if len(encoded) > _MAX_SNAPSHOT_BYTES:
        raise _conflict("Web 失败分析来源快照超过大小限制")
    return snapshot, hashlib.sha256(encoded).hexdigest(), len(encoded), failure_node_ids


def _load_failure_context(
    session: Session, run_id: str, case_run_id: int
) -> tuple[
    TestRun,
    CaseRun,
    RunWebExecutionResult,
    list[StepRun],
    dict[str, Any],
    str,
    int,
    set[str],
]:
    run = session.get(TestRun, run_id)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    if run.run_type != RunType.WEB_CASE.value:
        raise _conflict("只有 WEB_CASE Run 可以生成失败分析")
    if run.status not in _FAILURE_STATUSES:
        raise _conflict("只有 FAILED/TIMEOUT Web Run 可以生成失败分析")
    case_run = session.scalar(
        select(CaseRun).where(
            CaseRun.id == case_run_id,
            CaseRun.run_id == run.id,
        )
    )
    if case_run is None:
        raise ResourceNotFoundError("CaseRun 不存在")
    if (
        case_run.status != run.status
        or case_run.web_case_id != run.web_case_id
        or case_run.web_case_version_id != run.web_case_version_id
    ):
        raise _conflict("Run 与 CaseRun 失败上下文不匹配")
    result = session.scalar(
        select(RunWebExecutionResult).where(
            RunWebExecutionResult.run_id == run.id,
            RunWebExecutionResult.case_run_id == case_run.id,
        )
    )
    if result is None or result.status not in _FAILURE_STATUSES or result.status != run.status:
        raise _conflict("缺少匹配的 Web 失败执行结果")
    if result.outcome != run.status:
        raise _conflict("Web 执行结果与 Run 状态不匹配")
    step_runs = list(
        session.scalars(
            select(StepRun)
            .where(StepRun.case_run_id == case_run.id)
            .order_by(StepRun.sequence_no.asc(), StepRun.id.asc())
        ).all()
    )
    snapshot, digest, size, failure_nodes = _source_snapshot(
        session, run, case_run, result, step_runs
    )
    return run, case_run, result, step_runs, snapshot, digest, size, failure_nodes


def _validate_prompt_context(
    session: Session, project_id: int, prompt_id: int
) -> tuple[int, int]:
    prompt = session.get(PromptDefinition, prompt_id)
    if prompt is None or not prompt.enabled:
        raise ResourceNotFoundError("Web 失败分析 Prompt 不存在或已停用")
    if prompt.task_type != AiTaskType.WEB_FAILURE_ANALYSIS.value:
        raise _conflict("Prompt 与 Web 失败分析任务类型不匹配")
    if prompt.current_version_id is None:
        raise _conflict("Web 失败分析 Prompt 没有当前版本")
    version = session.get(PromptVersion, prompt.current_version_id)
    if version is None or version.output_schema_id is None:
        raise _conflict("Web 失败分析 Prompt 必须绑定 Output Schema")
    schema = session.get(OutputSchema, version.output_schema_id)
    if schema is None or not schema.enabled:
        raise _conflict("Web 失败分析 Output Schema 不存在或已停用")
    if project_id <= 0:
        raise _conflict("Web 失败分析项目上下文无效")
    return version.id, schema.id


def _delete_placeholder(session: Session, analysis_id: int) -> None:
    try:
        analysis = session.get(WebFailureAnalysis, analysis_id)
        if analysis is not None:
            session.delete(analysis)
            session.commit()
    except Exception:
        session.rollback()


def _analysis_response(
    analysis: WebFailureAnalysis,
) -> WebFailureAnalysisResponse:
    parsed_result = None
    if analysis.structured_result:
        try:
            parsed_result = WebFailureAnalysisResult.model_validate(
                analysis.structured_result
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise _conflict("Web 失败分析结果不可读取") from exc
    return WebFailureAnalysisResponse(
        id=analysis.id,
        project_id=analysis.project_id,
        run_id=analysis.run_id,
        case_run_id=analysis.case_run_id,
        status=WebFailureAnalysisStatus(analysis.status),
        ai_call_id=analysis.ai_call_id,
        actual_model=analysis.actual_model,
        prompt_version_id=analysis.prompt_version_id,
        output_schema_id=analysis.output_schema_id,
        fallback_used=analysis.fallback_used,
        repair_used=analysis.repair_used,
        source_snapshot_sha256=analysis.source_snapshot_sha256,
        source_snapshot_size=analysis.source_snapshot_size,
        structured_result=parsed_result,
        created_by=analysis.created_by,
        created_at=analysis.created_at,
    )


def generate_failure_analysis(
    session: Session,
    user: CurrentUser,
    run_id: str,
    payload: WebFailureAnalysisCreateRequest,
) -> WebFailureAnalysisResponse:
    run = session.get(TestRun, run_id)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    project = get_project(session, user, run.project_id)
    ensure_project_writable(session, project, user)
    expected_prompt_version_id, expected_output_schema_id = _validate_prompt_context(
        session, run.project_id, payload.prompt_id
    )
    (
        run,
        case_run,
        _execution_result,
        _step_runs,
        snapshot,
        snapshot_hash,
        snapshot_size,
        failure_node_ids,
    ) = _load_failure_context(session, run_id, payload.case_run_id)
    allowed_failure_node_ids = frozenset(failure_node_ids)
    existing = session.scalar(
        select(WebFailureAnalysis)
        .where(
            WebFailureAnalysis.run_id == run.id,
            WebFailureAnalysis.case_run_id == case_run.id,
            WebFailureAnalysis.status == WebFailureAnalysisStatus.DRAFT.value,
        )
        .with_for_update()
    )
    if existing is not None:
        raise _conflict("该 Web Case 失败已有进行中的分析")
    placeholder = WebFailureAnalysis(
        project_id=run.project_id,
        run_id=run.id,
        case_run_id=case_run.id,
        prompt_version_id=expected_prompt_version_id,
        output_schema_id=expected_output_schema_id,
        status=WebFailureAnalysisStatus.DRAFT.value,
        draft_key="DRAFT",
        source_snapshot=snapshot,
        source_snapshot_sha256=snapshot_hash,
        source_snapshot_size=snapshot_size,
        additional_instructions=payload.additional_instructions,
        created_by=user.id,
    )
    session.add(placeholder)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise _conflict("该 Web Case 失败已有进行中的分析") from exc
    session.refresh(placeholder)

    try:
        ai_result = generate(
            session,
            user,
            AiGenerateRequest(
                project_id=run.project_id,
                task_type=AiTaskType.WEB_FAILURE_ANALYSIS,
                prompt_id=payload.prompt_id,
                variables={
                    "source_snapshot": json.dumps(
                        snapshot,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "additional_instructions": _prompt_additional_instructions(
                        payload.additional_instructions,
                        allowed_failure_node_ids=allowed_failure_node_ids,
                    ),
                },
                entity_type="WEB_FAILURE_ANALYSIS",
                entity_id=snapshot_hash,
            ),
            result_validator=lambda value: _is_safe_generated_result(
                value,
                allowed_failure_node_ids=allowed_failure_node_ids,
            ),
        )
        call = session.get(AiCallLog, ai_result.ai_call_id)
        if (
            not ai_result.success
            or call is None
            or call.project_id != run.project_id
            or not call.success
            or call.task_type != AiTaskType.WEB_FAILURE_ANALYSIS.value
            or call.entity_type != "WEB_FAILURE_ANALYSIS"
            or call.entity_id != snapshot_hash
            or call.prompt_version_id != expected_prompt_version_id
            or call.output_schema_id != expected_output_schema_id
        ):
            raise _conflict("AI Web 失败分析调用记录不可用")
        parsed = WebFailureAnalysisResult.model_validate(ai_result.parsed_result)
        if not set(parsed.evidence_node_ids).issubset(failure_node_ids):
            raise _conflict("AI Web 失败分析引用了无效节点")
    except (AppError, ValidationError, TypeError, ValueError) as exc:
        _delete_placeholder(session, placeholder.id)
        if isinstance(exc, ResourceConflictError):
            raise
        raise _conflict("AI Web 失败分析输出不符合安全结构") from exc
    except Exception as exc:
        _delete_placeholder(session, placeholder.id)
        raise _conflict("AI Web 失败分析生成失败") from exc

    locked = session.scalar(
        select(WebFailureAnalysis)
        .where(WebFailureAnalysis.id == placeholder.id)
        .with_for_update()
    )
    if locked is None or locked.status != WebFailureAnalysisStatus.DRAFT.value:
        raise _conflict("Web 失败分析已被其他操作处理")
    locked.ai_call_id = call.id
    locked.structured_result = parsed.model_dump(mode="json")
    locked.actual_model = call.actual_model
    locked.fallback_used = call.fallback_used
    locked.repair_used = call.repair_used
    locked.confidence = parsed.confidence
    locked.needs_human_review = parsed.needs_human_review
    locked.status = WebFailureAnalysisStatus.COMPLETED.value
    locked.draft_key = None
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        _delete_placeholder(session, placeholder.id)
        raise _conflict("Web 失败分析保存冲突") from exc
    session.refresh(locked)
    return _analysis_response(locked)


def list_failure_analyses(
    session: Session,
    user: CurrentUser,
    run_id: str,
    case_run_id: int | None = None,
) -> WebFailureAnalysisListResponse:
    run = session.get(TestRun, run_id)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    get_project(session, user, run.project_id)
    statement = select(WebFailureAnalysis).where(
        WebFailureAnalysis.run_id == run.id,
        WebFailureAnalysis.project_id == run.project_id,
    )
    if case_run_id is not None:
        statement = statement.where(WebFailureAnalysis.case_run_id == case_run_id)
    analyses = list(
        session.scalars(
            statement.order_by(
                WebFailureAnalysis.created_at.desc(), WebFailureAnalysis.id.desc()
            )
        ).all()
    )
    return WebFailureAnalysisListResponse(
        items=[_analysis_response(item) for item in analyses],
        total=len(analyses),
    )
