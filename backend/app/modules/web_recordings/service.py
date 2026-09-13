import hashlib
import json
import re
from dataclasses import dataclass
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import (
    AuthenticationError,
    ResourceConflictError,
    ResourceNotFoundError,
    SecurityConfigurationError,
)
from app.core.secret_cipher import decrypt_secret, encrypt_secret
from app.core.time import to_utc_aware, utc_now_aware, utc_now_naive
from app.infrastructure.rabbitmq.client import TaskPublisher, TaskPublishResult
from app.infrastructure.redis.client import RedisRunnerHeartbeatStore
from app.modules.auth.schemas import CurrentUser
from app.modules.environments.models import Environment
from app.modules.projects.business_codes import (
    BusinessCodeNamespace,
    next_project_business_code,
)
from app.modules.projects.schemas import ProjectStatus
from app.modules.projects.service import ensure_project_writable, get_project
from app.modules.runners.models import Runner
from app.modules.runners.schemas import OnlineStatus, RunnerCapabilityStatus, RunnerStatus
from app.modules.runners.security import matches_digest
from app.modules.runners.service import get_runner_online_state
from app.modules.web_cases.models import (
    SessionProfile,
    WebCase,
    WebCaseVersion,
)
from app.modules.web_cases.schemas import WebCaseContent
from app.modules.web_cases.service import _validate_element_refs
from app.modules.web_design.models import WebTestPlan, WebTestPlanItem
from app.modules.web_recording_ai.models import WebRecordingAiSuggestion
from app.modules.web_recordings.models import WebRecording
from app.modules.web_recordings.schemas import (
    WebRecordingClaimResponse,
    WebRecordingCompleteRequest,
    WebRecordingCompleteResponse,
    WebRecordingConfirmRequest,
    WebRecordingConfirmResponse,
    WebRecordingControlResponse,
    WebRecordingCreateRequest,
    WebRecordingDetailResponse,
    WebRecordingDispatchResponse,
    WebRecordingDispatchStatus,
    WebRecordingEvent,
    WebRecordingEventPreview,
    WebRecordingExecutionPlanResponse,
    WebRecordingExecutionPlanSession,
    WebRecordingExecutionStartRequest,
    WebRecordingExecutionStartResponse,
    WebRecordingListItem,
    WebRecordingListResponse,
    WebRecordingLocator,
    WebRecordingOutcome,
    WebRecordingStatus,
    WebRecordingTaskEnvelopeV1,
    sanitize_public_url,
)

_MAX_EVENT_JSON_BYTES = 5_000_000
_MAX_RECORDING_ATTEMPTS = 10
_SENSITIVE_ERROR = re.compile(
    r"(?i)(?:bearer\s+[^\s,;]+|(?:password|passwd|token|access[_-]?token|secret|"
    r"credential|authorization|cookie|api[_-]?key)\s*[:=]\s*[^\s,;]+)"
)


def _safe_error(value: str | None, *, maximum: int = 1000) -> str | None:
    if value is None:
        return None
    return _SENSITIVE_ERROR.sub("<redacted>", value.strip())[:maximum] or None


def _commit(session: Session, message: str) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError(message) from exc


def _ensure_active_project(project: object) -> None:
    if getattr(project, "status", None) == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目不能创建或修改录制")


def _get_recording(
    session: Session, recording_id: str, *, for_update: bool = False
) -> WebRecording:
    statement = select(WebRecording).where(WebRecording.id == recording_id)
    if for_update:
        statement = statement.with_for_update()
    recording = session.scalar(statement)
    if recording is None:
        raise ResourceNotFoundError("录制不存在")
    return recording


def _authorize_write(session: Session, user: CurrentUser, recording: WebRecording) -> None:
    project = get_project(session, user, recording.project_id)
    ensure_project_writable(session, project, user)
    _ensure_active_project(project)


def _validate_runner_gate(
    session: Session,
    project_id: int,
    runner_id: str,
    heartbeat_store: RedisRunnerHeartbeatStore,
) -> Runner:
    runner = session.get(Runner, runner_id)
    if runner is None or runner.status != RunnerStatus.ACTIVE.value:
        raise ResourceConflictError("录制 Runner 不存在或未启用")
    online_status, _, redis_available = get_runner_online_state(runner, heartbeat_store)
    if not redis_available or online_status != OnlineStatus.ONLINE:
        raise ResourceConflictError("录制 Runner 当前不在线")
    if not any(
        item.capability == "WEB" and item.status == RunnerCapabilityStatus.READY.value
        for item in runner.capabilities
    ):
        raise ResourceConflictError("录制 Runner 缺少 READY 的 WEB Capability")
    web_slot = next((item for item in runner.slots if item.slot_type == "WEB"), None)
    if web_slot is None or web_slot.available < 1:
        raise ResourceConflictError("录制 Runner 没有可用 WEB Slot")
    if runner_id != runner.id or project_id <= 0:
        raise ResourceConflictError("录制 Runner 绑定无效")
    return runner


