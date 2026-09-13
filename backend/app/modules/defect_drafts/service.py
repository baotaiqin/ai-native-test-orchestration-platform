import hashlib
import json
import re
import string
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError, ResourceConflictError, ResourceNotFoundError
from app.core.redaction import redact_value
from app.modules.ai_gateway.schemas import AiGenerateRequest
from app.modules.ai_gateway.service import generate
from app.modules.auth.schemas import CurrentUser
from app.modules.model_center.schemas import AiTaskType
from app.modules.projects.schemas import ProjectStatus
from app.modules.projects.service import ensure_project_writable, get_project
from app.modules.prompt_center.models import (
    AiCallLog,
    OutputSchema,
    PromptDefinition,
    PromptVersion,
)
from app.modules.reports.service import get_report_detail
from app.modules.runs.enums import RunStatus
from app.modules.runs.models import CaseRun, TestRun

from .models import DefectDraft
from .schemas import (
    DefectDraftAiResult,
    DefectDraftContent,
    DefectDraftGenerateRequest,
    DefectDraftListResponse,
    DefectDraftResponse,
    DefectDraftUpdateRequest,
)

_MAX_SNAPSHOT_BYTES = 128 * 1024
_FAILURE_STATUSES = {RunStatus.FAILED.value, RunStatus.TIMEOUT.value}
_CASE_FAILURE_STATUSES = {"FAILED", "REVIEW", "TIMEOUT"}
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_MARKDOWN_ESCAPABLE = string.punctuation.replace("\\", "")


@dataclass(frozen=True)
class DefectDraftExport:
    content: bytes
    filename: str


def _conflict(message: str) -> ResourceConflictError:
    return ResourceConflictError(message)


def _validate_prompt(session: Session, prompt_id: int) -> tuple[int, int]:
    prompt = session.get(PromptDefinition, prompt_id)
    if prompt is None or not prompt.enabled:
        raise ResourceNotFoundError("缺陷草稿 Prompt 不存在或已停用")
    if prompt.task_type != AiTaskType.DEFECT_DRAFT.value:
        raise _conflict("Prompt 与缺陷草稿任务类型不匹配")
    version = session.get(PromptVersion, prompt.current_version_id)
    if version is None or version.output_schema_id is None:
        raise _conflict("缺陷草稿 Prompt 必须有当前版本并绑定 Output Schema")
    schema = session.get(OutputSchema, version.output_schema_id)
    if schema is None or not schema.enabled:
        raise _conflict("缺陷草稿 Output Schema 不存在或已停用")
    return version.id, schema.id


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _source_snapshot(
    session: Session,
    user: CurrentUser,
    run: TestRun,
    case_run_id: int | None,
) -> tuple[dict[str, Any], str, int]:
    detail = get_report_detail(session, user, run.id)
    selected_case: CaseRun | None = None
    if case_run_id is not None:
        selected_case = session.get(CaseRun, case_run_id)
        if selected_case is None or selected_case.run_id != run.id:
            raise ResourceNotFoundError("CaseRun 不存在")
        if selected_case.status not in _CASE_FAILURE_STATUSES:
            raise _conflict("只能为失败、超时或待复核 CaseRun 生成缺陷草稿")
    snapshot = redact_value(
        {
            "schema_version": 1,
            "summary": detail.summary.model_dump(mode="json"),
            "selected_case_run": (
                {
                    "id": selected_case.id,
                    "sequence_no": selected_case.sequence_no,
                    "status": selected_case.status,
                    "error_type": selected_case.error_type,
                    "error_message": selected_case.error_message,
                }
                if selected_case is not None
                else None
            ),
            "cases": [item.model_dump(mode="json") for item in detail.cases.items],
            "steps": [item.model_dump(mode="json") for item in detail.steps.items],
            "evidence": [
                {
                    "id": item.id,
                    "case_run_id": item.case_run_id,
                    "step_run_id": item.step_run_id,
                    "artifact_type": item.artifact_type,
                    "file_name": item.file_name,
                    "mime": item.mime,
                    "size": item.size,
                    "sha256": item.sha256,
                    "metadata": item.metadata,
                }
                for item in detail.evidence.items
            ],
            "snapshot_limits": {
                "cases": detail.cases.page_size,
                "steps": detail.steps.page_size,
                "evidence": detail.evidence.page_size,
            },
        }
    )
    if not isinstance(snapshot, dict):
        raise _conflict("缺陷草稿来源快照不可用")
    encoded = _canonical(snapshot)
    if len(encoded) > _MAX_SNAPSHOT_BYTES:
        raise _conflict("缺陷草稿来源快照超过大小限制")
    return snapshot, hashlib.sha256(encoded).hexdigest(), len(encoded)


