from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import (
    AppError,
    ResourceConflictError,
    ResourceNotFoundError,
    RunValidationError,
)
from app.core.logging import get_logger
from app.core.time import utc_now_naive
from app.infrastructure.rabbitmq.client import TaskPublisher
from app.infrastructure.redis.client import RedisRunEventStream, RedisRunnerHeartbeatStore
from app.modules.auth.schemas import CurrentUser
from app.modules.environments.models import Environment
from app.modules.projects.schemas import ProjectStatus
from app.modules.projects.service import ensure_project_writable, get_project
from app.modules.runners.models import Runner
from app.modules.runners.schemas import RunnerSlotType
from app.modules.runners.service import get_runner_online_state
from app.modules.runs.enums import TERMINAL_RUN_STATUSES, RunStatus, RunType
from app.modules.runs.models import TestRun
from app.modules.runs.schemas import RunCreateRequest
from app.modules.runs.service import cancel_run, create_run, dispatch_run, validate_run_request
from app.modules.scenarios.models import Scenario, ScenarioVersion
from app.modules.test_cases.models import TestCase, TestCaseVersion
from app.modules.test_plans.models import (
    TestPlan,
    TestPlanItem,
    TestPlanRun,
    TestPlanRunItem,
)
from app.modules.test_plans.schemas import (
    TestPlanCreate,
    TestPlanItemInput,
    TestPlanItemResponse,
    TestPlanItemValidation,
    TestPlanListResponse,
    TestPlanResponse,
    TestPlanRunItemResponse,
    TestPlanRunListResponse,
    TestPlanRunnerOption,
    TestPlanRunnerOptionListResponse,
    TestPlanRunnerSlot,
    TestPlanRunResponse,
    TestPlanRunStartResponse,
    TestPlanUpdate,
    TestPlanValidationResponse,
)
from app.modules.web_cases.models import WebCase, WebCaseVersion

logger = get_logger(__name__)


def _load_plan(
    session: Session, user: CurrentUser, plan_id: int, *, for_update: bool = False
) -> TestPlan:
    statement = (
        select(TestPlan)
        .where(TestPlan.id == plan_id)
        .options(selectinload(TestPlan.items))
    )
    if for_update:
        statement = statement.with_for_update()
    plan = session.scalar(statement)
    if plan is None:
        raise ResourceNotFoundError("Test Plan 不存在")
    get_project(session, user, plan.project_id)
    return plan


def _pin_item(
    session: Session, project_id: int, item: TestPlanItemInput, sequence_no: int
) -> TestPlanItem:
    values: dict[str, Any] = {
        "sequence_no": sequence_no,
        "target_type": item.target_type.value,
        "enabled": item.enabled,
    }
    if item.target_type == RunType.API_CASE:
        target = session.get(TestCase, item.case_id)
        if (
            target is None
            or target.project_id != project_id
            or target.status != "ACTIVE"
            or target.case_type != "API"
        ):
            raise ResourceConflictError("Test Plan 包含不可执行的 API Case")
        version_id = item.case_version_id or target.current_version_id
        version = session.get(TestCaseVersion, version_id) if version_id else None
        if version is None or version.case_id != target.id:
            raise ResourceConflictError("Test Plan API Case 缺少有效固定版本")
        values.update(
            target_name_snapshot=target.name,
            case_id=target.id,
            case_version_id=version.id,
        )
    elif item.target_type == RunType.SCENARIO:
        target = session.get(Scenario, item.scenario_id)
        if target is None or target.project_id != project_id or target.status == "ARCHIVED":
            raise ResourceConflictError("Test Plan 包含不可执行的 Scenario")
        version_id = item.scenario_version_id or target.current_version_id
        version = session.get(ScenarioVersion, version_id) if version_id else None
        if version is None or version.scenario_id != target.id:
            raise ResourceConflictError("Test Plan Scenario 缺少有效固定版本")
        values.update(
            target_name_snapshot=target.name,
            scenario_id=target.id,
            scenario_version_id=version.id,
        )
    else:
        target = session.get(WebCase, item.web_case_id)
        if target is None or target.project_id != project_id or target.status != "APPROVED":
            raise ResourceConflictError("Test Plan 包含未审批或不可执行的 Web Case")
        version_id = item.web_case_version_id or target.current_version_id
        version = session.get(WebCaseVersion, version_id) if version_id else None
        if version is None or version.web_case_id != target.id or version.status != "APPROVED":
            raise ResourceConflictError("Test Plan Web Case 缺少有效已审批固定版本")
        values.update(
            target_name_snapshot=target.name,
            web_case_id=target.id,
            web_case_version_id=version.id,
        )
    return TestPlanItem(**values)


