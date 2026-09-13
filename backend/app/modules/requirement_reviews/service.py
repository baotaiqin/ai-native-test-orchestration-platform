import json
from collections.abc import Callable

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError, ResourceConflictError, ResourceNotFoundError
from app.core.logging import get_logger, redact_text
from app.core.time import utc_now_naive
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
from app.modules.requirement_reviews.models import RequirementReview
from app.modules.requirement_reviews.schemas import (
    RequirementReviewDecision,
    RequirementReviewEdit,
    RequirementReviewGenerate,
    RequirementReviewListResponse,
    RequirementReviewResponse,
    RequirementReviewResult,
    RequirementRevisionPlanResult,
    RequirementRevisionUnresolved,
    ReviewDecision,
    ReviewStatus,
)
from app.modules.requirements.models import (
    Requirement,
    RequirementDocumentVersion,
    RequirementVersion,
)

logger = get_logger(__name__)

REVISION_PROMPT_CODE = "PLATFORM_REQUIREMENT_REVISION_PLAN"
REVISION_SCHEMA_NAME = "RequirementRevisionPlanResult"
REVISION_PLAN_TIMEOUT_SECONDS = 300


def _strict_schema_contract(value: object) -> None:
    if isinstance(value, dict):
        properties = value.get("properties")
        if value.get("type") == "object" and isinstance(properties, dict):
            value["required"] = list(properties)
        for child in value.values():
            _strict_schema_contract(child)
    elif isinstance(value, list):
        for child in value:
            _strict_schema_contract(child)


def _get_requirement(session: Session, requirement_id: int) -> Requirement:
    requirement = session.get(Requirement, requirement_id)
    if requirement is None:
        raise ResourceNotFoundError("需求不存在")
    return requirement


def _get_review(session: Session, review_id: int) -> RequirementReview:
    review = session.get(RequirementReview, review_id)
    if review is None:
        raise ResourceNotFoundError("需求评审不存在")
    return review


def _review_response(
    session: Session, review: RequirementReview, *, reused: bool = False
) -> RequirementReviewResponse:
    requirement = _get_requirement(session, review.requirement_id)
    version = session.get(RequirementVersion, review.requirement_version_id)
    if version is None:
        raise ResourceNotFoundError("需求评审关联的需求版本不存在")
    return RequirementReviewResponse.model_validate({
        **{
            column.name: getattr(review, column.name)
            for column in review.__table__.columns
        },
        "requirement_code": requirement.code,
        "requirement_title": requirement.title,
        "requirement_version_no": version.version_no,
        "reused": reused,
    })