def _safe_ai_result(value: Any) -> bool:
    try:
        parsed = DefectDraftAiResult.model_validate(value).model_dump(mode="json")
    except (ValidationError, TypeError, ValueError):
        return False
    return redact_value(parsed) == parsed


def _response(draft: DefectDraft) -> DefectDraftResponse:
    return DefectDraftResponse(
        id=draft.id,
        project_id=draft.project_id,
        run_id=draft.run_id,
        case_run_id=draft.case_run_id,
        prompt_version_id=draft.prompt_version_id,
        output_schema_id=draft.output_schema_id,
        ai_call_id=draft.ai_call_id,
        title=draft.title,
        module=draft.module,
        environment=draft.environment,
        preconditions=draft.preconditions,
        reproduction_steps=draft.reproduction_steps,
        expected_result=draft.expected_result,
        actual_result=draft.actual_result,
        evidence=draft.evidence,
        ai_analysis=draft.ai_analysis,
        source_snapshot_sha256=draft.source_snapshot_sha256,
        source_snapshot_size=draft.source_snapshot_size,
        actual_model=draft.actual_model,
        fallback_used=draft.fallback_used,
        repair_used=draft.repair_used,
        confidence=float(draft.confidence) if draft.confidence is not None else None,
        needs_human_review=draft.needs_human_review,
        revision=draft.revision,
        created_by=draft.created_by,
        updated_by=draft.updated_by,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )


