import hashlib
import json
import re
from copy import deepcopy
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    AppError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.core.time import utc_now_naive
from app.infrastructure.object_store.client import (
    ObjectStore,
    ObjectStoreNotFoundError,
    ObjectStoreUnavailableError,
)
from app.infrastructure.rabbitmq.client import TaskPublisher
from app.infrastructure.redis.client import RedisRunEventStream, RedisRunnerHeartbeatStore
from app.modules.ai_gateway.schemas import AiGenerateRequest
from app.modules.ai_gateway.service import AiImageInput, generate
from app.modules.auth.schemas import CurrentUser
from app.modules.evidence.models import EvidenceArtifact
from app.modules.evidence.schemas import WebEvidenceArtifactType
from app.modules.model_center.schemas import AiTaskType
from app.modules.projects.service import ensure_project_writable, get_project
from app.modules.prompt_center.models import (
    AiCallLog,
    OutputSchema,
    PromptDefinition,
    PromptVersion,
)
from app.modules.runners.schemas import RunnerCapabilityName, RunnerSlotType
from app.modules.runs.enums import RunNodeStatus, RunStatus, RunTriggerType, RunType
from app.modules.runs.models import CaseRun, RunWebExecutionResult, TestRun
from app.modules.runs.schemas import RunCreateRequest, WebExecutionTrace, WebHealingContext
from app.modules.web_cases.models import (
    WebCase,
    WebCaseVersion,
    WebElement,
    WebElementLocator,
    WebElementVersion,
)
from app.modules.web_cases.schemas import WebCaseContent
from app.modules.web_healing.models import WebHealingProposal, WebHealingValidation
from app.modules.web_healing.schemas import (
    MAX_HEALING_CANDIDATE_LOCATORS,
    HealingAnalysisStage,
    HealingCandidateLocator,
    HealingLocator,
    HealingLocatorReference,
    HealingSourceLocator,
    HealingStructuredResult,
    WebHealingProposalCreateRequest,
    WebHealingProposalDecisionRequest,
    WebHealingProposalListResponse,
    WebHealingProposalRejectRequest,
    WebHealingProposalResponse,
    WebHealingProposalStatus,
    WebHealingProposalValidateRequest,
    WebHealingValidationResponse,
)

_MAX_SNAPSHOT_BYTES = 1_000_000
_NODE_ID = re.compile(r"^(action|assertion)_([1-9][0-9]*)$")
_SIMPLE_VALUE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,199}$")
_SENSITIVE = re.compile(
    r"(?i)(authorization|bearer|cookie|token|password|passwd|secret|credential|api[-_]?key)"
)
_ALLOWED_TRACE_ERRORS = {"WEB_LOCATOR_NOT_FOUND", "ASSERTION_FAILED"}
_NOT_FOUND = "NOT_FOUND"


def _conflict(message: str = "Web Locator Healing 提案不可用") -> ResourceConflictError:
    return ResourceConflictError(message)


def _safe_text(value: object, *, maximum: int) -> str:
    text = str(value).strip()
    if not text or "\r" in text or "\n" in text or _SENSITIVE.search(text):
        raise _conflict("Web Locator Healing 提案包含不安全文本")
    return text[:maximum]


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _context_audit(context: WebHealingContext) -> dict[str, Any]:
    return {
        "schema_version": context.schema_version,
        "trigger": context.trigger,
        "element_version_id": context.element_version_id,
        "page_url": context.page_url,
        "page_title": context.page_title,
        "dom_candidates": [
            {
                key: value
                for key, value in (
                    ("tag", candidate.tag),
                    ("role", candidate.role),
                    ("id", candidate.id),
                    ("name", candidate.name),
                    ("aria-label", candidate.aria_label),
                    ("placeholder", candidate.placeholder),
                    ("data-testid", candidate.data_testid),
                    ("type", candidate.type),
                    ("title", candidate.title),
                )
                if value is not None
            }
            for candidate in context.dom_candidates
        ],
    }


def _node_from_content(content: WebCaseContent, node_id: str) -> tuple[Any, Any, int]:
    match = _NODE_ID.fullmatch(node_id)
    if match is None:
        raise _conflict("node_id 不是可定位的 Web Action/Assertion")
    section, raw_index = match.groups()
    index = int(raw_index) - 1
    items = content.actions if section == "action" else content.assertions
    if index >= len(items):
        raise _conflict("node_id 不存在于固定 Web Case Version")
    node = items[index]
    locator = getattr(node, "locator", None)
    if locator is None:
        raise _conflict("目标 Web 节点没有可修复的 Locator")
    return node, locator, index


def _old_locator(
    locator: Any, element_locators: list[HealingSourceLocator] | None = None
) -> dict[str, Any]:
    if locator.element_version_id is not None:
        return {
            "element_version_id": locator.element_version_id,
            "locators": [
                item.model_dump(mode="json", exclude_none=True)
                for item in (element_locators or [])
            ],
        }
    return {"strategy": locator.strategy.value, "value": locator.value}