def _ensure_writable(
    session: Session, user: CurrentUser, requirement: Requirement
) -> None:
    project = get_project(session, user, requirement.project_id)
    ensure_project_writable(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目不能生成或审核需求评审")


def _requirement_context(
    session: Session,
    requirement: Requirement,
    version: RequirementVersion,
    payload: RequirementReviewGenerate,
) -> dict:
    context: dict = {
        "requirement": {
            "id": requirement.id,
            "code": requirement.code,
            "title": requirement.title,
            "type": requirement.type,
            "version_no": version.version_no,
            "markdown_content": version.markdown_content,
        },
        "parent": None,
        "siblings": [],
        "descendants": [],
        "document_version": None,
    }
    if inspect(session.get_bind()).has_table(RequirementDocumentVersion.__tablename__):
        document_version = session.scalar(
            select(RequirementDocumentVersion)
            .where(RequirementDocumentVersion.project_id == requirement.project_id)
            .order_by(RequirementDocumentVersion.version_no.desc())
        )
        if document_version is not None:
            context["document_version"] = {
                "id": document_version.id,
                "version_no": document_version.version_no,
                "content_hash": document_version.content_hash,
            }
    if payload.include_parent and requirement.parent_id is not None:
        parent = session.get(Requirement, requirement.parent_id)
        if parent is not None:
            parent_version = session.get(RequirementVersion, parent.current_version_id)
            context["parent"] = {
                "code": parent.code,
                "title": parent.title,
                "markdown_content": parent_version.markdown_content if parent_version else "",
            }
    if payload.include_siblings:
        siblings = list(
            session.scalars(
                select(Requirement).where(
                    Requirement.project_id == requirement.project_id,
                    Requirement.parent_id == requirement.parent_id,
                    Requirement.id != requirement.id,
                )
            ).all()
        )
        for sibling in siblings:
            sibling_version = session.get(RequirementVersion, sibling.current_version_id)
            context["siblings"].append(
                {
                    "code": sibling.code,
                    "title": sibling.title,
                    "markdown_content": (
                        sibling_version.markdown_content if sibling_version else ""
                    ),
                }
            )
    requirements = list(session.scalars(
        select(Requirement)
        .where(
            Requirement.project_id == requirement.project_id,
            Requirement.status == "ACTIVE",
        )
        .order_by(Requirement.order_index, Requirement.id)
    ).all())
    by_parent: dict[int | None, list[Requirement]] = {}
    for item in requirements:
        by_parent.setdefault(item.parent_id, []).append(item)

    def append_descendants(parent_id: int) -> None:
        for child in by_parent.get(parent_id, []):
            child_version = session.get(RequirementVersion, child.current_version_id)
            if child_version is not None:
                context["descendants"].append({
                    "id": child.id,
                    "code": child.code,
                    "title": child.title,
                    "type": child.type,
                    "version_no": child_version.version_no,
                    "markdown_content": child_version.markdown_content,
                })
            append_descendants(child.id)

    append_descendants(requirement.id)
    return context


def _ensure_revision_prompt(session: Session, user: CurrentUser) -> PromptDefinition:
    prompt = session.scalar(
        select(PromptDefinition).where(PromptDefinition.code == REVISION_PROMPT_CODE)
    )
    if prompt is not None:
        return prompt
    schema = session.scalar(
        select(OutputSchema).where(
            OutputSchema.name == REVISION_SCHEMA_NAME,
            OutputSchema.version_no == 1,
        )
    )
    schema_json = RequirementRevisionPlanResult.model_json_schema()
    _strict_schema_contract(schema_json)
    if schema is None:
        schema = OutputSchema(
            name=REVISION_SCHEMA_NAME,
            version_no=1,
            description="平台内置的需求评审修订规划结构",
            schema_json=schema_json,
            enabled=True,
            created_by=user.id,
        )
        session.add(schema)
        session.flush()
    else:
        schema.schema_json = schema_json
        schema.enabled = True
    prompt = PromptDefinition(
        name="[平台内置] AI 评审修订规划",
        code=REVISION_PROMPT_CODE,
        task_type=AiTaskType.REQUIREMENT_REVIEW.value,
        description="自动把已确认的评审建议归属到需求，并规划内容修改、新增和删除",
        enabled=True,
        created_by=user.id,
    )
    session.add(prompt)
    session.flush()
    version = PromptVersion(
        prompt_id=prompt.id,
        version_no=1,
        system_prompt=(
            "你是资深需求分析师。请把每条评审建议准确归属到给定需求范围。"
            "内容补充或修正放入 content_revisions，并给出合并全部相关建议后的完整 Markdown；"
            "确实需要新增独立需求时放入 additions；确实属于冗余或应移除的旧需求时放入 deletions；"
            "无法可靠归属时放入 unresolved。不能编造范围外需求编号。"
            "每个建议编号必须且只能出现一次。"
        ),
        user_template=(
            "需求范围及原文：{{ requirement_scope }}\n"
            "待处理评审建议：{{ review_suggestions }}\n"
            "请返回结构化修订规划。新增需求的父级必须来自需求范围；"
            "删除含下级的需求时明确使用 SUBTREE 或 PROMOTE_CHILDREN。"
        ),
        output_schema_id=schema.id,
        change_note="平台内置初始版本",
        created_by=user.id,
    )
    session.add(version)
    session.flush()
    prompt.current_version_id = version.id
    return prompt


def _numbered_review_suggestions(result: dict) -> list[dict[str, str]]:
    groups = (
        ("清晰度问题", "clarity_issues"),
        ("歧义", "ambiguity"),
        ("缺失规则", "missing_rules"),
        ("异常场景缺口", "exception_gaps"),
        ("可测试性", "testability"),
        ("验收标准建议", "acceptance_criteria_suggestions"),
    )
    suggestions: list[dict[str, str]] = []
    for category, key in groups:
        values = result.get(key, [])
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, str) and value.strip():
                suggestions.append({
                    "id": f"S-{len(suggestions) + 1:02d}",
                    "category": category,
                    "content": value.strip(),
                })
    return suggestions


