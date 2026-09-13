import hashlib
import json

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import AppError, ResourceConflictError, ResourceNotFoundError
from app.core.time import utc_now_naive
from app.modules.ai_gateway.schemas import AiGenerateRequest
from app.modules.ai_gateway.service import generate
from app.modules.auth.schemas import CurrentUser
from app.modules.model_center.schemas import AiTaskType
from app.modules.projects.service import get_project
from app.modules.prompt_center.models import AiCallLog
from app.modules.web_cases.schemas import (
    AssertText,
    AssertUrl,
    AssertVisible,
    WebCaseContent,
)
from app.modules.web_recording_ai.models import WebRecordingAiSuggestion
from app.modules.web_recording_ai.schemas import (
    WebRecordingAiSuggestionAssertion,
    WebRecordingAiSuggestionCreateRequest,
    WebRecordingAiSuggestionListResponse,
    WebRecordingAiSuggestionResponse,
    WebRecordingAiSuggestionResult,
    WebRecordingAiSuggestionStatus,
)
from app.modules.web_recordings.models import WebRecording
from app.modules.web_recordings.schemas import (
    WebRecordingEvent,
    WebRecordingEventType,
    WebRecordingLocator,
    sanitize_public_url,
)
from app.modules.web_recordings.service import (
    _authorize_write,
    _get_recording,
    _validate_confirm_content,
)

_ACTION_BY_EVENT = {
    WebRecordingEventType.NAVIGATE: "GOTO",
    WebRecordingEventType.CLICK: "CLICK",
    WebRecordingEventType.FILL: "FILL",
    WebRecordingEventType.SELECT: "SELECT",
    WebRecordingEventType.PRESS: "PRESS",
}
_MAX_SNAPSHOT_BYTES = 1_000_000
_MAX_CANONICAL_TIMEOUT_MS = 900_000


def _safe_ai_conflict(message: str = "AI Web 建议生成失败") -> ResourceConflictError:
    return ResourceConflictError(message)


def _source_snapshot(
    recording: WebRecording,
) -> tuple[dict, str, int, dict[int, WebRecordingEvent]]:
    events: list[dict] = []
    indexed: dict[int, WebRecordingEvent] = {}
    for item in recording.events:
        try:
            event = WebRecordingEvent.model_validate(item)
        except ValidationError as exc:
            raise _safe_ai_conflict("录制事件已无法通过安全校验") from exc
        if event.sequence in indexed:
            raise _safe_ai_conflict("录制事件 sequence 不唯一")
        indexed[event.sequence] = event
        safe_event: dict = {
            "event_type": event.event_type.value,
            "sequence": event.sequence,
            "relative_time_ms": event.relative_time_ms,
            "page_url": sanitize_public_url(event.page_url),
            "title": event.title,
            "target_url": sanitize_public_url(event.target_url),
            "locator_candidates": [
                candidate.model_dump(mode="json") for candidate in event.locator_candidates
            ],
        }
        if event.value is not None:
            safe_event["value"] = event.value
        if event.key is not None:
            safe_event["key"] = event.key
        events.append({key: value for key, value in safe_event.items() if value is not None})
    snapshot = {
        "start_url": sanitize_public_url(recording.start_url) or recording.start_url,
        "events": events,
    }
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    encoded_bytes = encoded.encode("utf-8")
    if not events:
        raise _safe_ai_conflict("录制没有可整理的安全事件")
    if len(encoded_bytes) > _MAX_SNAPSHOT_BYTES:
        raise _safe_ai_conflict("录制安全事件快照超过大小限制")
    return snapshot, hashlib.sha256(encoded_bytes).hexdigest(), len(encoded_bytes), indexed


def _validate_selected_locator(
    selected: WebRecordingLocator | None, event: WebRecordingEvent
) -> dict:
    if not event.locator_candidates:
        raise _safe_ai_conflict("交互事件没有可用 Locator")
    candidate = event.locator_candidates[0]
    if selected is not None:
        candidate = next(
            (
                item
                for item in event.locator_candidates
                if item.strategy == selected.strategy and item.value == selected.value
            ),
            None,
        )
        if candidate is None:
            raise _safe_ai_conflict("AI 建议的 Locator 不是来源事件候选")
    candidate_data = candidate.model_dump(mode="json")
    return {
        "strategy": candidate_data["strategy"],
        "value": candidate_data["value"],
    }