def _validate_environment_and_profile(
    session: Session,
    *,
    project_id: int,
    environment_id: int | None,
    session_profile_id: int | None,
) -> None:
    if environment_id is not None:
        environment = session.get(Environment, environment_id)
        if environment is None or environment.project_id != project_id or not environment.enabled:
            raise ResourceConflictError("录制 Environment 不存在、不属于当前项目或已停用")
    if session_profile_id is not None:
        profile = session.get(SessionProfile, session_profile_id)
        if (
            profile is None
            or profile.project_id != project_id
            or profile.environment_id not in {None, environment_id}
            or profile.status != "ACTIVE"
            or (profile.expires_at is not None and profile.expires_at <= utc_now_naive())
        ):
            raise ResourceConflictError("录制 Session Profile 不可用")


def _recording_item(recording: WebRecording) -> WebRecordingListItem:
    return WebRecordingListItem(
        id=recording.id,
        project_id=recording.project_id,
        runner_id=recording.runner_id,
        environment_id=recording.environment_id,
        session_profile_id=recording.session_profile_id,
        plan_item_id=recording.plan_item_id,
        saved_session_profile_id=recording.saved_session_profile_id,
        start_url=sanitize_public_url(recording.start_url) or recording.start_url,
        browser="CHROME",
        status=WebRecordingStatus(recording.status),
        dispatch_status=WebRecordingDispatchStatus(recording.dispatch_status),
        message_id=recording.message_id,
        event_count=recording.event_count,
        save_session=recording.save_session,
        confirmed_web_case_id=recording.confirmed_web_case_id,
        confirmed_web_case_version_id=recording.confirmed_web_case_version_id,
        created_by=recording.created_by,
        created_at=recording.created_at,
        updated_at=recording.updated_at,
    )


def _event_preview(item: object) -> WebRecordingEventPreview | None:
    try:
        event = (
            item if isinstance(item, WebRecordingEvent) else WebRecordingEvent.model_validate(item)
        )
    except ValidationError:
        return None
    return WebRecordingEventPreview(
        event_type=event.event_type,
        sequence=event.sequence,
        relative_time_ms=event.relative_time_ms,
        page_url=sanitize_public_url(event.page_url),
        title=event.title,
        target_url=sanitize_public_url(event.target_url),
        locator_candidates=[
            WebRecordingLocator.model_validate(candidate.model_dump())
            for candidate in event.locator_candidates
        ],
        value=event.value,
        key=event.key,
    )


def _recording_detail(session: Session, recording: WebRecording) -> WebRecordingDetailResponse:
    previews = [
        preview for item in recording.events if (preview := _event_preview(item)) is not None
    ]
    return WebRecordingDetailResponse(
        **_recording_item(recording).model_dump(),
        events=previews,
        dom_context_available=recording.dom_context_ciphertext is not None,
        stop_requested_at=recording.stop_requested_at,
        cancel_requested_at=recording.cancel_requested_at,
        started_at=recording.started_at,
        completed_at=recording.completed_at,
        error_type=recording.error_type,
        error_message=_safe_error(recording.error_message),
        confirmed_by=recording.confirmed_by,
        confirmed_at=recording.confirmed_at,
    )