def _candidate_locators(context: WebHealingContext) -> list[tuple[int, HealingLocator]]:
    derived: list[tuple[int, HealingLocator]] = []
    seen: set[tuple[str, str, int]] = set()
    for index, candidate in enumerate(context.dom_candidates):
        options: list[tuple[str, str | None]] = []
        if candidate.id and _SIMPLE_VALUE.fullmatch(candidate.id):
            options.append(("css", f"#{candidate.id}"))
        if candidate.data_testid and _SIMPLE_VALUE.fullmatch(candidate.data_testid):
            options.append(("test_id", candidate.data_testid))
            options.append(("css", f"[data-testid='{candidate.data_testid}']"))
            options.append(("css", f'[data-testid="{candidate.data_testid}"]'))
        if candidate.placeholder:
            options.append(("placeholder", candidate.placeholder))
        if candidate.aria_label:
            options.append(("label", candidate.aria_label))
        if candidate.role:
            options.append(("role", candidate.role))
        if candidate.name and _SIMPLE_VALUE.fullmatch(candidate.name):
            options.append(("css", f"[name='{candidate.name}']"))
        if candidate.title and _SIMPLE_VALUE.fullmatch(candidate.title):
            options.append(("css", f"[title='{candidate.title}']"))
        for strategy, value in options:
            if value is None:
                continue
            locator = HealingLocator(strategy=strategy, value=value)
            key = (locator.strategy, locator.value, index)
            if key not in seen:
                if len(derived) >= MAX_HEALING_CANDIDATE_LOCATORS:
                    return derived
                derived.append((index, locator))
                seen.add(key)
    return derived


def _validate_candidate(
    candidate: HealingLocator,
    candidate_index: int,
    allowed: list[tuple[int, HealingLocator]],
) -> HealingLocator:
    for index, item in allowed:
        if index == candidate_index and item == candidate:
            return candidate
    raise _conflict("AI 建议的 Locator 不是安全来源候选")


def _validate_candidate_any(
    candidate: HealingLocator, allowed: list[tuple[int, HealingLocator]]
) -> HealingLocator:
    if any(item == candidate for _index, item in allowed):
        return candidate
    raise _conflict("人工选择的 Locator 不是安全来源候选")


def _candidate_similarity(old_locator: dict[str, Any], candidate: HealingLocator) -> float:
    source_values: list[str] = []
    direct = old_locator.get("value")
    if isinstance(direct, str):
        source_values.append(direct)
    for item in old_locator.get("locators", []):
        if isinstance(item, dict) and isinstance(item.get("value"), str):
            source_values.append(item["value"])

    def normalized(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower())[:2000]

    target = normalized(candidate.value)
    scores = [
        SequenceMatcher(None, normalized(value), target, autojunk=False).ratio()
        for value in source_values
        if normalized(value) and target
    ]
    return round(max(scores, default=0.0), 4)


def _ranked_candidate_locators(
    context: WebHealingContext, old_locator: dict[str, Any]
) -> list[HealingCandidateLocator]:
    candidates = [
        HealingCandidateLocator(
            candidate_index=index,
            locator=locator,
            similarity_score=_candidate_similarity(old_locator, locator),
        )
        for index, locator in _candidate_locators(context)
    ]
    return sorted(
        candidates,
        key=lambda item: (
            -item.similarity_score,
            item.candidate_index,
            item.locator.strategy,
            item.locator.value,
        ),
    )