def create_revision_plan_task(
    session: Session, user: CurrentUser, review_id: int
) -> RequirementReviewResponse:
    review = _get_review(session, review_id)
    requirement = _get_requirement(session, review.requirement_id)
    _ensure_writable(session, user, requirement)
    if review.status != ReviewStatus.ACCEPTED.value:
        raise ResourceConflictError("只能为已确认的 AI 评审生成修订规划")
    context = dict(review.context_snapshot or {})
    current = context.get("revision_plan")
    if isinstance(current, dict) and current.get("status") in {
        "QUEUED", "RUNNING", "SUCCEEDED",
    }:
        return _review_response(session, review, reused=True)
    context["revision_plan"] = {
        "status": "QUEUED",
        "result": None,
        "ai_call_id": None,
        "error_message": None,
        "updated_at": utc_now_naive().isoformat(),
    }
    review.context_snapshot = context
    session.commit()
    session.refresh(review)
    return _review_response(session, review)


def process_revision_plan_task(
    session_factory: Callable[[], Session], review_id: int, user: CurrentUser
) -> None:
    with session_factory() as session:
        review = session.get(RequirementReview, review_id)
        if review is None:
            return
        context = dict(review.context_snapshot or {})
        plan_state = context.get("revision_plan")
        if not isinstance(plan_state, dict) or plan_state.get("status") not in {
            "QUEUED", "RUNNING",
        }:
            return
        context["revision_plan"] = {
            **plan_state,
            "status": "RUNNING",
            "updated_at": utc_now_naive().isoformat(),
        }
        review.context_snapshot = context
        session.commit()
        try:
            result = review.human_result or review.structured_result
            if not isinstance(result, dict):
                raise ResourceConflictError("评审没有可用于修订的结构化建议")
            suggestions = _numbered_review_suggestions(result)
            descendants = context.get("descendants", [])
            descendants = descendants if isinstance(descendants, list) else []
            scope_nodes = [context.get("requirement"), *descendants]
            scope_nodes = [item for item in scope_nodes if isinstance(item, dict)]
            allowed_codes = {
                item.get("code") for item in scope_nodes if isinstance(item.get("code"), str)
            }
            prompt = _ensure_revision_prompt(session, user)
            ai_result = generate(
                session,
                user,
                AiGenerateRequest(
                    project_id=review.project_id,
                    task_type=AiTaskType.REQUIREMENT_REVIEW,
                    prompt_id=prompt.id,
                    variables={
                        "requirement_scope": json.dumps(scope_nodes, ensure_ascii=False),
                        "review_suggestions": json.dumps(suggestions, ensure_ascii=False),
                    },
                    entity_type="REQUIREMENT_REVISION_PLAN",
                    entity_id=str(review.id),
                ),
                timeout_seconds=REVISION_PLAN_TIMEOUT_SECONDS,
            )
            plan = RequirementRevisionPlanResult.model_validate(ai_result.parsed_result)
            valid_suggestion_ids = {item["id"] for item in suggestions}
            unresolved = list(plan.unresolved)
            content_revisions = []
            additions = []
            deletions = []
            assigned: set[str] = set()

            def valid_ids(values: list[str]) -> list[str]:
                return [
                    value for value in values
                    if value in valid_suggestion_ids and value not in assigned
                ]

            for item in plan.content_revisions:
                item.suggestion_ids = valid_ids(item.suggestion_ids)
                if item.target_requirement_code in allowed_codes and item.suggestion_ids:
                    content_revisions.append(item)
                    assigned.update(item.suggestion_ids)
                elif item.suggestion_ids:
                    unresolved.append(RequirementRevisionUnresolved(
                        suggestion_ids=item.suggestion_ids,
                        reason=f"AI 返回的目标需求 {item.target_requirement_code} 不在评审范围内",
                    ))
            for item in plan.additions:
                item.suggestion_ids = valid_ids(item.suggestion_ids)
                if item.parent_requirement_code in allowed_codes and item.suggestion_ids:
                    if item.insert_after_requirement_code not in allowed_codes:
                        item.insert_after_requirement_code = ""
                    additions.append(item)
                    assigned.update(item.suggestion_ids)
                elif item.suggestion_ids:
                    unresolved.append(RequirementRevisionUnresolved(
                        suggestion_ids=item.suggestion_ids,
                        reason="AI 返回的新增需求父级不在评审范围内",
                    ))
            for item in plan.deletions:
                item.suggestion_ids = valid_ids(item.suggestion_ids)
                if item.target_requirement_code in allowed_codes and item.suggestion_ids:
                    deletions.append(item)
                    assigned.update(item.suggestion_ids)
                elif item.suggestion_ids:
                    unresolved.append(RequirementRevisionUnresolved(
                        suggestion_ids=item.suggestion_ids,
                        reason=f"AI 返回的删除目标 {item.target_requirement_code} 不在评审范围内",
                    ))
            for item in unresolved:
                item.suggestion_ids = valid_ids(item.suggestion_ids)
                assigned.update(item.suggestion_ids)
            missing = sorted(valid_suggestion_ids - assigned)
            if missing:
                unresolved.append(RequirementRevisionUnresolved(
                    suggestion_ids=missing,
                    reason="AI 未能可靠归属这些建议，请人工确认",
                ))
            safe_plan = RequirementRevisionPlanResult(
                content_revisions=content_revisions,
                additions=additions,
                deletions=deletions,
                unresolved=[item for item in unresolved if item.suggestion_ids],
            )
            context = dict(review.context_snapshot or {})
            context["revision_plan"] = {
                "status": "SUCCEEDED",
                "result": safe_plan.model_dump(),
                "suggestions": suggestions,
                "ai_call_id": ai_result.ai_call_id,
                "error_message": None,
                "updated_at": utc_now_naive().isoformat(),
            }
            review.context_snapshot = context
        except Exception as exc:
            logger.exception(
                "REQUIREMENT_REVISION_PLAN_TASK_FAILED",
                extra={"review_id": review_id, "error_type": type(exc).__name__},
            )
            session.rollback()
            review = session.get(RequirementReview, review_id)
            if review is None:
                return
            context = dict(review.context_snapshot or {})
            message = exc.message if isinstance(exc, AppError) else "AI 修订规划生成失败"
            context["revision_plan"] = {
                "status": "FAILED",
                "result": None,
                "ai_call_id": None,
                "error_message": redact_text(message)[:500],
                "updated_at": utc_now_naive().isoformat(),
            }
            review.context_snapshot = context
        session.commit()