def _assertion_matches_source(
    item: WebRecordingAiSuggestionAssertion,
    event: WebRecordingEvent,
    start_url: str,
) -> dict:
    assertion = item.assertion
    if isinstance(assertion, AssertVisible):
        if assertion.locator is None:
            raise _safe_ai_conflict("ASSERT_VISIBLE 缺少 Locator")
        _validate_selected_locator(
            WebRecordingLocator(
                strategy=assertion.locator.strategy,
                value=assertion.locator.value,
                priority=1,
            ),
            event,
        )
    elif isinstance(assertion, AssertText):
        if assertion.locator is None or event.title is None or assertion.expected != event.title:
            raise _safe_ai_conflict("ASSERT_TEXT 必须使用来源事件标题和 Locator")
        _validate_selected_locator(
            WebRecordingLocator(
                strategy=assertion.locator.strategy,
                value=assertion.locator.value,
                priority=1,
            ),
            event,
        )
    elif isinstance(assertion, AssertUrl):
        expected = sanitize_public_url(assertion.expected)
        source_urls = {
            sanitize_public_url(event.page_url),
            sanitize_public_url(event.target_url),
            start_url,
        }
        if expected is None or expected not in source_urls:
            raise _safe_ai_conflict("ASSERT_URL 必须使用来源事件 URL")
    else:
        raise _safe_ai_conflict("AI 建议包含不支持的断言")
    return assertion.model_dump(mode="json")


def _canonical_content(
    session: Session,
    recording: WebRecording,
    result: WebRecordingAiSuggestionResult,
    indexed: dict[int, WebRecordingEvent],
) -> WebCaseContent:
    start_url = sanitize_public_url(recording.start_url) or recording.start_url
    actions: list[dict] = []
    natural_language_steps: list[str] = []
    seen_sequences: set[int] = set()
    for step in result.steps:
        if step.source_event_sequence in seen_sequences:
            raise _safe_ai_conflict("AI 建议步骤引用重复事件")
        event = indexed.get(step.source_event_sequence)
        if event is None:
            raise _safe_ai_conflict("AI 建议步骤引用不存在的来源事件")
        expected_action = _ACTION_BY_EVENT[event.event_type]
        if step.action != expected_action:
            raise _safe_ai_conflict("AI 建议步骤类型与来源事件不匹配")
        seen_sequences.add(step.source_event_sequence)
        natural_language_steps.append(step.natural_language_step.strip())
        if event.event_type == WebRecordingEventType.NAVIGATE:
            target_url = sanitize_public_url(event.target_url)
            if target_url is None:
                raise _safe_ai_conflict("NAVIGATE 来源事件缺少安全 URL")
            actions.append({"type": "GOTO", "url": target_url})
        elif event.event_type == WebRecordingEventType.CLICK:
            actions.append(
                {"type": "CLICK", "locator": _validate_selected_locator(step.locator, event)}
            )
        elif event.event_type == WebRecordingEventType.FILL:
            if event.value is None:
                raise _safe_ai_conflict("FILL 来源事件缺少安全值")
            actions.append(
                {
                    "type": "FILL",
                    "locator": _validate_selected_locator(step.locator, event),
                    "value": event.value,
                }
            )
        elif event.event_type == WebRecordingEventType.SELECT:
            if event.value is None:
                raise _safe_ai_conflict("SELECT 来源事件缺少安全值")
            actions.append(
                {
                    "type": "SELECT",
                    "locator": _validate_selected_locator(step.locator, event),
                    "value": event.value,
                }
            )
        else:
            if event.key is None:
                raise _safe_ai_conflict("PRESS 来源事件缺少安全按键")
            actions.append(
                {
                    "type": "PRESS",
                    "locator": _validate_selected_locator(step.locator, event),
                    "key": event.key,
                }
            )

    assertions: list[dict] = []
    for item in result.assertions:
        event = indexed.get(item.source_event_sequence)
        if event is None:
            raise _safe_ai_conflict("AI 建议断言引用不存在的来源事件")
        assertions.append(_assertion_matches_source(item, event, start_url))

    profile_id = recording.saved_session_profile_id or recording.session_profile_id
    content = WebCaseContent.model_validate(
        {
            "start_url": start_url,
            "actions": actions,
            "natural_language_steps": natural_language_steps,
            "assertions": assertions,
            "session_profile_id": profile_id,
            "browser": "CHROME",
            "headless": False,
            "total_timeout_ms": _MAX_CANONICAL_TIMEOUT_MS,
            "parameters": {},
        }
    )
    _validate_confirm_content(session, recording, content)
    return content


def _delete_placeholder(session: Session, suggestion_id: int) -> None:
    placeholder = session.get(WebRecordingAiSuggestion, suggestion_id)
    if placeholder is not None:
        session.delete(placeholder)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()