def generate_defect_draft(
    session: Session,
    user: CurrentUser,
    run_id: str,
    payload: DefectDraftGenerateRequest,
) -> DefectDraftResponse:
    run = session.get(TestRun, run_id)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    project = get_project(session, user, run.project_id)
    ensure_project_writable(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise _conflict("归档项目不能生成缺陷草稿")
    if run.status not in _FAILURE_STATUSES:
        raise _conflict("只有失败或超时 Run 可以生成缺陷草稿")
    prompt_version_id, output_schema_id = _validate_prompt(session, payload.prompt_id)
    snapshot, snapshot_hash, snapshot_size = _source_snapshot(
        session, user, run, payload.case_run_id
    )
    try:
        generated = generate(
            session,
            user,
            AiGenerateRequest(
                project_id=run.project_id,
                task_type=AiTaskType.DEFECT_DRAFT,
                prompt_id=payload.prompt_id,
                variables={
                    "source_snapshot": json.dumps(
                        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ),
                    "additional_instructions": payload.additional_instructions or "无",
                },
                entity_type="DEFECT_DRAFT",
                entity_id=snapshot_hash,
            ),
            result_validator=_safe_ai_result,
        )
        parsed = DefectDraftAiResult.model_validate(generated.parsed_result)
        call = session.get(AiCallLog, generated.ai_call_id)
        if (
            call is None
            or not call.success
            or call.project_id != run.project_id
            or call.task_type != AiTaskType.DEFECT_DRAFT.value
            or call.entity_type != "DEFECT_DRAFT"
            or call.entity_id != snapshot_hash
            or call.prompt_version_id != prompt_version_id
            or call.output_schema_id != output_schema_id
        ):
            raise _conflict("AI 缺陷草稿调用记录不可用")
    except ResourceConflictError:
        raise
    except (AppError, ValidationError, TypeError, ValueError) as exc:
        raise _conflict("AI 缺陷草稿输出不符合安全结构") from exc

    content = DefectDraftContent.model_validate(
        parsed.model_dump(exclude={"confidence", "needs_human_review"})
    )
    draft = DefectDraft(
        project_id=run.project_id,
        run_id=run.id,
        case_run_id=payload.case_run_id,
        prompt_version_id=prompt_version_id,
        output_schema_id=output_schema_id,
        ai_call_id=call.id,
        **content.model_dump(mode="json"),
        source_snapshot_sha256=snapshot_hash,
        source_snapshot_size=snapshot_size,
        actual_model=call.actual_model,
        fallback_used=call.fallback_used,
        repair_used=call.repair_used,
        confidence=parsed.confidence,
        needs_human_review=parsed.needs_human_review,
        revision=1,
        created_by=user.id,
        updated_by=user.id,
    )
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return _response(draft)


def _visible_draft(session: Session, user: CurrentUser, draft_id: int) -> DefectDraft:
    draft = session.get(DefectDraft, draft_id)
    if draft is None:
        raise ResourceNotFoundError("缺陷草稿不存在")
    get_project(session, user, draft.project_id)
    return draft


def get_defect_draft(
    session: Session, user: CurrentUser, draft_id: int
) -> DefectDraftResponse:
    return _response(_visible_draft(session, user, draft_id))


def list_defect_drafts(
    session: Session,
    user: CurrentUser,
    project_id: int,
    *,
    run_id: str | None,
    page: int,
    page_size: int,
) -> DefectDraftListResponse:
    get_project(session, user, project_id)
    filters = [DefectDraft.project_id == project_id]
    if run_id is not None:
        filters.append(DefectDraft.run_id == run_id)
    total = int(session.scalar(select(func.count(DefectDraft.id)).where(*filters)) or 0)
    drafts = list(
        session.scalars(
            select(DefectDraft)
            .where(*filters)
            .order_by(DefectDraft.updated_at.desc(), DefectDraft.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return DefectDraftListResponse(
        items=[_response(item) for item in drafts],
        total=total,
        page=page,
        page_size=page_size,
        has_more=page * page_size < total,
    )


def update_defect_draft(
    session: Session,
    user: CurrentUser,
    draft_id: int,
    payload: DefectDraftUpdateRequest,
) -> DefectDraftResponse:
    draft = _visible_draft(session, user, draft_id)
    project = get_project(session, user, draft.project_id)
    ensure_project_writable(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise _conflict("归档项目不能编辑缺陷草稿")
    locked = session.scalar(
        select(DefectDraft).where(DefectDraft.id == draft.id).with_for_update()
    )
    if locked is None:
        raise ResourceNotFoundError("缺陷草稿不存在")
    if locked.revision != payload.revision:
        raise _conflict("缺陷草稿已被其他操作更新，请刷新后重试")
    changes = payload.model_dump(exclude_unset=True, exclude={"revision"})
    current = DefectDraftContent(
        title=locked.title,
        module=locked.module,
        environment=locked.environment,
        preconditions=locked.preconditions,
        reproduction_steps=locked.reproduction_steps,
        expected_result=locked.expected_result,
        actual_result=locked.actual_result,
        evidence=locked.evidence,
        ai_analysis=locked.ai_analysis,
    ).model_copy(update=changes)
    validated = DefectDraftContent.model_validate(current.model_dump())
    for field, value in validated.model_dump(mode="json").items():
        setattr(locked, field, value)
    locked.revision += 1
    locked.updated_by = user.id
    session.commit()
    session.refresh(locked)
    return _response(locked)


def _markdown(value: str) -> str:
    value = value.replace("\r", " ").replace("\n", " ")
    return "".join(f"\\{char}" if char in _MARKDOWN_ESCAPABLE else char for char in value)


def export_defect_draft_markdown(
    session: Session, user: CurrentUser, draft_id: int
) -> DefectDraftExport:
    draft = _visible_draft(session, user, draft_id)
    lines = [
        f"# {_markdown(draft.title)}",
        "",
        f"- 模块：{_markdown(draft.module)}",
        f"- 环境：{_markdown(draft.environment)}",
        f"- 关联 Run：{_markdown(draft.run_id)}",
        f"- 关联 CaseRun：{draft.case_run_id if draft.case_run_id is not None else '未指定'}",
        "- 状态：草稿（未提交到外部缺陷系统）",
        "",
        "## 前置条件",
        "",
        *([f"- {_markdown(item)}" for item in draft.preconditions] or ["无"]),
        "",
        "## 复现步骤",
        "",
        *[f"{index}. {_markdown(item)}" for index, item in enumerate(draft.reproduction_steps, 1)],
        "",
        "## 预期结果",
        "",
        _markdown(draft.expected_result),
        "",
        "## 实际结果",
        "",
        _markdown(draft.actual_result),
        "",
        "## 证据",
        "",
        *([f"- {_markdown(item)}" for item in draft.evidence] or ["无"]),
        "",
        "## AI 分析",
        "",
        _markdown(draft.ai_analysis),
        "",
    ]
    content = "\n".join(lines).encode("utf-8", errors="strict")
    safe_title = _UNSAFE_FILENAME.sub("-", draft.title).strip("-._")[:48] or "defect-draft"
    return DefectDraftExport(content=content, filename=f"{safe_title}-{draft.id}.md")