def _validate_plan_config(session: Session, user: CurrentUser, payload: TestPlanCreate) -> None:
    project = get_project(session, user, payload.project_id)
    ensure_project_writable(session, project, user)
    if project.status == ProjectStatus.ARCHIVED.value:
        raise ResourceConflictError("归档项目不能保存 Test Plan")
    environment = session.get(Environment, payload.environment_id)
    if (
        environment is None
        or environment.project_id != payload.project_id
        or not environment.enabled
    ):
        raise ResourceConflictError("Test Plan Environment 不存在、不属于项目或已停用")
    runner = session.get(Runner, payload.runner_id)
    if runner is None or runner.status != "ACTIVE":
        raise ResourceConflictError("Test Plan Runner 不存在或已停用")


def _plan_item_response(item: TestPlanItem) -> TestPlanItemResponse:
    return TestPlanItemResponse(
        id=item.id,
        sequence_no=item.sequence_no,
        target_type=item.target_type,
        target_name=item.target_name_snapshot,
        case_id=item.case_id,
        case_version_id=item.case_version_id,
        scenario_id=item.scenario_id,
        scenario_version_id=item.scenario_version_id,
        web_case_id=item.web_case_id,
        web_case_version_id=item.web_case_version_id,
        enabled=item.enabled,
    )