def _transition(recording: WebRecording, target: WebRecordingStatus) -> bool:
    current = WebRecordingStatus(recording.status)
    if current == target:
        return False
    allowed = {
        WebRecordingStatus.CREATED: {
            WebRecordingStatus.QUEUED,
            WebRecordingStatus.CANCELLED,
            WebRecordingStatus.STOP_REQUESTED,
        },
        WebRecordingStatus.QUEUED: {
            WebRecordingStatus.RUNNING,
            WebRecordingStatus.CANCELLED,
            WebRecordingStatus.STOP_REQUESTED,
        },
        WebRecordingStatus.RUNNING: {
            WebRecordingStatus.STOP_REQUESTED,
            WebRecordingStatus.COMPLETED,
            WebRecordingStatus.FAILED,
        },
        WebRecordingStatus.STOP_REQUESTED: {
            WebRecordingStatus.COMPLETED,
            WebRecordingStatus.FAILED,
            WebRecordingStatus.CANCELLED,
        },
        WebRecordingStatus.COMPLETED: set(),
        WebRecordingStatus.FAILED: set(),
        WebRecordingStatus.CANCELLED: set(),
    }
    if target not in allowed[current]:
        raise ResourceConflictError(f"录制不允许从 {current.value} 转为 {target.value}")
    now = utc_now_naive()
    recording.status = target.value
    if target == WebRecordingStatus.RUNNING and recording.started_at is None:
        recording.started_at = now
    if target == WebRecordingStatus.STOP_REQUESTED and recording.stop_requested_at is None:
        recording.stop_requested_at = now
    if target in {
        WebRecordingStatus.COMPLETED,
        WebRecordingStatus.FAILED,
        WebRecordingStatus.CANCELLED,
    }:
        recording.completed_at = recording.completed_at or now
    return True


def create_recording(
    session: Session,
    user: CurrentUser,
    payload: WebRecordingCreateRequest,
    heartbeat_store: RedisRunnerHeartbeatStore,
) -> WebRecordingDetailResponse:
    project = get_project(session, user, payload.project_id)
    ensure_project_writable(session, project, user)
    _ensure_active_project(project)
    _validate_environment_and_profile(
        session,
        project_id=payload.project_id,
        environment_id=payload.environment_id,
        session_profile_id=payload.session_profile_id,
    )
    _validate_runner_gate(session, payload.project_id, payload.runner_id, heartbeat_store)
    if payload.plan_item_id is not None:
        plan_item = session.get(WebTestPlanItem, payload.plan_item_id)
        plan = session.get(WebTestPlan, plan_item.plan_id) if plan_item else None
        if plan_item is None or plan is None or plan.project_id != payload.project_id:
            raise ResourceConflictError("录制绑定的 Web 测试方案条目不属于当前项目")
    expires_at = to_utc_aware(payload.save_session_expires_at)
    if expires_at is not None and expires_at.replace(tzinfo=None) <= utc_now_naive():
        raise ResourceConflictError("保存 Session 的过期时间必须晚于当前时间")
    recording = WebRecording(
        id=uuid4().hex,
        project_id=payload.project_id,
        environment_id=payload.environment_id,
        runner_id=payload.runner_id,
        session_profile_id=payload.session_profile_id,
        plan_item_id=payload.plan_item_id,
        start_url=payload.start_url,
        browser="CHROME",
        status=WebRecordingStatus.CREATED.value,
        dispatch_status=WebRecordingDispatchStatus.PENDING.value,
        save_session=payload.save_session,
        save_session_name=payload.save_session_name,
        save_session_expires_at=expires_at.replace(tzinfo=None) if expires_at else None,
        events=[],
        created_by=user.id,
    )
    session.add(recording)
    _commit(session, "录制创建冲突")
    session.refresh(recording)
    return _recording_detail(session, recording)