def _load_context(
    session: Session,
    run_id: str,
    case_run_id: int,
    node_id: str,
) -> tuple[
    TestRun,
    CaseRun,
    WebCase,
    WebCaseVersion,
    WebExecutionTrace,
    Any,
    WebHealingContext,
    list[HealingSourceLocator],
]:
    run = session.get(TestRun, run_id)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    if (
        run.run_type != RunType.WEB_CASE.value
        or run.web_case_id is None
        or run.web_case_version_id is None
        or run.status != RunStatus.FAILED.value
    ):
        raise _conflict("只有失败的 WEB_CASE Run 可以生成 Locator Healing 提案")
    case_run = session.scalar(
        select(CaseRun).where(CaseRun.id == case_run_id, CaseRun.run_id == run.id)
    )
    case = session.get(WebCase, run.web_case_id)
    version = session.get(WebCaseVersion, run.web_case_version_id)
    result = session.scalar(
        select(RunWebExecutionResult).where(
            RunWebExecutionResult.run_id == run.id,
            RunWebExecutionResult.case_run_id == case_run_id,
        )
    )
    if (
        case_run is None
        or case_run.status != RunNodeStatus.FAILED.value
        or case_run.web_case_version_id != run.web_case_version_id
        or case is None
        or case.project_id != run.project_id
        or version is None
        or version.web_case_id != case.id
        or result is None
        or result.status != RunStatus.FAILED.value
    ):
        raise _conflict("固定 Web Case 失败上下文不一致")
    try:
        content = WebCaseContent.model_validate(version.content)
        _, locator, _ = _node_from_content(content, node_id)
    except ValidationError as exc:
        raise _conflict("固定 Web Case Version 无法安全解析") from exc
    trace_data = next(
        (
            item
            for item in result.traces
            if isinstance(item, dict) and item.get("node_id") == node_id
        ),
        None,
    )
    if trace_data is None:
        raise _conflict("失败结果中不存在目标节点 trace")
    try:
        trace = WebExecutionTrace.model_validate(trace_data)
    except ValidationError as exc:
        raise _conflict("失败 trace 无法通过安全校验") from exc
    if (
        trace.status != "FAILED"
        or trace.error_type not in _ALLOWED_TRACE_ERRORS
        or not trace.locator_attempts
        or any(item.status != _NOT_FOUND for item in trace.locator_attempts)
        or trace.healing_context is None
    ):
        raise _conflict("失败 trace 不满足 Locator Healing 门禁")
    context = trace.healing_context
    if locator.element_version_id != context.element_version_id:
        raise _conflict("失败 trace 的 Element Version 与固定节点不一致")
    element_locators: list[HealingSourceLocator] = []
    if locator.element_version_id is not None:
        element_version = session.get(WebElementVersion, locator.element_version_id)
        element = session.get(WebElement, element_version.element_id) if element_version else None
        if (
            element_version is None
            or element is None
            or element.project_id != run.project_id
            or not element_version.locators
        ):
            raise _conflict("失败节点引用的 Element Version 不可用")
        try:
            element_locators = [
                HealingSourceLocator(
                    strategy=item.strategy,
                    value=item.value,
                    priority=item.priority,
                    source=item.source,
                )
                for item in sorted(
                    element_version.locators,
                    key=lambda item: (item.priority, item.id),
                )
            ]
        except (TypeError, ValueError, ValidationError) as exc:
            raise _conflict("失败节点的 Element Locator 不满足安全边界") from exc
    allowed = _candidate_locators(context)
    if not allowed:
        raise _conflict("失败 trace 没有可安全推导的 Locator 候选")
    return run, case_run, case, version, trace, locator, context, element_locators


def _source_snapshot(
    run: TestRun,
    case_run: CaseRun,
    case: WebCase,
    version: WebCaseVersion,
    node_id: str,
    node: Any,
    locator: Any,
    context: WebHealingContext,
    element_locators: list[HealingSourceLocator],
    analysis_stage: HealingAnalysisStage,
    screenshot: EvidenceArtifact | None,
) -> tuple[dict, str, int]:
    old_locator_snapshot = _old_locator(locator, element_locators)
    snapshot = {
        "schema_version": 1,
        "run_id": run.id,
        "project_id": run.project_id,
        "case_run_id": case_run.id,
        "web_case_id": case.id,
        "web_case_version_id": version.id,
        "node_id": node_id,
        "node_type": node.type,
        "old_locator": old_locator_snapshot,
        "healing_context": _context_audit(context),
        "candidate_locators": [
            item.model_dump(mode="json")
            for item in _ranked_candidate_locators(context, old_locator_snapshot)
        ],
        "analysis_stage": analysis_stage.value,
        "screenshot_evidence": (
            {
                "artifact_id": screenshot.id,
                "sha256": screenshot.sha256,
                "size": screenshot.size,
                "mime": screenshot.mime,
            }
            if screenshot is not None
            else None
        ),
    }
    encoded = _json_bytes(snapshot)
    if len(encoded) > _MAX_SNAPSHOT_BYTES:
        raise _conflict("Locator Healing 来源快照超过 1MB")
    digest = hashlib.sha256(encoded).hexdigest()
    return snapshot, digest, len(encoded)


def _delete_placeholder(session: Session, proposal_id: int) -> None:
    proposal = session.get(WebHealingProposal, proposal_id)
    if proposal is not None:
        session.delete(proposal)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()


def _call_metadata(
    session: Session, call_id: int | None
) -> tuple[int | None, str | None, int | None, int | None, bool | None, bool | None]:
    if call_id is None:
        return None, None, None, None, None, None
    call = session.get(AiCallLog, call_id)
    if call is None:
        return None, None, None, None, None, None
    return (
        call.id,
        call.actual_model,
        call.prompt_version_id,
        call.output_schema_id,
        call.fallback_used,
        call.repair_used,
    )