def create_review_task(
    session: Session,
    user: CurrentUser,
    requirement_id: int,
    payload: RequirementReviewGenerate,
) -> RequirementReviewResponse:
    requirement = _get_requirement(session, requirement_id)
    _ensure_writable(session, user, requirement)
    version = session.get(RequirementVersion, requirement.current_version_id)
    if version is None:
        raise ResourceConflictError("需求没有当前版本")
    prompt = session.get(PromptDefinition, payload.prompt_id)
    if (
        prompt is None
        or not prompt.enabled
        or prompt.task_type != AiTaskType.REQUIREMENT_REVIEW.value
    ):
        raise ResourceConflictError("请选择可用的需求评审 Prompt")
    active = session.scalar(
        select(RequirementReview)
        .where(
            RequirementReview.requirement_id == requirement.id,
            RequirementReview.generation_status.in_(["QUEUED", "RUNNING"]),
        )
        .order_by(RequirementReview.id.desc())
    )
    if active is not None:
        if (
            active.requirement_version_id == version.id
            and active.prompt_id == payload.prompt_id
            and active.additional_instructions == payload.additional_instructions
        ):
            return _review_response(session, active, reused=True)
        raise ResourceConflictError("当前需求已有 AI 评审任务正在执行，请从评审记录查看")
    context = _requirement_context(session, requirement, version, payload)
    review = RequirementReview(
        project_id=requirement.project_id,
        requirement_id=requirement.id,
        requirement_version_id=version.id,
        prompt_id=payload.prompt_id,
        ai_call_id=None,
        generation_status="QUEUED",
        status=ReviewStatus.DRAFT.value,
        additional_instructions=payload.additional_instructions,
        context_snapshot=context,
        raw_response=None,
        structured_result=None,
        human_result=None,
        created_by=user.id,
    )
    session.add(review)
    session.commit()
    session.refresh(review)
    return _review_response(session, review)