def list_recordings(
    session: Session,
    user: CurrentUser,
    project_id: int,
    *,
    page: int,
    page_size: int,
    plan_item_id: int | None = None,
) -> WebRecordingListResponse:
    get_project(session, user, project_id)
    statement = select(WebRecording).where(WebRecording.project_id == project_id)
    count_statement = select(func.count(WebRecording.id)).where(
        WebRecording.project_id == project_id
    )
    if plan_item_id is not None:
        statement = statement.where(WebRecording.plan_item_id == plan_item_id)
        count_statement = count_statement.where(WebRecording.plan_item_id == plan_item_id)
    items = list(
        session.scalars(
            statement.order_by(WebRecording.created_at.desc(), WebRecording.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    total = session.scalar(count_statement)
    return WebRecordingListResponse(
        items=[_recording_item(item) for item in items],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


def get_recording_detail(
    session: Session, user: CurrentUser, recording_id: str
) -> WebRecordingDetailResponse:
    recording = _get_recording(session, recording_id)
    get_project(session, user, recording.project_id)
    return _recording_detail(session, recording)


def _dispatch_response(
    recording: WebRecording, *, published: bool, idempotent: bool
) -> WebRecordingDispatchResponse:
    if not recording.message_id:
        raise ResourceConflictError("录制任务消息尚未生成")
    return WebRecordingDispatchResponse(
        recording_id=recording.id,
        message_id=recording.message_id,
        runner_id=recording.runner_id,
        status=WebRecordingStatus(recording.status),
        dispatch_status=WebRecordingDispatchStatus(recording.dispatch_status),
        attempt_count=recording.attempt_count,
        published=published,
        idempotent=idempotent,
    )


def dispatch_recording(
    session: Session,
    user: CurrentUser,
    recording_id: str,
    heartbeat_store: RedisRunnerHeartbeatStore,
    publisher: TaskPublisher,
) -> WebRecordingDispatchResponse:
    recording = _get_recording(session, recording_id, for_update=True)
    _authorize_write(session, user, recording)
    current = WebRecordingStatus(recording.status)
    if (
        current == WebRecordingStatus.QUEUED
        and recording.dispatch_status == WebRecordingDispatchStatus.PUBLISHED.value
    ):
        return _dispatch_response(recording, published=True, idempotent=True)
    if current not in {WebRecordingStatus.CREATED, WebRecordingStatus.QUEUED}:
        raise ResourceConflictError("只有 CREATED/QUEUED 录制可以投递")
    if recording.attempt_count >= _MAX_RECORDING_ATTEMPTS:
        raise ResourceConflictError("录制任务投递重试次数已耗尽")
    _validate_runner_gate(session, recording.project_id, recording.runner_id, heartbeat_store)
    message_id = recording.message_id or uuid4().hex
    recording.message_id = message_id
    recording.routing_key = f"runner.{recording.runner_id}.web"
    recording.dispatch_status = WebRecordingDispatchStatus.PENDING.value
    recording.attempt_count += 1
    recording.last_error = None
    _transition(recording, WebRecordingStatus.QUEUED)
    session.commit()
    envelope = WebRecordingTaskEnvelopeV1(
        recording_id=recording.id,
        message_id=message_id,
        runner_id=recording.runner_id,
        project_id=recording.project_id,
        attempt=recording.attempt_count,
        enqueued_at=utc_now_aware(),
    ).model_dump(mode="json")
    try:
        result = publisher.publish(routing_key=recording.routing_key, payload=envelope)
    except Exception:
        result = TaskPublishResult(published=False, code="BROKER_UNAVAILABLE")
    locked = _get_recording(session, recording.id, for_update=True)
    if result.published:
        locked.dispatch_status = WebRecordingDispatchStatus.PUBLISHED.value
        locked.published_at = locked.published_at or utc_now_naive()
        locked.last_error = None
    else:
        locked.dispatch_status = WebRecordingDispatchStatus.FAILED.value
        locked.last_error = f"{result.code or 'BROKER_UNAVAILABLE'}: 录制任务发布未确认"
    _commit(session, "录制任务投递状态保存失败")
    return _dispatch_response(
        locked,
        published=result.published,
        idempotent=False,
    )


def _runner_for_recording(session: Session, recording: WebRecording, credential: str) -> Runner:
    runner = session.get(Runner, recording.runner_id)
    if (
        runner is None
        or runner.status != RunnerStatus.ACTIVE.value
        or not credential
        or len(credential) > 512
        or not matches_digest(credential, runner.credential_digest)
    ):
        raise AuthenticationError("Runner credential 无效")
    return runner


@dataclass
class _RecordingRunnerContext:
    recording: WebRecording
    runner: Runner


def _runner_context(
    session: Session,
    recording_id: str,
    credential: str,
    message_id: str,
    *,
    for_update: bool = True,
) -> _RecordingRunnerContext:
    recording = _get_recording(session, recording_id, for_update=for_update)
    runner = _runner_for_recording(session, recording, credential)
    if recording.message_id != message_id:
        raise ResourceConflictError("录制任务消息与录制不匹配")
    if recording.dispatch_status != WebRecordingDispatchStatus.PUBLISHED.value:
        raise ResourceConflictError("录制任务尚未被 RabbitMQ 确认发布")
    if recording.claimed_runner_id not in {None, runner.id}:
        raise ResourceConflictError("录制任务已被其他 Runner 认领")
    return _RecordingRunnerContext(recording=recording, runner=runner)


def claim_recording(
    session: Session,
    recording_id: str,
    credential: str,
    message_id: str,
) -> WebRecordingClaimResponse:
    context = _runner_context(session, recording_id, credential, message_id)
    recording = context.recording
    if recording.claimed_runner_id == context.runner.id and recording.claimed_at is not None:
        return WebRecordingClaimResponse(
            recording_id=recording.id,
            message_id=message_id,
            runner_id=context.runner.id,
            status=WebRecordingStatus(recording.status),
            claimed_at=(
                None
                if recording.status == WebRecordingStatus.CANCELLED.value
                else to_utc_aware(recording.claimed_at)
            ),
            idempotent=True,
        )
    current = WebRecordingStatus(recording.status)
    if current not in {
        WebRecordingStatus.QUEUED,
        WebRecordingStatus.STOP_REQUESTED,
        WebRecordingStatus.CANCELLED,
    }:
        raise ResourceConflictError("当前录制状态不能认领")
    recording.claimed_runner_id = context.runner.id
    recording.claimed_at = recording.claimed_at or utc_now_naive()
    session.commit()
    return WebRecordingClaimResponse(
        recording_id=recording.id,
        message_id=message_id,
        runner_id=context.runner.id,
        status=current,
        claimed_at=(
            None if current == WebRecordingStatus.CANCELLED else to_utc_aware(recording.claimed_at)
        ),
        idempotent=False,
    )


def _ensure_claimed(context: _RecordingRunnerContext) -> None:
    recording = context.recording
    if recording.claimed_runner_id != context.runner.id or recording.claimed_at is None:
        raise ResourceConflictError("录制尚未由当前 Runner 认领")


def _session_plan(
    session: Session, recording: WebRecording
) -> WebRecordingExecutionPlanSession | None:
    if recording.session_profile_id is None:
        return None
    profile = session.get(SessionProfile, recording.session_profile_id)
    if (
        profile is None
        or profile.project_id != recording.project_id
        or profile.environment_id not in {None, recording.environment_id}
        or profile.status != "ACTIVE"
        or (profile.expires_at is not None and profile.expires_at <= utc_now_naive())
    ):
        raise ResourceConflictError("录制 Session Profile 不可用")
    try:
        raw = decrypt_secret(profile.storage_state_ciphertext, get_settings().secret_key)
        state = json.loads(raw)
    except (SecurityConfigurationError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ResourceConflictError("录制 Session Profile 无法安全读取") from exc
    if not isinstance(state, dict):
        raise ResourceConflictError("录制 Session Profile Storage State 格式无效")
    return WebRecordingExecutionPlanSession(profile_id=profile.id, storage_state=state)


def get_recording_execution_plan(
    session: Session,
    recording_id: str,
    credential: str,
    message_id: str,
) -> WebRecordingExecutionPlanResponse:
    context = _runner_context(session, recording_id, credential, message_id)
    _ensure_claimed(context)
    current = WebRecordingStatus(context.recording.status)
    if current in {
        WebRecordingStatus.STOP_REQUESTED,
        WebRecordingStatus.COMPLETED,
        WebRecordingStatus.FAILED,
        WebRecordingStatus.CANCELLED,
    }:
        raise ResourceConflictError("终态录制不能再次读取执行计划")
    return WebRecordingExecutionPlanResponse(
        recording_id=context.recording.id,
        message_id=context.recording.message_id or message_id,
        runner_id=context.runner.id,
        project_id=context.recording.project_id,
        environment_id=context.recording.environment_id,
        start_url=context.recording.start_url,
        browser="CHROME",
        headless=False,
        session=_session_plan(session, context.recording),
        save_session=context.recording.save_session,
        save_session_name=context.recording.save_session_name,
        save_session_expires_at=context.recording.save_session_expires_at,
    )


def start_recording(
    session: Session,
    recording_id: str,
    credential: str,
    payload: WebRecordingExecutionStartRequest,
) -> WebRecordingExecutionStartResponse:
    context = _runner_context(session, recording_id, credential, payload.message_id)
    _ensure_claimed(context)
    recording = context.recording
    current = WebRecordingStatus(recording.status)
    if current == WebRecordingStatus.RUNNING:
        return WebRecordingExecutionStartResponse(
            recording_id=recording.id,
            message_id=payload.message_id,
            runner_id=context.runner.id,
            status=current,
            started_at=to_utc_aware(recording.started_at),
            idempotent=True,
        )
    if current == WebRecordingStatus.STOP_REQUESTED:
        return WebRecordingExecutionStartResponse(
            recording_id=recording.id,
            message_id=payload.message_id,
            runner_id=context.runner.id,
            status=current,
            started_at=to_utc_aware(recording.started_at),
            idempotent=True,
        )
    if current != WebRecordingStatus.QUEUED:
        raise ResourceConflictError("只有 QUEUED 录制可以开始")
    _transition(recording, WebRecordingStatus.RUNNING)
    session.commit()
    return WebRecordingExecutionStartResponse(
        recording_id=recording.id,
        message_id=payload.message_id,
        runner_id=context.runner.id,
        status=WebRecordingStatus.RUNNING,
        started_at=to_utc_aware(recording.started_at),
        idempotent=False,
    )


def get_recording_control(
    session: Session,
    recording_id: str,
    credential: str,
    message_id: str,
) -> WebRecordingControlResponse:
    context = _runner_context(session, recording_id, credential, message_id)
    _ensure_claimed(context)
    status = WebRecordingStatus(context.recording.status)
    return WebRecordingControlResponse(
        recording_id=context.recording.id,
        message_id=message_id,
        runner_id=context.runner.id,
        status=status,
        stop_requested=context.recording.stop_requested_at is not None,
        cancellation_requested=(
            context.recording.cancel_requested_at is not None
            or status == WebRecordingStatus.CANCELLED
        ),
    )


def _persist_saved_session(session: Session, recording: WebRecording, storage_state: dict) -> int:
    if not recording.save_session:
        raise ResourceConflictError("当前录制未启用保存 Session")
    if not recording.save_session_name:
        raise ResourceConflictError("录制保存 Session 名称无效")
    encoded = json.dumps(storage_state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 1_000_000:
        raise ResourceConflictError("Storage State 不能超过 1MB")
    profile = SessionProfile(
        project_id=recording.project_id,
        environment_id=recording.environment_id,
        name=recording.save_session_name,
        status="ACTIVE",
        storage_state_ciphertext=encrypt_secret(encoded, get_settings().secret_key),
        storage_state_fingerprint=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        profile_metadata={"source": "WEB_RECORDING", "recording_id": recording.id},
        expires_at=recording.save_session_expires_at,
        created_by=recording.created_by,
    )
    session.add(profile)
    session.flush()
    recording.saved_session_profile_id = profile.id
    return profile.id


def complete_recording(
    session: Session,
    recording_id: str,
    credential: str,
    payload: WebRecordingCompleteRequest,
) -> WebRecordingCompleteResponse:
    context = _runner_context(session, recording_id, credential, payload.message_id)
    _ensure_claimed(context)
    recording = context.recording
    current = WebRecordingStatus(recording.status)
    if current in {
        WebRecordingStatus.COMPLETED,
        WebRecordingStatus.FAILED,
        WebRecordingStatus.CANCELLED,
    }:
        return _complete_response(recording, context.runner.id, idempotent=True)
    if current not in {WebRecordingStatus.RUNNING, WebRecordingStatus.STOP_REQUESTED}:
        raise ResourceConflictError("只有 RUNNING/STOP_REQUESTED 录制可以完成")
    if payload.outcome == WebRecordingOutcome.CANCELLED and (
        current != WebRecordingStatus.STOP_REQUESTED or recording.cancel_requested_at is None
    ):
        raise ResourceConflictError("录制取消完成前必须先收到取消请求")
    if (
        recording.save_session
        and payload.outcome == WebRecordingOutcome.COMPLETED
        and recording.started_at is not None
    ):
        if payload.storage_state is None:
            raise ResourceConflictError("录制启用了保存 Session，但完成结果未提供 Storage State")
    elif payload.storage_state is not None:
        raise ResourceConflictError("当前录制未允许在此结果中保存 Session")
    audited_events = [event.model_dump(mode="json") for event in payload.events]
    event_json = json.dumps(audited_events, ensure_ascii=False, separators=(",", ":"))
    if len(event_json.encode("utf-8")) > _MAX_EVENT_JSON_BYTES:
        raise ResourceConflictError("录制事件总量超过限制")
    dom_ciphertext = None
    dom_fingerprint = None
    dom_size = 0
    if payload.dom_context is not None:
        encoded_dom = json.dumps(
            payload.dom_context, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        dom_bytes = encoded_dom.encode("utf-8")
        dom_size = len(dom_bytes)
        dom_ciphertext = encrypt_secret(encoded_dom, get_settings().secret_key)
        dom_fingerprint = hashlib.sha256(dom_bytes).hexdigest()
    if recording.save_session and payload.storage_state is not None:
        _persist_saved_session(session, recording, payload.storage_state)
    recording.events = audited_events
    recording.event_count = len(audited_events)
    recording.dom_context_ciphertext = dom_ciphertext
    recording.dom_context_fingerprint = dom_fingerprint
    recording.dom_context_size = dom_size
    recording.error_type = _safe_error(payload.error_type, maximum=100)
    recording.error_message = _safe_error(payload.error_message, maximum=1000)
    target = WebRecordingStatus(payload.outcome.value)
    _transition(recording, target)
    _commit(session, "录制完成结果保存冲突")
    return _complete_response(recording, context.runner.id, idempotent=False)


def _complete_response(
    recording: WebRecording, runner_id: str, *, idempotent: bool
) -> WebRecordingCompleteResponse:
    return WebRecordingCompleteResponse(
        recording_id=recording.id,
        message_id=recording.message_id or "",
        runner_id=runner_id,
        outcome=WebRecordingOutcome(recording.status),
        status=WebRecordingStatus(recording.status),
        event_count=recording.event_count,
        saved_session_profile_id=recording.saved_session_profile_id,
        completed_at=to_utc_aware(recording.completed_at),
        idempotent=idempotent,
    )


def stop_recording(
    session: Session, user: CurrentUser, recording_id: str
) -> WebRecordingDetailResponse:
    recording = _get_recording(session, recording_id, for_update=True)
    _authorize_write(session, user, recording)
    current = WebRecordingStatus(recording.status)
    if current == WebRecordingStatus.STOP_REQUESTED:
        return _recording_detail(session, recording)
    if current in {WebRecordingStatus.COMPLETED, WebRecordingStatus.FAILED}:
        raise ResourceConflictError("终态录制不能请求停止")
    if current == WebRecordingStatus.CANCELLED:
        return _recording_detail(session, recording)
    if current == WebRecordingStatus.CREATED:
        _transition(recording, WebRecordingStatus.CANCELLED)
        recording.error_type = "STOP_BEFORE_DISPATCH"
        recording.error_message = "录制尚未投递，已安全取消"
    elif current == WebRecordingStatus.QUEUED and (
        recording.dispatch_status != WebRecordingDispatchStatus.PUBLISHED.value
    ):
        _transition(recording, WebRecordingStatus.CANCELLED)
        recording.error_type = "STOP_BEFORE_RUNNER"
        recording.error_message = "录制任务尚未确认发布，已安全取消"
    else:
        _transition(recording, WebRecordingStatus.STOP_REQUESTED)
    _commit(session, "录制停止请求保存冲突")
    return _recording_detail(session, recording)


def cancel_recording(
    session: Session, user: CurrentUser, recording_id: str
) -> WebRecordingDetailResponse:
    recording = _get_recording(session, recording_id, for_update=True)
    _authorize_write(session, user, recording)
    current = WebRecordingStatus(recording.status)
    if current == WebRecordingStatus.CANCELLED:
        return _recording_detail(session, recording)
    if current == WebRecordingStatus.RUNNING:
        _transition(recording, WebRecordingStatus.STOP_REQUESTED)
        recording.cancel_requested_at = recording.cancel_requested_at or utc_now_naive()
    elif current in {
        WebRecordingStatus.CREATED,
        WebRecordingStatus.QUEUED,
        WebRecordingStatus.STOP_REQUESTED,
    }:
        _transition(recording, WebRecordingStatus.CANCELLED)
        recording.cancel_requested_at = recording.cancel_requested_at or utc_now_naive()
        recording.error_type = "CANCEL_REQUESTED"
        recording.error_message = "录制已取消"
    else:
        raise ResourceConflictError("终态录制不能取消")
    _commit(session, "录制取消请求保存冲突")
    return _recording_detail(session, recording)


def _validate_confirm_content(
    session: Session, recording: WebRecording, content: WebCaseContent
) -> None:
    _validate_element_refs(session, recording.project_id, content)
    if content.session_profile_id is None:
        return
    profile = session.get(SessionProfile, content.session_profile_id)
    if (
        profile is None
        or profile.project_id != recording.project_id
        or profile.environment_id not in {None, recording.environment_id}
        or profile.status != "ACTIVE"
        or (profile.expires_at is not None and profile.expires_at <= utc_now_naive())
    ):
        raise ResourceConflictError("确认内容引用的 Session Profile 不可用")


def confirm_recording(
    session: Session,
    user: CurrentUser,
    recording_id: str,
    payload: WebRecordingConfirmRequest,
) -> WebRecordingConfirmResponse:
    recording = _get_recording(session, recording_id, for_update=True)
    _authorize_write(session, user, recording)
    ai_suggestion = None
    confirmation_content = payload.content
    confirmation_name = payload.name
    if payload.ai_suggestion_id is not None:
        from app.modules.web_recording_ai.schemas import WebRecordingAiSuggestionResult
        from app.modules.web_recording_ai.service import get_suggestion_for_confirmation

        if recording.confirmed_web_case_id is not None:
            suggestion = session.get(WebRecordingAiSuggestion, payload.ai_suggestion_id)
            if (
                suggestion is not None
                and suggestion.recording_id == recording.id
                and suggestion.status == "ACCEPTED"
                and suggestion.confirmed_web_case_id == recording.confirmed_web_case_id
            ):
                return WebRecordingConfirmResponse(
                    recording_id=recording.id,
                    web_case_id=recording.confirmed_web_case_id,
                    web_case_version_id=recording.confirmed_web_case_version_id or 0,
                    status="DRAFT",
                    idempotent=True,
                )
            raise ResourceConflictError("录制已确认到其他 Web Case")
        ai_suggestion = get_suggestion_for_confirmation(
            session, recording, payload.ai_suggestion_id
        )
        try:
            canonical = WebCaseContent.model_validate(ai_suggestion.canonical_suggested_content)
        except ValidationError as exc:
            raise ResourceConflictError("AI Web 建议内容已无法安全读取") from exc
        confirmation_content = payload.content or canonical
        confirmation_name = confirmation_name or (
            WebRecordingAiSuggestionResult.model_validate(
                ai_suggestion.structured_result
            ).suggested_name
        )
    if recording.confirmed_web_case_id is not None:
        if payload.web_case_id not in {None, recording.confirmed_web_case_id}:
            raise ResourceConflictError("录制已确认到其他 Web Case")
        return WebRecordingConfirmResponse(
            recording_id=recording.id,
            web_case_id=recording.confirmed_web_case_id,
            web_case_version_id=recording.confirmed_web_case_version_id or 0,
            status="DRAFT",
            idempotent=True,
        )
    if recording.status != WebRecordingStatus.COMPLETED.value:
        raise ResourceConflictError("只有 COMPLETED 录制可以人工确认")
    if confirmation_content is None:
        raise ResourceConflictError("确认必须提供 Web Case 内容")
    _validate_confirm_content(session, recording, confirmation_content)
    if payload.web_case_id is None:
        case = WebCase(
            project_id=recording.project_id,
            code=next_project_business_code(
                session,
                project_id=recording.project_id,
                namespace=BusinessCodeNamespace.WEB_CASE,
                model=WebCase,
            ),
            name=(confirmation_name or "录制 Web Case").strip(),
            status="DRAFT",
            created_by=user.id,
        )
        session.add(case)
        session.flush()
        version_no = 1
    else:
        case = session.scalar(
            select(WebCase)
            .where(WebCase.id == payload.web_case_id, WebCase.project_id == recording.project_id)
            .with_for_update()
        )
        if case is None:
            raise ResourceNotFoundError("Web Case 不存在")
        if case.status == "ARCHIVED":
            raise ResourceConflictError("归档 Web Case 不能确认录制")
        version_no = (
            session.scalar(
                select(func.max(WebCaseVersion.version_no)).where(
                    WebCaseVersion.web_case_id == case.id
                )
            )
            or 0
        ) + 1
        if case.status == "APPROVED":
            case.status = "DRAFT"
    version = WebCaseVersion(
        web_case_id=case.id,
        version_no=version_no,
        content=confirmation_content.model_dump(mode="json"),
        change_note=f"由录制 {recording.id} 人工确认",
        status="DRAFT",
        created_by=user.id,
    )
    session.add(version)
    session.flush()
    case.current_version_id = version.id
    recording.confirmed_web_case_id = case.id
    recording.confirmed_web_case_version_id = version.id
    recording.confirmed_by = user.id
    recording.confirmed_at = utc_now_naive()
    if ai_suggestion is not None:
        ai_suggestion.status = "ACCEPTED"
        ai_suggestion.draft_key = None
        ai_suggestion.human_content = confirmation_content.model_dump(mode="json")
        ai_suggestion.decision_note = payload.decision_note
        ai_suggestion.confirmed_web_case_id = case.id
        ai_suggestion.confirmed_web_case_version_id = version.id
        ai_suggestion.reviewed_by = user.id
        ai_suggestion.reviewed_at = utc_now_naive()
    _commit(session, "录制确认保存冲突")
    return WebRecordingConfirmResponse(
        recording_id=recording.id,
        web_case_id=case.id,
        web_case_version_id=version.id,
        status="DRAFT",
        idempotent=False,
    )