def _plan_response(plan: TestPlan) -> TestPlanResponse:
    return TestPlanResponse(
        id=plan.id,
        project_id=plan.project_id,
        name=plan.name,
        description=plan.description,
        environment_id=plan.environment_id,
        runner_id=plan.runner_id,
        runtime_variables=plan.runtime_variables or {},
        execution_mode=plan.execution_mode,
        failure_strategy=plan.failure_strategy,
        status=plan.status,
        items=[_plan_item_response(item) for item in plan.items],
        created_by=plan.created_by,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


def create_test_plan(
    session: Session, user: CurrentUser, payload: TestPlanCreate
) -> TestPlanResponse:
    _validate_plan_config(session, user, payload)
    plan = TestPlan(
        project_id=payload.project_id,
        name=payload.name,
        description=payload.description,
        environment_id=payload.environment_id,
        runner_id=payload.runner_id,
        runtime_variables=dict(payload.runtime_variables),
        execution_mode=payload.execution_mode,
        failure_strategy=payload.failure_strategy,
        status="ACTIVE",
        created_by=user.id,
    )
    plan.items = [
        _pin_item(session, payload.project_id, item, sequence)
        for sequence, item in enumerate(payload.items, start=1)
    ]
    session.add(plan)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError("Test Plan 创建冲突") from exc
    return _plan_response(_load_plan(session, user, plan.id))


def update_test_plan(
    session: Session, user: CurrentUser, plan_id: int, payload: TestPlanUpdate
) -> TestPlanResponse:
    plan = _load_plan(session, user, plan_id, for_update=True)
    if plan.project_id != payload.project_id:
        raise ResourceConflictError("Test Plan 不能跨项目移动")
    _validate_plan_config(session, user, TestPlanCreate.model_validate(payload.model_dump()))
    project = get_project(session, user, plan.project_id)
    ensure_project_writable(session, project, user)
    if plan.status != "ACTIVE":
        raise ResourceConflictError("已归档 Test Plan 不能编辑")
    replacement = [
        _pin_item(session, payload.project_id, item, sequence)
        for sequence, item in enumerate(payload.items, start=1)
    ]
    plan.name = payload.name
    plan.description = payload.description
    plan.environment_id = payload.environment_id
    plan.runner_id = payload.runner_id
    plan.runtime_variables = dict(payload.runtime_variables)
    plan.execution_mode = payload.execution_mode
    plan.failure_strategy = payload.failure_strategy
    plan.items.clear()
    session.flush()
    plan.items.extend(replacement)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError("Test Plan 更新冲突") from exc
    return _plan_response(_load_plan(session, user, plan.id))


def list_test_plans(
    session: Session, user: CurrentUser, project_id: int
) -> TestPlanListResponse:
    get_project(session, user, project_id)
    plans = list(
        session.scalars(
            select(TestPlan)
            .where(TestPlan.project_id == project_id)
            .options(selectinload(TestPlan.items))
            .order_by(TestPlan.created_at.desc(), TestPlan.id.desc())
        ).all()
    )
    return TestPlanListResponse(items=[_plan_response(plan) for plan in plans], total=len(plans))


def get_test_plan(
    session: Session, user: CurrentUser, plan_id: int
) -> TestPlanResponse:
    return _plan_response(_load_plan(session, user, plan_id))


def set_test_plan_status(
    session: Session, user: CurrentUser, plan_id: int, status: str
) -> TestPlanResponse:
    plan = _load_plan(session, user, plan_id, for_update=True)
    project = get_project(session, user, plan.project_id)
    ensure_project_writable(session, project, user)
    plan.status = status
    session.commit()
    return _plan_response(_load_plan(session, user, plan.id))


def list_runner_options(
    session: Session,
    user: CurrentUser,
    project_id: int,
    store: RedisRunnerHeartbeatStore,
) -> TestPlanRunnerOptionListResponse:
    get_project(session, user, project_id)
    runners = list(
        session.scalars(
            select(Runner)
            .where(Runner.status == "ACTIVE")
            .options(selectinload(Runner.capabilities), selectinload(Runner.slots))
            .order_by(Runner.name.asc(), Runner.id.asc())
        ).all()
    )
    options: list[TestPlanRunnerOption] = []
    for runner in runners:
        _, online, _ = get_runner_online_state(runner, store)
        slots = [
            TestPlanRunnerSlot(type=slot.slot_type, available=slot.available)
            for slot in runner.slots
            if slot.slot_type in {RunnerSlotType.API.value, RunnerSlotType.WEB.value}
            and slot.available > 0
        ]
        if online and slots:
            options.append(
                TestPlanRunnerOption(
                    id=runner.id,
                    name=runner.name,
                    ready_capabilities=sorted(
                        capability.capability
                        for capability in runner.capabilities
                        if capability.status == "READY"
                    ),
                    slots=sorted(slots, key=lambda item: item.type),
                )
            )
    return TestPlanRunnerOptionListResponse(items=options, total=len(options))


def _item_run_payload(plan: TestPlan, item: TestPlanItem) -> RunCreateRequest:
    return RunCreateRequest(
        project_id=plan.project_id,
        environment_id=plan.environment_id,
        runner_id=plan.runner_id,
        run_type=item.target_type,
        case_id=item.case_id,
        case_version_id=item.case_version_id,
        scenario_id=item.scenario_id,
        scenario_version_id=item.scenario_version_id,
        web_case_id=item.web_case_id,
        web_case_version_id=item.web_case_version_id,
        trigger_type="SYSTEM",
        required_slot_type=(
            RunnerSlotType.WEB if item.target_type == RunType.WEB_CASE.value else RunnerSlotType.API
        ),
        runtime_variables=plan.runtime_variables or {},
    )


def validate_test_plan(
    session: Session,
    user: CurrentUser,
    plan_id: int,
    store: RedisRunnerHeartbeatStore,
) -> TestPlanValidationResponse:
    plan = _load_plan(session, user, plan_id)
    results: list[TestPlanItemValidation] = []
    for item in plan.items:
        if not item.enabled:
            continue
        validation, _ = validate_run_request(
            session, user, _item_run_payload(plan, item), store
        )
        results.append(
            TestPlanItemValidation(
                sequence_no=item.sequence_no,
                target_type=item.target_type,
                target_name=item.target_name_snapshot,
                valid=validation.valid,
                issues=validation.issues,
            )
        )
    return TestPlanValidationResponse(
        valid=bool(results) and all(item.valid for item in results),
        item_count=len(results),
        items=results,
    )


def _plan_snapshot(plan: TestPlan) -> dict[str, Any]:
    return {
        "name": plan.name,
        "description": plan.description,
        "environment_id": plan.environment_id,
        "runner_id": plan.runner_id,
        "runtime_variables": plan.runtime_variables or {},
        "execution_mode": plan.execution_mode,
        "failure_strategy": plan.failure_strategy,
        "items": [
            {
                "sequence_no": item.sequence_no,
                "target_type": item.target_type,
                "target_name": item.target_name_snapshot,
                "case_id": item.case_id,
                "case_version_id": item.case_version_id,
                "scenario_id": item.scenario_id,
                "scenario_version_id": item.scenario_version_id,
                "web_case_id": item.web_case_id,
                "web_case_version_id": item.web_case_version_id,
            }
            for item in plan.items
            if item.enabled
        ],
    }


def _refresh_plan_run(session: Session, plan_run: TestPlanRun) -> TestPlanRunResponse:
    statuses: list[RunStatus] = []
    responses: list[TestPlanRunItemResponse] = []
    changed = False
    for item in plan_run.items:
        run = session.get(TestRun, item.run_id) if item.run_id else None
        if run is not None and item.status != run.status:
            item.status = run.status
            changed = True
        status = RunStatus(item.status)
        statuses.append(status)
        responses.append(
            TestPlanRunItemResponse(
                id=item.id,
                sequence_no=item.sequence_no,
                target_type=item.target_type,
                target_name=item.target_name_snapshot,
                run_id=item.run_id,
                run_code=run.run_code if run else None,
                status=status,
                error_code=item.error_code,
                error_message=item.error_message,
            )
        )
    terminal = all(status in TERMINAL_RUN_STATUSES for status in statuses)
    if statuses and all(status == RunStatus.SUCCESS for status in statuses):
        aggregate = RunStatus.SUCCESS
    elif statuses and all(status == RunStatus.CANCELLED for status in statuses):
        aggregate = RunStatus.CANCELLED
    elif statuses and all(status == RunStatus.TIMEOUT for status in statuses):
        aggregate = RunStatus.TIMEOUT
    elif terminal:
        aggregate = RunStatus.FAILED
    elif any(
        status in {RunStatus.ASSIGNED, RunStatus.RUNNING, RunStatus.CANCELLING}
        for status in statuses
    ):
        aggregate = RunStatus.RUNNING
    elif any(status == RunStatus.QUEUED for status in statuses):
        aggregate = RunStatus.QUEUED
    else:
        aggregate = RunStatus.CREATED
    if plan_run.status != aggregate.value:
        plan_run.status = aggregate.value
        changed = True
    if terminal and plan_run.ended_at is None:
        plan_run.ended_at = utc_now_naive()
        changed = True
    if changed:
        session.commit()
    if terminal:
        from app.modules.ci_cd.service import enqueue_test_plan_run_webhooks

        enqueue_test_plan_run_webhooks(session, plan_run)
    return TestPlanRunResponse(
        id=plan_run.id,
        plan_id=plan_run.plan_id,
        project_id=plan_run.project_id,
        plan_name=str(plan_run.plan_snapshot.get("name") or "Test Plan"),
        status=aggregate,
        trigger_type=plan_run.trigger_type,
        schedule_id=plan_run.schedule_id,
        total=len(statuses),
        passed=sum(status == RunStatus.SUCCESS for status in statuses),
        failed=sum(
            status in TERMINAL_RUN_STATUSES and status != RunStatus.SUCCESS
            for status in statuses
        ),
        running=sum(
            status in {RunStatus.ASSIGNED, RunStatus.RUNNING, RunStatus.CANCELLING}
            for status in statuses
        ),
        pending=sum(status in {RunStatus.CREATED, RunStatus.QUEUED} for status in statuses),
        items=responses,
        created_by=plan_run.created_by,
        created_at=plan_run.created_at,
        started_at=plan_run.started_at,
        ended_at=plan_run.ended_at,
        updated_at=plan_run.updated_at,
    )


def _load_plan_run(
    session: Session, user: CurrentUser, plan_run_id: str
) -> TestPlanRun:
    plan_run = session.scalar(
        select(TestPlanRun)
        .where(TestPlanRun.id == plan_run_id)
        .options(selectinload(TestPlanRun.items))
    )
    if plan_run is None:
        raise ResourceNotFoundError("Test Plan Run 不存在")
    get_project(session, user, plan_run.project_id)
    return plan_run


def start_test_plan_run(
    session: Session,
    user: CurrentUser,
    plan_id: int,
    store: RedisRunnerHeartbeatStore,
    publisher: TaskPublisher,
    event_stream: RedisRunEventStream,
    *,
    trigger_type: str = "MANUAL",
    schedule_id: int | None = None,
) -> TestPlanRunStartResponse:
    plan = _load_plan(session, user, plan_id)
    project = get_project(session, user, plan.project_id)
    ensure_project_writable(session, project, user)
    if plan.status != "ACTIVE":
        raise ResourceConflictError("已归档 Test Plan 不能执行")
    validation = validate_test_plan(session, user, plan_id, store)
    if not validation.valid:
        details = [
            {
                **issue.model_dump(),
                "sequence_no": item.sequence_no,
                "target_name": item.target_name,
            }
            for item in validation.items
            for issue in item.issues
        ]
        raise RunValidationError(details)
    enabled_items = [item for item in plan.items if item.enabled]
    plan_run = TestPlanRun(
        id=f"planrun_{uuid4().hex}",
        plan_id=plan.id,
        project_id=plan.project_id,
        status=RunStatus.CREATED.value,
        trigger_type=trigger_type,
        schedule_id=schedule_id,
        plan_snapshot=_plan_snapshot(plan),
        created_by=user.id,
        started_at=utc_now_naive(),
    )
    plan_run.items = [
        TestPlanRunItem(
            plan_item_id=item.id,
            sequence_no=item.sequence_no,
            target_type=item.target_type,
            target_name_snapshot=item.target_name_snapshot,
            status=RunStatus.CREATED.value,
        )
        for item in enabled_items
    ]
    session.add(plan_run)
    session.commit()

    for plan_item, run_item in zip(enabled_items, plan_run.items, strict=True):
        try:
            created = create_run(
                session,
                user,
                _item_run_payload(plan, plan_item),
                store,
                event_stream,
            )
            run_item.run_id = created.id
            run_item.status = created.status.value
            session.commit()
            dispatched = dispatch_run(
                session, user, created.id, store, publisher, event_stream
            )
            run_item.status = dispatched.run_status.value
            if dispatched.outbox_status.value == "FAILED":
                run_item.error_code = "DISPATCH_FAILED"
                run_item.error_message = dispatched.last_error
            session.commit()
        except AppError as exc:
            session.rollback()
            persisted = session.get(TestPlanRunItem, run_item.id)
            if persisted is not None:
                persisted.status = RunStatus.FAILED.value
                persisted.error_code = exc.code
                persisted.error_message = exc.message
                session.commit()
            logger.warning(
                "test_plan_item_start_failed",
                extra={
                    "event": "TEST_PLAN_ITEM_START_FAILED",
                    "plan_run_id": plan_run.id,
                    "sequence_no": plan_item.sequence_no,
                    "error_code": exc.code,
                },
            )
    refreshed = _load_plan_run(session, user, plan_run.id)
    return TestPlanRunStartResponse(
        plan_run=_refresh_plan_run(session, refreshed), validation=validation
    )


def get_test_plan_run(
    session: Session, user: CurrentUser, plan_run_id: str
) -> TestPlanRunResponse:
    return _refresh_plan_run(session, _load_plan_run(session, user, plan_run_id))


def refresh_test_plan_run_state(session: Session, plan_run_id: str) -> TestPlanRunResponse:
    """Refresh aggregate state for trusted background coordinators."""
    plan_run = session.scalar(
        select(TestPlanRun)
        .where(TestPlanRun.id == plan_run_id)
        .options(selectinload(TestPlanRun.items))
    )
    if plan_run is None:
        raise ResourceNotFoundError("Test Plan Run 不存在")
    return _refresh_plan_run(session, plan_run)


def list_test_plan_runs(
    session: Session, user: CurrentUser, project_id: int
) -> TestPlanRunListResponse:
    get_project(session, user, project_id)
    runs = list(
        session.scalars(
            select(TestPlanRun)
            .where(TestPlanRun.project_id == project_id)
            .options(selectinload(TestPlanRun.items))
            .order_by(TestPlanRun.created_at.desc(), TestPlanRun.id.desc())
        ).all()
    )
    return TestPlanRunListResponse(
        items=[_refresh_plan_run(session, plan_run) for plan_run in runs], total=len(runs)
    )


def cancel_test_plan_run(
    session: Session,
    user: CurrentUser,
    plan_run_id: str,
    event_stream: RedisRunEventStream,
) -> TestPlanRunResponse:
    plan_run = _load_plan_run(session, user, plan_run_id)
    project = get_project(session, user, plan_run.project_id)
    ensure_project_writable(session, project, user)
    for item in plan_run.items:
        if not item.run_id:
            continue
        run = session.get(TestRun, item.run_id)
        if run is None or RunStatus(run.status) in TERMINAL_RUN_STATUSES:
            continue
        try:
            cancel_run(session, user, run.id, event_stream)
        except AppError as exc:
            session.rollback()
            persisted = session.get(TestPlanRunItem, item.id)
            if persisted is not None:
                persisted.error_code = exc.code
                persisted.error_message = exc.message
                session.commit()
    return _refresh_plan_run(session, _load_plan_run(session, user, plan_run_id))


def retry_test_plan_run_dispatch(
    session: Session,
    user: CurrentUser,
    plan_run_id: str,
    store: RedisRunnerHeartbeatStore,
    publisher: TaskPublisher,
    event_stream: RedisRunEventStream,
) -> TestPlanRunResponse:
    plan_run = _load_plan_run(session, user, plan_run_id)
    project = get_project(session, user, plan_run.project_id)
    ensure_project_writable(session, project, user)
    for item in plan_run.items:
        run = session.get(TestRun, item.run_id) if item.run_id else None
        if run is None or RunStatus(run.status) not in {RunStatus.CREATED, RunStatus.QUEUED}:
            continue
        try:
            dispatched = dispatch_run(
                session, user, run.id, store, publisher, event_stream
            )
            persisted = session.get(TestPlanRunItem, item.id)
            if persisted is not None:
                persisted.status = dispatched.run_status.value
                if dispatched.outbox_status.value == "FAILED":
                    persisted.error_code = "DISPATCH_FAILED"
                    persisted.error_message = dispatched.last_error
                else:
                    persisted.error_code = None
                    persisted.error_message = None
                session.commit()
        except AppError as exc:
            session.rollback()
            persisted = session.get(TestPlanRunItem, item.id)
            if persisted is not None:
                persisted.error_code = exc.code
                persisted.error_message = exc.message
                session.commit()
    return _refresh_plan_run(session, _load_plan_run(session, user, plan_run_id))