def process_review_task(
    session_factory: Callable[[], Session], review_id: int, user: CurrentUser
) -> None:
    with session_factory() as session:
        review = session.get(RequirementReview, review_id)
        if review is None or review.generation_status not in {"QUEUED", "RUNNING"}:
            return
        review.generation_status = "RUNNING"
        review.started_at = utc_now_naive()
        session.commit()
        try:
            requirement = _get_requirement(session, review.requirement_id)
            version = session.get(RequirementVersion, review.requirement_version_id)
            if version is None:
                raise ResourceConflictError("需求评审关联的需求版本不存在")
            if review.prompt_id is None:
                raise ResourceConflictError("需求评审没有记录生成 Prompt")
            context = review.context_snapshot
            scope_payload = {
                "selected_requirement": context.get("requirement"),
                "descendants": context.get("descendants", []),
            }
            ai_result = generate(
                session,
                user,
                AiGenerateRequest(
                    project_id=requirement.project_id,
                    task_type=AiTaskType.REQUIREMENT_REVIEW,
                    prompt_id=review.prompt_id,
                    variables={
                        "requirement": json.dumps(scope_payload, ensure_ascii=False),
                        "requirement_title": requirement.title,
                        "requirement_code": requirement.code,
                        "parent_context": context.get("parent") or "无",
                        "sibling_context": context.get("siblings", []),
                        "child_context": context.get("descendants", []),
                        "additional_instructions": review.additional_instructions or "无",
                    },
                    entity_type="REQUIREMENT",
                    entity_id=str(requirement.id),
                ),
            )
            structured = RequirementReviewResult.model_validate(ai_result.parsed_result)
            call_log = session.get(AiCallLog, ai_result.ai_call_id)
            if call_log is None:
                raise ResourceConflictError("AI 调用记录不存在")
            review.ai_call_id = call_log.id
            review.raw_response = call_log.raw_response
            review.structured_result = structured.model_dump()
            review.generation_status = "SUCCEEDED"
            review.error_message = None
        except Exception as exc:
            logger.exception(
                "REQUIREMENT_REVIEW_TASK_FAILED",
                extra={"review_id": review_id, "error_type": type(exc).__name__},
            )
            session.rollback()
            review = session.get(RequirementReview, review_id)
            if review is None:
                return
            review.generation_status = "FAILED"
            message = exc.message if isinstance(exc, AppError) else "模型调用或输出校验失败"
            review.error_message = redact_text(message)[:500]
        review.completed_at = utc_now_naive()
        session.commit()