def _call_metadata(
    session: Session, ai_call_id: int | None
) -> tuple[int | None, str | None, int | None, int | None, bool | None, bool | None]:
    if ai_call_id is None:
        return None, None, None, None, None, None
    call = session.get(AiCallLog, ai_call_id)
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


def _suggestion_response(
    session: Session, suggestion: WebRecordingAiSuggestion, *, idempotent: bool = False
) -> WebRecordingAiSuggestionResponse:
    (
        ai_call_id,
        actual_model,
        prompt_version_id,
        output_schema_id,
        fallback_used,
        repair_used,
    ) = _call_metadata(session, suggestion.ai_call_id)
    try:
        structured = (
            WebRecordingAiSuggestionResult.model_validate(suggestion.structured_result)
            if suggestion.ai_call_id is not None
            else None
        )
    except ValidationError:
        structured = None
    try:
        canonical = (
            WebCaseContent.model_validate(suggestion.canonical_suggested_content)
            if suggestion.ai_call_id is not None
            else None
        )
    except ValidationError:
        canonical = None
    try:
        human = (
            WebCaseContent.model_validate(suggestion.human_content)
            if suggestion.human_content is not None
            else None
        )
    except ValidationError:
        human = None
    if canonical is not None:
        canonical = canonical.model_copy(update={"session_profile_id": None})
    if human is not None:
        human = human.model_copy(update={"session_profile_id": None})
    return WebRecordingAiSuggestionResponse(
        id=suggestion.id,
        recording_id=suggestion.recording_id,
        project_id=suggestion.project_id,
        status=WebRecordingAiSuggestionStatus(suggestion.status),
        ai_call_id=ai_call_id,
        actual_model=actual_model,
        prompt_version_id=prompt_version_id,
        output_schema_id=output_schema_id,
        fallback_used=fallback_used,
        repair_used=repair_used,
        source_snapshot_sha256=suggestion.source_snapshot_sha256,
        source_snapshot_size=suggestion.source_snapshot_size,
        additional_instructions=suggestion.additional_instructions,
        structured_result=structured,
        canonical_suggested_content=canonical,
        human_content=human,
        decision_note=suggestion.decision_note,
        confirmed_web_case_id=suggestion.confirmed_web_case_id,
        confirmed_web_case_version_id=suggestion.confirmed_web_case_version_id,
        created_by=suggestion.created_by,
        reviewed_by=suggestion.reviewed_by,
        created_at=suggestion.created_at,
        reviewed_at=suggestion.reviewed_at,
        idempotent=idempotent,
    )


def generate_ai_suggestion(
    session: Session,
    user: CurrentUser,
    recording_id: str,
    payload: WebRecordingAiSuggestionCreateRequest,
) -> WebRecordingAiSuggestionResponse:
    recording = _get_recording(session, recording_id, for_update=True)
    _authorize_write(session, user, recording)
    if recording.status != "COMPLETED":
        raise ResourceConflictError("只有 COMPLETED 录制可以生成 AI 建议")
    if recording.confirmed_web_case_id is not None:
        raise ResourceConflictError("录制已确认 Web Case，不能再次生成建议")
    snapshot, snapshot_hash, snapshot_size, indexed = _source_snapshot(recording)
    existing = session.scalar(
        select(WebRecordingAiSuggestion)
        .where(
            WebRecordingAiSuggestion.recording_id == recording.id,
            WebRecordingAiSuggestion.status == WebRecordingAiSuggestionStatus.DRAFT.value,
        )
        .with_for_update()
    )
    if existing is not None:
        raise ResourceConflictError("该录制已有待确认的 AI 建议")
    placeholder = WebRecordingAiSuggestion(
        project_id=recording.project_id,
        recording_id=recording.id,
        status=WebRecordingAiSuggestionStatus.DRAFT.value,
        draft_key="DRAFT",
        source_snapshot=snapshot,
        source_snapshot_sha256=snapshot_hash,
        source_snapshot_size=snapshot_size,
        additional_instructions=payload.additional_instructions,
        structured_result={},
        canonical_suggested_content={},
        created_by=user.id,
    )
    session.add(placeholder)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError("该录制已有待确认的 AI 建议") from exc
    session.refresh(placeholder)

    try:
        ai_result = generate(
            session,
            user,
            AiGenerateRequest(
                project_id=recording.project_id,
                task_type=AiTaskType.WEB_CASE_GENERATE,
                prompt_id=payload.prompt_id,
                variables={
                    "recording_events": json.dumps(
                        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ),
                    "additional_instructions": payload.additional_instructions or "无",
                },
                entity_type="WEB_RECORDING_AI_SUGGESTION",
                entity_id=str(recording.id),
            ),
        )
        if not ai_result.success:
            raise _safe_ai_conflict()
        call = session.get(AiCallLog, ai_result.ai_call_id)
        if (
            call is None
            or call.project_id != recording.project_id
            or not call.success
            or call.task_type != AiTaskType.WEB_CASE_GENERATE.value
            or call.entity_type != "WEB_RECORDING_AI_SUGGESTION"
            or call.entity_id != str(recording.id)
        ):
            raise _safe_ai_conflict("AI 调用记录不可用")
        result = WebRecordingAiSuggestionResult.model_validate(ai_result.parsed_result)
        canonical = _canonical_content(session, recording, result, indexed)
    except AppError as exc:
        _delete_placeholder(session, placeholder.id)
        raise _safe_ai_conflict() from exc
    except (ValidationError, TypeError, ValueError) as exc:
        _delete_placeholder(session, placeholder.id)
        raise _safe_ai_conflict("AI 输出不符合安全 Web 建议结构") from exc

    locked = session.scalar(
        select(WebRecordingAiSuggestion)
        .where(WebRecordingAiSuggestion.id == placeholder.id)
        .with_for_update()
    )
    if locked is None or locked.status != WebRecordingAiSuggestionStatus.DRAFT.value:
        raise ResourceConflictError("AI 建议已被其他操作处理")
    locked.ai_call_id = call.id
    locked.structured_result = result.model_dump(mode="json")
    locked.canonical_suggested_content = canonical.model_dump(mode="json")
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError("AI 建议保存冲突") from exc
    session.refresh(locked)
    return _suggestion_response(session, locked)