def _proposal_response(
    session: Session, proposal: WebHealingProposal, *, idempotent: bool = False
) -> WebHealingProposalResponse:
    metadata = _call_metadata(session, proposal.ai_call_id)
    proposed = (
        HealingLocator.model_validate(proposal.proposed_locator)
        if proposal.proposed_locator is not None
        else None
    )
    human = (
        HealingLocator.model_validate(proposal.human_locator)
        if proposal.human_locator is not None
        else None
    )
    if "element_version_id" in proposal.old_locator:
        old_locator = HealingLocatorReference.model_validate(proposal.old_locator)
    else:
        old_locator = HealingSourceLocator.model_validate(proposal.old_locator)
    context = WebHealingContext.model_validate(proposal.source_snapshot["healing_context"])
    candidate_locators = _ranked_candidate_locators(context, proposal.old_locator)
    validations = list(
        session.scalars(
            select(WebHealingValidation)
            .where(WebHealingValidation.proposal_id == proposal.id)
            .order_by(WebHealingValidation.id.desc())
        ).all()
    )
    latest_validation = None
    validated_locators: list[HealingLocator] = []
    seen_validated: set[tuple[str, str]] = set()
    for validation in validations:
        validation_run = session.get(TestRun, validation.run_id)
        if validation_run is None:
            raise _conflict("Healing 候选验证 Run 不存在")
        try:
            validation_locator = HealingLocator.model_validate(validation.locator)
        except (ValidationError, TypeError, ValueError) as exc:
            raise _conflict("Healing 候选验证记录无效") from exc
        if latest_validation is None:
            latest_validation = WebHealingValidationResponse(
                id=validation.id,
                run_id=validation_run.id,
                run_code=validation_run.run_code,
                run_status=validation_run.status,
                locator=validation_locator,
                created_by=validation.created_by,
                created_at=validation.created_at,
            )
        key = (validation_locator.strategy, validation_locator.value)
        if validation_run.status == RunStatus.SUCCESS.value and key not in seen_validated:
            validated_locators.append(validation_locator)
            seen_validated.add(key)
    return WebHealingProposalResponse(
        id=proposal.id,
        project_id=proposal.project_id,
        run_id=proposal.run_id,
        case_run_id=proposal.case_run_id,
        web_case_id=proposal.web_case_id,
        web_case_version_id=proposal.web_case_version_id,
        node_id=proposal.node_id,
        status=WebHealingProposalStatus(proposal.status),
        ai_call_id=metadata[0],
        analysis_stage=HealingAnalysisStage(proposal.analysis_stage),
        screenshot_artifact_id=proposal.screenshot_artifact_id,
        actual_model=metadata[1],
        prompt_version_id=metadata[2],
        output_schema_id=metadata[3],
        fallback_used=metadata[4],
        repair_used=metadata[5],
        source_snapshot_sha256=proposal.source_snapshot_sha256,
        source_snapshot_size=proposal.source_snapshot_size,
        old_locator=old_locator,
        proposed_locator=proposed,
        human_locator=human,
        candidate_locators=candidate_locators,
        confidence=float(proposal.confidence),
        reason=proposal.reason,
        evidence_candidate_index=proposal.evidence_candidate_index,
        decision_note=proposal.decision_note,
        created_element_version_id=proposal.created_element_version_id,
        created_web_case_version_id=proposal.created_web_case_version_id,
        latest_validation=latest_validation,
        validated_locators=validated_locators,
        created_by=proposal.created_by,
        reviewed_by=proposal.reviewed_by,
        created_at=proposal.created_at,
        reviewed_at=proposal.reviewed_at,
        idempotent=idempotent,
    )


def _validate_prompt_context(
    session: Session, project_id: int, prompt_id: int
) -> tuple[int, int]:
    prompt = session.get(PromptDefinition, prompt_id)
    if prompt is None or not prompt.enabled:
        raise ResourceNotFoundError("Locator Healing Prompt 不存在或已停用")
    if prompt.task_type != AiTaskType.LOCATOR_HEALING.value:
        raise _conflict("Prompt 与 Locator Healing 任务类型不匹配")
    if prompt.current_version_id is None:
        raise _conflict("Locator Healing Prompt 没有当前版本")
    prompt_version = session.get(PromptVersion, prompt.current_version_id)
    if prompt_version is None or prompt_version.output_schema_id is None:
        raise _conflict("Locator Healing Prompt 必须绑定 Output Schema")
    schema = session.get(OutputSchema, prompt_version.output_schema_id)
    if schema is None or not schema.enabled:
        raise _conflict("Locator Healing Output Schema 不存在或已停用")
    # Keep this explicit so a future prompt implementation cannot silently
    # cross project boundaries while the Gateway is being mocked in tests.
    if prompt.id <= 0 or project_id <= 0:
        raise _conflict("Locator Healing Prompt 上下文无效")
    return prompt_version.id, schema.id


def _ready_proposal_result(
    session: Session, proposal: WebHealingProposal
) -> HealingStructuredResult:
    """Require a fully materialized AI result before human review can mutate state."""
    if (
        proposal.ai_call_id is None
        or proposal.proposed_locator is None
        or not proposal.structured_result
        or not proposal.reason
        or proposal.reason == "待生成"
        or proposal.evidence_candidate_index is None
    ):
        raise _conflict("Healing 提案仍在生成中，请稍后重试")
    try:
        result = HealingStructuredResult.model_validate(proposal.structured_result)
        proposed = HealingLocator.model_validate(proposal.proposed_locator)
    except (ValidationError, TypeError, ValueError) as exc:
        raise _conflict("Healing 提案仍在生成中，请稍后重试") from exc
    if (
        result.locator != proposed
        or proposal.reason != result.reason
        or proposal.evidence_candidate_index != result.evidence_candidate_index
    ):
        raise _conflict("Healing 提案仍在生成中，请稍后重试")
    if session.get(AiCallLog, proposal.ai_call_id) is None:
        raise _conflict("Healing 提案仍在生成中，请稍后重试")
    return result