def list_reviews(
    session: Session, user: CurrentUser, requirement_id: int
) -> RequirementReviewListResponse:
    requirement = _get_requirement(session, requirement_id)
    get_project(session, user, requirement.project_id)
    items = list(
        session.scalars(
            select(RequirementReview)
            .where(RequirementReview.requirement_id == requirement_id)
            .order_by(RequirementReview.id.desc())
        ).all()
    )
    return RequirementReviewListResponse(
        items=[_review_response(session, item) for item in items],
        total=len(items),
        page=1,
        page_size=max(10, len(items)),
        active_count=sum(item.generation_status in {"QUEUED", "RUNNING"} for item in items),
    )


def get_review(
    session: Session, user: CurrentUser, review_id: int
) -> RequirementReviewResponse:
    review = _get_review(session, review_id)
    get_project(session, user, review.project_id)
    return _review_response(session, review)


def list_project_reviews(
    session: Session,
    user: CurrentUser,
    project_id: int,
    page: int = 1,
    page_size: int = 10,
) -> RequirementReviewListResponse:
    get_project(session, user, project_id)
    total = int(session.scalar(
        select(func.count(RequirementReview.id)).where(
            RequirementReview.project_id == project_id
        )
    ) or 0)
    active_count = int(session.scalar(
        select(func.count(RequirementReview.id)).where(
            RequirementReview.project_id == project_id,
            RequirementReview.generation_status.in_(["QUEUED", "RUNNING"]),
        )
    ) or 0)
    items = list(session.scalars(
        select(RequirementReview)
        .where(RequirementReview.project_id == project_id)
        .order_by(RequirementReview.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all())
    return RequirementReviewListResponse(
        items=[_review_response(session, item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
        active_count=active_count,
    )


def delete_review(
    session: Session,
    user: CurrentUser,
    review_id: int,
) -> None:
    review = _get_review(session, review_id)
    requirement = _get_requirement(session, review.requirement_id)
    _ensure_writable(session, user, requirement)
    if review.generation_status in {"QUEUED", "RUNNING"}:
        raise ResourceConflictError("正在生成的 AI 评审不能删除，请等待任务结束")
    session.delete(review)
    session.commit()


def edit_review(
    session: Session,
    user: CurrentUser,
    review_id: int,
    payload: RequirementReviewEdit,
) -> RequirementReviewResponse:
    review = _get_review(session, review_id)
    requirement = _get_requirement(session, review.requirement_id)
    _ensure_writable(session, user, requirement)
    if review.generation_status != "SUCCEEDED" or review.structured_result is None:
        raise ResourceConflictError("AI 评审尚未生成成功，不能编辑")
    if review.status != ReviewStatus.DRAFT.value:
        raise ResourceConflictError("已完成决策的评审不能再编辑")
    review.human_result = payload.human_result.model_dump()
    review.decision_note = payload.decision_note
    review.reviewed_by = user.id
    session.commit()
    session.refresh(review)
    return _review_response(session, review)


def decide_review(
    session: Session,
    user: CurrentUser,
    review_id: int,
    payload: RequirementReviewDecision,
) -> RequirementReviewResponse:
    review = _get_review(session, review_id)
    requirement = _get_requirement(session, review.requirement_id)
    _ensure_writable(session, user, requirement)
    if review.generation_status != "SUCCEEDED" or review.structured_result is None:
        raise ResourceConflictError("AI 评审尚未生成成功，不能决策")
    if review.status != ReviewStatus.DRAFT.value:
        raise ResourceConflictError("该评审已经完成决策")
    review.status = (
        ReviewStatus.ACCEPTED.value
        if payload.action == ReviewDecision.ACCEPT
        else ReviewStatus.REJECTED.value
    )
    review.decision_note = payload.decision_note or review.decision_note
    review.reviewed_by = user.id
    review.decided_at = utc_now_naive()
    session.commit()
    session.refresh(review)
    return _review_response(session, review)