def list_ai_suggestions(
    session: Session, user: CurrentUser, recording_id: str
) -> WebRecordingAiSuggestionListResponse:
    recording = _get_recording(session, recording_id)
    get_project(session, user, recording.project_id)
    suggestions = list(
        session.scalars(
            select(WebRecordingAiSuggestion)
            .where(WebRecordingAiSuggestion.recording_id == recording.id)
            .order_by(WebRecordingAiSuggestion.id.desc())
        ).all()
    )
    return WebRecordingAiSuggestionListResponse(
        items=[_suggestion_response(session, item) for item in suggestions],
        total=len(suggestions),
    )


def reject_ai_suggestion(
    session: Session,
    user: CurrentUser,
    recording_id: str,
    suggestion_id: int,
    decision_note: str | None,
) -> WebRecordingAiSuggestionResponse:
    recording = _get_recording(session, recording_id, for_update=True)
    _authorize_write(session, user, recording)
    suggestion = session.scalar(
        select(WebRecordingAiSuggestion)
        .where(
            WebRecordingAiSuggestion.id == suggestion_id,
            WebRecordingAiSuggestion.recording_id == recording.id,
        )
        .with_for_update()
    )
    if suggestion is None:
        raise ResourceNotFoundError("AI Web 建议不存在")
    if suggestion.status == WebRecordingAiSuggestionStatus.REJECTED.value:
        return _suggestion_response(session, suggestion, idempotent=True)
    if suggestion.status != WebRecordingAiSuggestionStatus.DRAFT.value:
        raise ResourceConflictError("已接受的 AI 建议不能拒绝")
    suggestion.status = WebRecordingAiSuggestionStatus.REJECTED.value
    suggestion.draft_key = None
    suggestion.decision_note = decision_note
    suggestion.reviewed_by = user.id
    suggestion.reviewed_at = utc_now_naive()
    session.commit()
    session.refresh(suggestion)
    return _suggestion_response(session, suggestion)


def get_suggestion_for_confirmation(
    session: Session,
    recording: WebRecording,
    suggestion_id: int,
) -> WebRecordingAiSuggestion:
    suggestion = session.scalar(
        select(WebRecordingAiSuggestion)
        .where(
            WebRecordingAiSuggestion.id == suggestion_id,
            WebRecordingAiSuggestion.recording_id == recording.id,
            WebRecordingAiSuggestion.project_id == recording.project_id,
        )
        .with_for_update()
    )
    if suggestion is None:
        raise ResourceNotFoundError("AI Web 建议不存在")
    if suggestion.status != WebRecordingAiSuggestionStatus.DRAFT.value:
        raise ResourceConflictError("AI Web 建议已被处理")
    if suggestion.ai_call_id is None:
        raise ResourceConflictError("AI Web 建议尚未生成完成")
    return suggestion