def generate_healing_proposal(
    session: Session,
    user: CurrentUser,
    run_id: str,
    payload: WebHealingProposalCreateRequest,
    evidence_store: ObjectStore,
) -> WebHealingProposalResponse:
    run = session.get(TestRun, run_id)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    project = get_project(session, user, run.project_id)
    ensure_project_writable(session, project, user)
    run, case_run, case, version, _, locator, context, element_locators = _load_context(
        session, run_id, payload.case_run_id, payload.node_id
    )
    expected_prompt_version_id, expected_output_schema_id = _validate_prompt_context(
        session, run.project_id, payload.prompt_id
    )
    content = WebCaseContent.model_validate(version.content)
    node, _, _ = _node_from_content(content, payload.node_id)
    screenshot: EvidenceArtifact | None = None
    image_input: AiImageInput | None = None
    if payload.screenshot_artifact_id is not None:
        screenshot = session.get(EvidenceArtifact, payload.screenshot_artifact_id)
        if (
            screenshot is None
            or screenshot.project_id != run.project_id
            or screenshot.run_id != run.id
            or screenshot.case_run_id != case_run.id
            or screenshot.artifact_type != WebEvidenceArtifactType.SCREENSHOT.value
            or screenshot.mime != "image/png"
            or not 0 < screenshot.size <= 5_000_000
        ):
            raise _conflict("截图 Evidence 不存在、归属不匹配或不是安全 PNG")
        try:
            screenshot_content = evidence_store.get_object(
                bucket=screenshot.minio_bucket, key=screenshot.minio_key
            )
        except ObjectStoreNotFoundError as exc:
            raise _conflict("截图 Evidence 对象不存在") from exc
        except ObjectStoreUnavailableError as exc:
            raise _conflict("截图 Evidence 暂时不可读取") from exc
        if (
            len(screenshot_content) != screenshot.size
            or hashlib.sha256(screenshot_content).hexdigest() != screenshot.sha256
        ):
            raise _conflict("截图 Evidence 完整性校验失败")
        image_input = AiImageInput(mime="image/png", content=screenshot_content)
    snapshot, digest, size = _source_snapshot(
        run,
        case_run,
        case,
        version,
        payload.node_id,
        node,
        locator,
        context,
        element_locators,
        payload.analysis_stage,
        screenshot,
    )
    existing = session.scalar(
        select(WebHealingProposal)
        .where(
            WebHealingProposal.run_id == run.id,
            WebHealingProposal.case_run_id == case_run.id,
            WebHealingProposal.node_id == payload.node_id,
            WebHealingProposal.status == WebHealingProposalStatus.DRAFT.value,
        )
        .with_for_update()
    )
    if existing is not None:
        raise _conflict("该失败节点已有待审 Healing 提案")
    placeholder = WebHealingProposal(
        project_id=run.project_id,
        run_id=run.id,
        case_run_id=case_run.id,
        web_case_id=case.id,
        web_case_version_id=version.id,
        node_id=payload.node_id,
        analysis_stage=payload.analysis_stage.value,
        screenshot_artifact_id=screenshot.id if screenshot is not None else None,
        status=WebHealingProposalStatus.DRAFT.value,
        draft_key="DRAFT",
        source_snapshot=snapshot,
        source_snapshot_sha256=digest,
        source_snapshot_size=size,
        additional_instructions=payload.additional_instructions,
        structured_result={},
        old_locator=_old_locator(locator, element_locators),
        confidence=Decimal("0"),
        reason="待生成",
        evidence_candidate_index=0,
        created_by=user.id,
    )
    session.add(placeholder)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise _conflict("该失败节点已有待审 Healing 提案") from exc
    session.refresh(placeholder)

    try:
        ai_payload = AiGenerateRequest(
            project_id=run.project_id,
            task_type=AiTaskType.LOCATOR_HEALING,
            prompt_id=payload.prompt_id,
            variables={
                "source_snapshot": json.dumps(
                    snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
                "additional_instructions": payload.additional_instructions or "无",
            },
            entity_type="WEB_HEALING_PROPOSAL",
            entity_id=digest,
        )
        ai_result = (
            generate(session, user, ai_payload, image_input=image_input)
            if image_input is not None
            else generate(session, user, ai_payload)
        )
        call = session.get(AiCallLog, ai_result.ai_call_id)
        if (
            call is None
            or call.project_id != run.project_id
            or not call.success
            or call.task_type != AiTaskType.LOCATOR_HEALING.value
            or call.entity_type != "WEB_HEALING_PROPOSAL"
            or call.entity_id != digest
            or call.prompt_version_id != expected_prompt_version_id
            or call.output_schema_id != expected_output_schema_id
        ):
            raise _conflict("AI 调用记录不可用")
        result = HealingStructuredResult.model_validate(ai_result.parsed_result)
        allowed = _candidate_locators(context)
        _validate_candidate(result.locator, result.evidence_candidate_index, allowed)
    except (AppError, ValidationError, TypeError, ValueError) as exc:
        _delete_placeholder(session, placeholder.id)
        if isinstance(exc, ResourceConflictError):
            raise
        raise _conflict("AI Locator Healing 输出不符合安全结构") from exc
    except Exception as exc:
        _delete_placeholder(session, placeholder.id)
        raise _conflict("AI Locator Healing 生成失败") from exc

    locked = session.scalar(
        select(WebHealingProposal)
        .where(WebHealingProposal.id == placeholder.id)
        .with_for_update()
    )
    if locked is None or locked.status != WebHealingProposalStatus.DRAFT.value:
        raise _conflict("Healing 提案已被其他操作处理")
    locked.ai_call_id = call.id
    locked.structured_result = result.model_dump(mode="json")
    locked.proposed_locator = result.locator.model_dump(mode="json")
    locked.confidence = Decimal(str(result.confidence))
    locked.reason = result.reason
    locked.evidence_candidate_index = result.evidence_candidate_index
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise _conflict("Healing 提案保存冲突") from exc
    session.refresh(locked)
    return _proposal_response(session, locked)


def list_healing_proposals(
    session: Session, user: CurrentUser, run_id: str, case_run_id: int | None = None
) -> WebHealingProposalListResponse:
    run = session.get(TestRun, run_id)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    get_project(session, user, run.project_id)
    statement = select(WebHealingProposal).where(
        WebHealingProposal.run_id == run.id,
        WebHealingProposal.project_id == run.project_id,
    )
    if case_run_id is not None:
        statement = statement.where(WebHealingProposal.case_run_id == case_run_id)
    proposals = list(session.scalars(statement.order_by(WebHealingProposal.id.desc())).all())
    return WebHealingProposalListResponse(
        items=[_proposal_response(session, item) for item in proposals],
        total=len(proposals),
    )


def _load_proposal_for_write(
    session: Session, user: CurrentUser, run_id: str, proposal_id: int
) -> WebHealingProposal:
    run = session.get(TestRun, run_id)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    project = get_project(session, user, run.project_id)
    ensure_project_writable(session, project, user)
    proposal = session.scalar(
        select(WebHealingProposal)
        .where(
            WebHealingProposal.id == proposal_id,
            WebHealingProposal.run_id == run.id,
            WebHealingProposal.project_id == run.project_id,
        )
        .with_for_update()
    )
    if proposal is None:
        raise ResourceNotFoundError("Web Locator Healing 提案不存在")
    return proposal


def reject_healing_proposal(
    session: Session,
    user: CurrentUser,
    run_id: str,
    proposal_id: int,
    payload: WebHealingProposalRejectRequest,
) -> WebHealingProposalResponse:
    proposal = _load_proposal_for_write(session, user, run_id, proposal_id)
    if proposal.status == WebHealingProposalStatus.REJECTED.value:
        return _proposal_response(session, proposal, idempotent=True)
    if proposal.status != WebHealingProposalStatus.DRAFT.value:
        raise _conflict("已接受的 Healing 提案不能拒绝")
    _ready_proposal_result(session, proposal)
    proposal.status = WebHealingProposalStatus.REJECTED.value
    proposal.draft_key = None
    proposal.decision_note = payload.decision_note
    proposal.reviewed_by = user.id
    proposal.reviewed_at = utc_now_naive()
    session.commit()
    session.refresh(proposal)
    return _proposal_response(session, proposal)


def validate_healing_proposal(
    session: Session,
    user: CurrentUser,
    run_id: str,
    proposal_id: int,
    payload: WebHealingProposalValidateRequest,
    store: RedisRunnerHeartbeatStore,
    publisher: TaskPublisher,
    event_stream: RedisRunEventStream,
) -> WebHealingProposalResponse:
    """Dispatch a normal Web Run with one temporary, non-persistent locator override."""

    proposal = _load_proposal_for_write(session, user, run_id, proposal_id)
    if proposal.status != WebHealingProposalStatus.DRAFT.value:
        raise _conflict("只有待审核的 Healing 提案可以验证候选")
    proposed = _ready_proposal_result(session, proposal)
    source_run, _, case, version, _, locator, context, element_locators = _load_context(
        session, run_id, proposal.case_run_id, proposal.node_id
    )
    if version.id != proposal.web_case_version_id or case.id != proposal.web_case_id:
        raise _conflict("Healing 提案来源版本已变化")
    content_model = WebCaseContent.model_validate(version.content)
    _, current_locator, _ = _node_from_content(content_model, proposal.node_id)
    if _old_locator(current_locator, element_locators) != proposal.old_locator:
        raise _conflict("固定 Web Case Locator 已变化，请重新生成提案")
    chosen = payload.locator or proposed.locator
    _validate_candidate_any(chosen, _candidate_locators(context))
    if (
        case.status != "APPROVED"
        or case.current_version_id != version.id
        or version.status != "APPROVED"
        or source_run.web_case_version_id != version.id
    ):
        raise _conflict("Web Case 当前版本已变化，请重新生成提案")

    existing = list(
        session.scalars(
            select(WebHealingValidation)
            .where(WebHealingValidation.proposal_id == proposal.id)
            .order_by(WebHealingValidation.id.desc())
        ).all()
    )
    for item in existing:
        validation_run = session.get(TestRun, item.run_id)
        if validation_run is None:
            raise _conflict("Healing 候选验证 Run 不存在")
        item_locator = HealingLocator.model_validate(item.locator)
        if validation_run.status in {
            RunStatus.CREATED.value,
            RunStatus.QUEUED.value,
            RunStatus.ASSIGNED.value,
            RunStatus.RUNNING.value,
            RunStatus.CANCELLING.value,
        }:
            raise _conflict("已有 Healing 候选验证 Run 正在执行")
        if validation_run.status == RunStatus.SUCCESS.value and item_locator == chosen:
            return _proposal_response(session, proposal, idempotent=True)

    # Import locally to keep the run router's healing sub-router free of an
    # import cycle while reusing the exact normal Run validation/dispatch path.
    from app.modules.runs.service import create_run, dispatch_run

    created = create_run(
        session,
        user,
        RunCreateRequest(
            project_id=source_run.project_id,
            environment_id=source_run.environment_id or 0,
            runner_id=source_run.runner_id or "",
            run_type=RunType.WEB_CASE,
            web_case_id=source_run.web_case_id,
            web_case_version_id=source_run.web_case_version_id,
            trigger_type=RunTriggerType.SYSTEM,
            required_capabilities=[
                RunnerCapabilityName(item) for item in source_run.required_capabilities
            ],
            required_tags=list(source_run.required_tags),
            required_slot_type=RunnerSlotType(source_run.required_slot_type),
            required_slot_count=source_run.required_slot_count,
            total_timeout_ms=source_run.total_timeout_ms,
        ),
        store,
        event_stream,
    )
    validation = WebHealingValidation(
        proposal_id=proposal.id,
        run_id=created.id,
        locator=chosen.model_dump(mode="json"),
        created_by=user.id,
    )
    session.add(validation)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise _conflict("Healing 候选验证记录保存冲突") from exc
    dispatch_run(session, user, created.id, store, publisher, event_stream)
    session.refresh(proposal)
    return _proposal_response(session, proposal)


def _replace_failed_node(
    content: dict[str, Any], node_id: str, chosen: HealingLocator, old_element_id: int | None,
    new_element_id: int | None,
) -> None:
    if old_element_id is not None and new_element_id is not None:
        for key in ("actions", "assertions"):
            for node in content.get(key, []):
                locator = node.get("locator") if isinstance(node, dict) else None
                if (
                    isinstance(locator, dict)
                    and locator.get("element_version_id") == old_element_id
                ):
                    locator["element_version_id"] = new_element_id
        return
    match = _NODE_ID.fullmatch(node_id)
    if match is None:
        raise _conflict("node_id 无效")
    key = "actions" if match.group(1) == "action" else "assertions"
    index = int(match.group(2)) - 1
    nodes = content.get(key, [])
    if index >= len(nodes) or not isinstance(nodes[index], dict):
        raise _conflict("node_id 不存在")
    nodes[index]["locator"] = chosen.model_dump(mode="json", exclude_none=True)


def _create_healed_element_version(
    session: Session, user: CurrentUser, run: TestRun, old_id: int, chosen: HealingLocator
) -> tuple[int, int]:
    old_version = session.scalar(
        select(WebElementVersion).where(WebElementVersion.id == old_id).with_for_update()
    )
    element = session.scalar(
        select(WebElement)
        .where(WebElement.id == old_version.element_id if old_version else False)
        .with_for_update()
    )
    if (
        old_version is None
        or element is None
        or element.project_id != run.project_id
        or element.current_version_id != old_version.id
        or element.status != "ACTIVE"
    ):
        raise _conflict("Element Version 已发生变化，请重新生成提案")
    latest = session.scalar(
        select(func.max(WebElementVersion.version_no)).where(
            WebElementVersion.element_id == element.id
        )
    ) or 0
    new_version = WebElementVersion(
        element_id=element.id,
        version_no=latest + 1,
        description=old_version.description,
        element_type=old_version.element_type,
        created_by=user.id,
    )
    session.add(new_version)
    session.flush()
    locators: list[tuple[str, str, str]] = [(chosen.strategy, chosen.value, "HEALED")]
    seen = {(chosen.strategy, chosen.value)}
    for item in sorted(old_version.locators, key=lambda value: (value.priority, value.id)):
        key = (item.strategy, item.value)
        if key in seen:
            continue
        seen.add(key)
        if len(locators) >= 20:
            break
        locators.append((item.strategy, item.value, item.source))
    new_version.locators = [
        WebElementLocator(
            strategy=strategy,
            value=value,
            priority=index,
            source=source,
        )
        for index, (strategy, value, source) in enumerate(locators, start=1)
    ]
    element.current_version_id = new_version.id
    return old_version.id, new_version.id


def accept_healing_proposal(
    session: Session,
    user: CurrentUser,
    run_id: str,
    proposal_id: int,
    payload: WebHealingProposalDecisionRequest,
) -> WebHealingProposalResponse:
    proposal = _load_proposal_for_write(session, user, run_id, proposal_id)
    if proposal.status == WebHealingProposalStatus.ACCEPTED.value:
        return _proposal_response(session, proposal, idempotent=True)
    if proposal.status != WebHealingProposalStatus.DRAFT.value:
        raise _conflict("已拒绝的 Healing 提案不能接受")
    # Serialize review with Run/version edits.  The proposal row itself is
    # locked by _load_proposal_for_write; these locks protect the immutable
    # source check and the new current-version pointer as one transaction.
    session.scalar(select(TestRun).where(TestRun.id == run_id).with_for_update())
    session.scalar(
        select(WebCase).where(WebCase.id == proposal.web_case_id).with_for_update()
    )
    session.scalar(
        select(WebCaseVersion)
        .where(WebCaseVersion.id == proposal.web_case_version_id)
        .with_for_update()
    )
    session.scalar(
        select(CaseRun).where(CaseRun.id == proposal.case_run_id).with_for_update()
    )
    proposed = _ready_proposal_result(session, proposal)
    run, case_run, case, version, _, locator, context, element_locators = _load_context(
        session, run_id, proposal.case_run_id, proposal.node_id
    )
    if version.id != proposal.web_case_version_id or case.id != proposal.web_case_id:
        raise _conflict("Healing 提案来源版本已变化")
    content_model = WebCaseContent.model_validate(version.content)
    node, current_locator, _ = _node_from_content(content_model, proposal.node_id)
    if _old_locator(current_locator, element_locators) != proposal.old_locator:
        raise _conflict("固定 Web Case Locator 已变化，请重新生成提案")
    allowed = _candidate_locators(context)
    chosen = payload.locator or proposed.locator
    _validate_candidate_any(chosen, allowed)
    validations = list(
        session.scalars(
            select(WebHealingValidation).where(
                WebHealingValidation.proposal_id == proposal.id
            )
        ).all()
    )
    if not any(
        session.get(TestRun, validation.run_id) is not None
        and session.get(TestRun, validation.run_id).status == RunStatus.SUCCESS.value
        and HealingLocator.model_validate(validation.locator) == chosen
        for validation in validations
    ):
        raise _conflict("所选 Locator 尚未通过临时 Web Run 验证")
    if (
        case.status != "APPROVED"
        or case.current_version_id != version.id
        or version.status != "APPROVED"
        or run.web_case_version_id != version.id
    ):
        raise _conflict("Web Case 当前版本已变化，请重新生成提案")
    old_element_id = locator.element_version_id
    new_element_id = None
    if old_element_id is not None:
        _, new_element_id = _create_healed_element_version(
            session, user, run, old_element_id, chosen
        )
    new_content = deepcopy(version.content)
    _replace_failed_node(new_content, proposal.node_id, chosen, old_element_id, new_element_id)
    try:
        validated_content = WebCaseContent.model_validate(new_content)
    except ValidationError as exc:
        raise _conflict("Healing 后 Web Case 内容无法通过安全校验") from exc
    latest = session.scalar(
        select(func.max(WebCaseVersion.version_no)).where(WebCaseVersion.web_case_id == case.id)
    ) or 0
    new_case_version = WebCaseVersion(
        web_case_id=case.id,
        version_no=latest + 1,
        content=validated_content.model_dump(mode="json", exclude_none=True),
        change_note="人工接受 Locator Healing 提案",
        status="DRAFT",
        created_by=user.id,
    )
    session.add(new_case_version)
    session.flush()
    case.current_version_id = new_case_version.id
    case.status = "DRAFT"
    proposal.status = WebHealingProposalStatus.ACCEPTED.value
    proposal.draft_key = None
    proposal.human_locator = chosen.model_dump(mode="json")
    proposal.decision_note = payload.decision_note
    proposal.created_element_version_id = new_element_id
    proposal.created_web_case_version_id = new_case_version.id
    proposal.reviewed_by = user.id
    proposal.reviewed_at = utc_now_naive()
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise _conflict("Healing 提案接受保存冲突") from exc
    session.refresh(proposal)
    return _proposal_response(session, proposal)
