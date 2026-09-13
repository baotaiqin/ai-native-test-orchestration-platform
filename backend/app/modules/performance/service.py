from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import AppError, ResourceConflictError, ResourceNotFoundError
from app.core.metrics import PERFORMANCE_RUNS_COMPLETED
from app.core.time import utc_now_naive
from app.infrastructure.rabbitmq.client import TaskPublisher
from app.infrastructure.redis.client import RedisRunEventStream, RedisRunnerHeartbeatStore
from app.modules.ai_gateway.schemas import AiGenerateRequest
from app.modules.ai_gateway.service import generate
from app.modules.auth.schemas import CurrentUser
from app.modules.model_center.schemas import AiTaskType
from app.modules.performance.models import (
    PerformanceAnalysis,
    PerformanceProfile,
    PerformanceRun,
)
from app.modules.performance.schemas import (
    PerformanceAnalysisCreateRequest,
    PerformanceAnalysisListResponse,
    PerformanceAnalysisResponse,
    PerformanceAnalysisResult,
    PerformanceComparisonItem,
    PerformanceComparisonResponse,
    PerformanceCompleteRequest,
    PerformanceCompleteResponse,
    PerformanceExecutionPlanResponse,
    PerformanceMetricSummary,
    PerformanceProfileCreate,
    PerformanceProfileListResponse,
    PerformanceProfileResponse,
    PerformanceRunCreate,
    PerformanceRunListResponse,
    PerformanceRunnerOption,
    PerformanceRunnerOptionListResponse,
    PerformanceRunResponse,
    PerformanceRunStartResponse,
    PerformanceSlaResult,
)
from app.modules.projects.service import ensure_project_writable, get_project
from app.modules.prompt_center.models import (
    AiCallLog,
    OutputSchema,
    PromptDefinition,
    PromptVersion,
)
from app.modules.prompt_center.service import resolve_effective_prompt
from app.modules.runners.models import Runner
from app.modules.runners.schemas import RunnerCapabilityName, RunnerSlotType
from app.modules.runners.service import get_runner_online_state
from app.modules.runs.enums import RunNodeStatus, RunStatus, RunTriggerType, RunType
from app.modules.runs.events import publish_run_event_best_effort
from app.modules.runs.models import TestRun
from app.modules.runs.schemas import (
    NodeStatusUpdateRequest,
    RunCreateRequest,
    RunStatusUpdateRequest,
)
from app.modules.runs.service import (
    _execution_context,
    _recompute_summary,
    _set_node_status,
    _set_run_status,
    create_run,
    dispatch_run,
    get_execution_plan,
    get_scenario_execution_plan,
)
from app.modules.scenarios.models import Scenario, ScenarioVersion
from app.modules.scenarios.schemas import ScenarioDsl, ScenarioNodeType
from app.modules.test_cases.models import TestCase, TestCaseVersion
from app.modules.test_cases.schemas import CaseType, SuggestedCase


def _load_profile(session: Session, user: CurrentUser, profile_id: int) -> PerformanceProfile:
    profile = session.get(PerformanceProfile, profile_id)
    if profile is None:
        raise ResourceNotFoundError("性能测试配置不存在")
    get_project(session, user, profile.project_id)
    return profile


def _load_compatible_case(
    session: Session, project_id: int, case_id: int, case_version_id: int | None
) -> tuple[TestCase, TestCaseVersion, SuggestedCase]:
    case = session.get(TestCase, case_id)
    if case is None or case.project_id != project_id or case.status != "ACTIVE":
        raise ResourceConflictError("性能测试目标用例不存在、已归档或不属于当前项目")
    version_id = case_version_id or case.current_version_id
    version = session.get(TestCaseVersion, version_id) if version_id is not None else None
    if version is None or version.case_id != case.id:
        raise ResourceConflictError("性能测试目标用例版本不存在")
    try:
        content = SuggestedCase.model_validate(version.content)
    except ValueError as exc:
        raise ResourceConflictError("性能测试目标用例内容无效") from exc
    if content.case_type != CaseType.API or content.request is None:
        raise ResourceConflictError("首批性能主流程仅支持可执行 API 用例")
    return case, version, content


def _load_compatible_scenario(
    session: Session, project_id: int, scenario_id: int, scenario_version_id: int | None
) -> tuple[Scenario, ScenarioVersion, ScenarioDsl]:
    scenario = session.get(Scenario, scenario_id)
    if scenario is None or scenario.project_id != project_id or scenario.status == "ARCHIVED":
        raise ResourceConflictError("性能测试目标 Scenario 不存在、已归档或不属于当前项目")
    version_id = scenario_version_id or scenario.current_version_id
    version = session.get(ScenarioVersion, version_id) if version_id is not None else None
    if version is None or version.scenario_id != scenario.id:
        raise ResourceConflictError("性能测试目标 Scenario 版本不存在")
    try:
        dsl = ScenarioDsl.model_validate(version.dsl)
    except ValueError as exc:
        raise ResourceConflictError("性能测试目标 Scenario 内容无效") from exc
    if any(node.type == ScenarioNodeType.AI_ASSERTION for node in dsl.nodes):
        raise ResourceConflictError("性能测试 Scenario 暂不执行需要服务端 AI 判定的节点")
    return scenario, version, dsl


def _validate_engine_target(
    payload: PerformanceProfileCreate | PerformanceProfile, content: SuggestedCase | None
) -> None:
    if content is not None and content.data_source is not None:
        raise ResourceConflictError("性能测试目标暂不复用用例数据源，请使用固定 Runtime 变量")
    if (
        payload.engine in {"PYTHON_STREAM", "JMETER"}
        and content is not None
        and (content.pre_actions or content.post_actions or content.extractors or content.cleanup)
    ):
        raise ResourceConflictError("流式/JMeter 引擎仅支持不含动作、提取器和 Cleanup 的 API 用例")
    if payload.engine == "PYTHON_STREAM" and payload.cleanup_mode != "NONE":
        raise ResourceConflictError("流式执行当前仅支持 NONE Cleanup")
    if payload.engine == "JMETER" and payload.cleanup_mode != "NONE":
        raise ResourceConflictError("JMeter 执行当前仅支持 NONE Cleanup")
    if payload.engine == "JMETER" and payload.load_mode not in {
        "FIXED_ITERATIONS",
        "FIXED_RPS",
    }:
        raise ResourceConflictError("JMeter 当前仅支持固定迭代和固定 RPS")
    if (
        payload.engine == "JMETER"
        and content is not None
        and content.request is not None
        and content.request.body.type == "MULTIPART"
    ):
        raise ResourceConflictError("JMeter 性能目标暂不支持 Multipart 请求体")


def create_profile(
    session: Session, user: CurrentUser, payload: PerformanceProfileCreate
) -> PerformanceProfileResponse:
    project = get_project(session, user, payload.project_id)
    ensure_project_writable(session, project, user)
    case_version: TestCaseVersion | None = None
    scenario_version: ScenarioVersion | None = None
    content: SuggestedCase | None = None
    if payload.target_type == "API_CASE":
        _, case_version, content = _load_compatible_case(
            session, payload.project_id, payload.case_id or 0, payload.case_version_id
        )
    else:
        _, scenario_version, _ = _load_compatible_scenario(
            session, payload.project_id, payload.scenario_id or 0, payload.scenario_version_id
        )
    _validate_engine_target(payload, content)
    profile = PerformanceProfile(
        project_id=payload.project_id,
        name=payload.name.strip(),
        description=payload.description.strip() if payload.description else None,
        target_type=payload.target_type,
        case_id=payload.case_id,
        case_version_id=case_version.id if case_version is not None else None,
        scenario_id=payload.scenario_id,
        scenario_version_id=scenario_version.id if scenario_version is not None else None,
        concurrency=payload.concurrency,
        iterations=payload.iterations,
        load_mode=payload.load_mode,
        target_rps=payload.target_rps,
        duration_seconds=payload.duration_seconds,
        step_stages=(
            [stage.model_dump(mode="json") for stage in payload.step_stages]
            if payload.step_stages
            else None
        ),
        warmup_iterations=payload.warmup_iterations,
        request_timeout_ms=payload.request_timeout_ms,
        engine=payload.engine,
        cleanup_mode=payload.cleanup_mode,
        stream_config=payload.stream_config.model_dump(mode="json"),
        sla=payload.sla.model_dump(mode="json", exclude_none=True),
        created_by=user.id,
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return PerformanceProfileResponse.model_validate(profile)


def list_profiles(
    session: Session, user: CurrentUser, project_id: int
) -> PerformanceProfileListResponse:
    get_project(session, user, project_id)
    items = list(
        session.scalars(
            select(PerformanceProfile)
            .where(PerformanceProfile.project_id == project_id)
            .order_by(PerformanceProfile.created_at.desc(), PerformanceProfile.id.desc())
        ).all()
    )
    return PerformanceProfileListResponse(
        items=[PerformanceProfileResponse.model_validate(item) for item in items],
        total=len(items),
    )


def list_runner_options(
    session: Session,
    user: CurrentUser,
    project_id: int,
    heartbeat_store: RedisRunnerHeartbeatStore,
) -> PerformanceRunnerOptionListResponse:
    """Expose only runnable performance capacity to project members."""

    get_project(session, user, project_id)
    runners = list(
        session.scalars(
            select(Runner)
            .where(Runner.status == "ACTIVE")
            .options(selectinload(Runner.capabilities), selectinload(Runner.slots))
            .order_by(Runner.name.asc(), Runner.id.asc())
        ).all()
    )
    items: list[PerformanceRunnerOption] = []
    for runner in runners:
        _, online, _ = get_runner_online_state(runner, heartbeat_store)
        ready_capabilities = sorted(
            capability.capability
            for capability in runner.capabilities
            if capability.status == "READY"
        )
        performance_slot = next(
            (
                slot
                for slot in runner.slots
                if slot.slot_type == RunnerSlotType.PERFORMANCE.value and slot.available > 0
            ),
            None,
        )
        if online and performance_slot is not None:
            items.append(
                PerformanceRunnerOption(
                    id=runner.id,
                    name=runner.name,
                    performance_slots_available=performance_slot.available,
                    ready_capabilities=ready_capabilities,
                )
            )
    return PerformanceRunnerOptionListResponse(items=items, total=len(items))


def start_run(
    session: Session,
    user: CurrentUser,
    profile_id: int,
    payload: PerformanceRunCreate,
    heartbeat_store: RedisRunnerHeartbeatStore,
    publisher: TaskPublisher,
    event_stream: RedisRunEventStream,
) -> PerformanceRunStartResponse:
    profile = _load_profile(session, user, profile_id)
    project = get_project(session, user, profile.project_id)
    ensure_project_writable(session, project, user)
    if profile.status != "ACTIVE":
        raise ResourceConflictError("已归档的性能测试配置不能执行")
    required_capabilities: list[RunnerCapabilityName] = []
    if profile.target_type == "API_CASE":
        _, _, content = _load_compatible_case(
            session, profile.project_id, profile.case_id or 0, profile.case_version_id
        )
        _validate_engine_target(profile, content)
        required_capabilities.append(RunnerCapabilityName.API)
    else:
        _load_compatible_scenario(
            session, profile.project_id, profile.scenario_id or 0, profile.scenario_version_id
        )
    if profile.engine == "PYTHON_STREAM":
        required_capabilities.append(RunnerCapabilityName.SSE)
    elif profile.engine == "JMETER":
        required_capabilities.append(RunnerCapabilityName.JMETER)
    warmup_timeout_ms = (
        math.ceil(profile.warmup_iterations / profile.concurrency) * profile.request_timeout_ms
    )
    if profile.load_mode in {"FIXED_RPS", "FIXED_CONCURRENCY"}:
        execution_timeout_ms = (profile.duration_seconds or 0) * 1000 + profile.request_timeout_ms
    elif profile.load_mode == "STEP_LOAD":
        execution_timeout_ms = (
            sum(int(stage["duration_seconds"]) for stage in profile.step_stages or []) * 1000
            + profile.request_timeout_ms
        )
    else:
        execution_timeout_ms = (
            math.ceil(profile.iterations / profile.concurrency) * profile.request_timeout_ms
        )
    total_timeout_ms = min(
        86_400_000, max(60_000, warmup_timeout_ms + execution_timeout_ms + 30_000)
    )
    created = create_run(
        session,
        user,
        RunCreateRequest(
            project_id=profile.project_id,
            environment_id=payload.environment_id,
            runner_id=payload.runner_id,
            run_type=(RunType.API_CASE if profile.target_type == "API_CASE" else RunType.SCENARIO),
            case_id=profile.case_id,
            case_version_id=profile.case_version_id,
            scenario_id=profile.scenario_id,
            scenario_version_id=profile.scenario_version_id,
            trigger_type=RunTriggerType.MANUAL,
            required_capabilities=required_capabilities,
            required_slot_type=RunnerSlotType.PERFORMANCE,
            required_slot_count=1,
            total_timeout_ms=total_timeout_ms,
        ),
        heartbeat_store,
        event_stream,
    )
    snapshot = {
        "profile_id": profile.id,
        "profile_name": profile.name,
        "case_id": profile.case_id,
        "case_version_id": profile.case_version_id,
        "target_type": profile.target_type,
        "scenario_id": profile.scenario_id,
        "scenario_version_id": profile.scenario_version_id,
        "concurrency": profile.concurrency,
        "iterations": profile.iterations,
        "load_mode": profile.load_mode,
        "target_rps": profile.target_rps,
        "duration_seconds": profile.duration_seconds,
        "step_stages": profile.step_stages,
        "warmup_iterations": profile.warmup_iterations,
        "request_timeout_ms": profile.request_timeout_ms,
        "engine": profile.engine,
        "cleanup_mode": profile.cleanup_mode,
        "stream_config": dict(profile.stream_config or {}),
        "sla": dict(profile.sla or {}),
    }
    performance_run = PerformanceRun(
        run_id=created.id,
        profile_id=profile.id,
        project_id=profile.project_id,
        config_snapshot=snapshot,
        status=created.status.value,
    )
    session.add(performance_run)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError("性能 Run 创建冲突") from exc
    dispatched = dispatch_run(session, user, created.id, heartbeat_store, publisher, event_stream)
    current = session.get(PerformanceRun, created.id)
    if current is not None:
        run = session.get(TestRun, created.id)
        current.status = run.status if run is not None else current.status
        session.commit()
    return PerformanceRunStartResponse(run=created, dispatch=dispatched)


def _percentile(values: list[int], quantile: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def _summarize(
    payload: PerformanceCompleteRequest,
) -> tuple[PerformanceMetricSummary, dict[str, int]]:
    durations = [item.duration_ms for item in payload.samples]
    successes = [
        item for item in payload.samples if item.status_code is not None and item.status_code < 400
    ]
    failures = len(payload.samples) - len(successes)
    errors: Counter[str] = Counter()
    for item in payload.samples:
        if item.error_type:
            errors[item.error_type] += 1
        elif item.status_code is not None and item.status_code >= 400:
            errors[f"HTTP_{item.status_code}"] += 1
    count = len(payload.samples)
    ttfts = [item.ttft_ms for item in payload.samples if item.ttft_ms is not None]
    stream_durations = [
        item.stream_duration_ms for item in payload.samples if item.stream_duration_ms is not None
    ]
    input_tokens = [item.input_tokens for item in payload.samples if item.input_tokens is not None]
    output_tokens = [
        item.output_tokens for item in payload.samples if item.output_tokens is not None
    ]
    chunk_counts = [item.chunk_count for item in payload.samples if item.chunk_count is not None]
    chunk_gaps = [
        item.max_chunk_gap_ms for item in payload.samples if item.max_chunk_gap_ms is not None
    ]
    events: list[tuple[int, int]] = []
    for item in payload.samples:
        events.append((item.started_offset_ms, 1))
        events.append((item.started_offset_ms + item.duration_ms, -1))
    active = 0
    active_peak = 0
    for _, delta in sorted(events, key=lambda event: (event[0], -event[1])):
        active += delta
        active_peak = max(active_peak, active)
    buckets: dict[int, list] = {}
    for item in payload.samples:
        buckets.setdefault(item.started_offset_ms // 1000, []).append(item)
    trends = []
    for second, bucket in sorted(buckets.items()):
        bucket_durations = [item.duration_ms for item in bucket]
        bucket_success = sum(
            1 for item in bucket if item.status_code is not None and item.status_code < 400
        )
        trends.append(
            {
                "second": second,
                "requests": len(bucket),
                "success": bucket_success,
                "fail": len(bucket) - bucket_success,
                "average_ms": round(sum(bucket_durations) / len(bucket), 3),
                "p95_ms": _percentile(bucket_durations, 0.95),
            }
        )
    metrics = PerformanceMetricSummary(
        request_count=count,
        success_count=len(successes),
        fail_count=failures,
        success_rate=round(len(successes) / count, 6),
        error_rate=round(failures / count, 6),
        rps=round(count / (payload.wall_duration_ms / 1000), 3),
        tps=round(len(successes) / (payload.wall_duration_ms / 1000), 3),
        min_ms=min(durations),
        max_ms=max(durations),
        average_ms=round(sum(durations) / count, 3),
        p50_ms=_percentile(durations, 0.50),
        p75_ms=_percentile(durations, 0.75),
        p90_ms=_percentile(durations, 0.90),
        p95_ms=_percentile(durations, 0.95),
        p99_ms=_percentile(durations, 0.99),
        received_bytes=sum(item.bytes_received for item in payload.samples),
        sent_bytes=sum(item.bytes_sent for item in payload.samples),
        active_users_peak=active_peak,
        wall_duration_ms=payload.wall_duration_ms,
        ttft_average_ms=(round(sum(ttfts) / len(ttfts), 3) if ttfts else None),
        ttft_p95_ms=(_percentile(ttfts, 0.95) if ttfts else None),
        stream_duration_average_ms=(
            round(sum(stream_durations) / len(stream_durations), 3) if stream_durations else None
        ),
        input_tokens=(sum(input_tokens) if input_tokens else None),
        output_tokens=(sum(output_tokens) if output_tokens else None),
        tokens_per_second=(
            round(sum(output_tokens) / (payload.wall_duration_ms / 1000), 3)
            if output_tokens
            else None
        ),
        chunk_count=(sum(chunk_counts) if chunk_counts else None),
        max_chunk_gap_ms=(max(chunk_gaps) if chunk_gaps else None),
        interrupted_streams=(
            sum(item.stream_completed is False for item in payload.samples)
            if any(item.stream_completed is not None for item in payload.samples)
            else None
        ),
        trends=trends,
    )
    return metrics, dict(sorted(errors.items()))


def _evaluate_sla(metrics: PerformanceMetricSummary, sla: dict) -> list[PerformanceSlaResult]:
    checks: list[PerformanceSlaResult] = []
    if sla.get("max_error_rate") is not None:
        threshold = float(sla["max_error_rate"])
        checks.append(
            PerformanceSlaResult(
                metric="error_rate",
                operator="LTE",
                threshold=threshold,
                actual=metrics.error_rate,
                passed=metrics.error_rate <= threshold,
            )
        )
    if sla.get("max_p95_ms") is not None:
        threshold = float(sla["max_p95_ms"])
        checks.append(
            PerformanceSlaResult(
                metric="p95_ms",
                operator="LTE",
                threshold=threshold,
                actual=float(metrics.p95_ms),
                passed=metrics.p95_ms <= threshold,
            )
        )
    if sla.get("max_p99_ms") is not None:
        threshold = float(sla["max_p99_ms"])
        checks.append(
            PerformanceSlaResult(
                metric="p99_ms",
                operator="LTE",
                threshold=threshold,
                actual=float(metrics.p99_ms),
                passed=metrics.p99_ms <= threshold,
            )
        )
    if sla.get("max_average_ms") is not None:
        threshold = float(sla["max_average_ms"])
        checks.append(
            PerformanceSlaResult(
                metric="average_ms",
                operator="LTE",
                threshold=threshold,
                actual=metrics.average_ms,
                passed=metrics.average_ms <= threshold,
            )
        )
    if sla.get("max_ttft_p95_ms") is not None:
        threshold = float(sla["max_ttft_p95_ms"])
        actual = float(metrics.ttft_p95_ms or 0)
        checks.append(
            PerformanceSlaResult(
                metric="ttft_p95_ms",
                operator="LTE",
                threshold=threshold,
                actual=actual,
                passed=metrics.ttft_p95_ms is not None and actual <= threshold,
            )
        )
    if sla.get("min_rps") is not None:
        threshold = float(sla["min_rps"])
        checks.append(
            PerformanceSlaResult(
                metric="rps",
                operator="GTE",
                threshold=threshold,
                actual=metrics.rps,
                passed=metrics.rps >= threshold,
            )
        )
    if sla.get("min_success_rate") is not None:
        threshold = float(sla["min_success_rate"])
        checks.append(
            PerformanceSlaResult(
                metric="success_rate",
                operator="GTE",
                threshold=threshold,
                actual=metrics.success_rate,
                passed=metrics.success_rate >= threshold,
            )
        )
    return checks


def _passes_performance_run(
    metrics: PerformanceMetricSummary,
    sla_results: list[PerformanceSlaResult],
    sla: dict,
) -> bool:
    if not all(item.passed for item in sla_results):
        return False
    has_error_budget = any(
        sla.get(metric) is not None for metric in ("max_error_rate", "min_success_rate")
    )
    return has_error_budget or metrics.fail_count == 0


def get_execution_plan_for_runner(
    session: Session,
    run_id: str,
    credential: str,
    message_id: str,
) -> PerformanceExecutionPlanResponse:
    performance_run = session.get(PerformanceRun, run_id)
    if performance_run is None:
        raise ResourceNotFoundError("性能 Run 不存在")
    config = performance_run.config_snapshot
    if config.get("target_type", "API_CASE") == "SCENARIO":
        plan = get_scenario_execution_plan(session, run_id, credential, message_id, None)
        request = None
    else:
        plan = get_execution_plan(session, run_id, credential, message_id, None)
        request = plan.request.model_copy(update={"timeout_ms": config["request_timeout_ms"]})
    return PerformanceExecutionPlanResponse(
        run_id=run_id,
        message_id=message_id,
        runner_id=plan.runner_id,
        case_run_id=plan.case_run_id,
        target_type=config.get("target_type", "API_CASE"),
        engine=config.get("engine", "PYTHON_HTTP"),
        request=request,
        concurrency=config["concurrency"],
        iterations=config["iterations"],
        load_mode=config.get("load_mode", "FIXED_ITERATIONS"),
        target_rps=config.get("target_rps"),
        duration_seconds=config.get("duration_seconds"),
        step_stages=config.get("step_stages"),
        warmup_iterations=config["warmup_iterations"],
        request_timeout_ms=config["request_timeout_ms"],
        cleanup_mode=config.get("cleanup_mode", "NONE"),
        stream_config=config.get("stream_config", {}),
        total_timeout_ms=plan.total_timeout_ms,
    )


def complete_run(
    session: Session,
    run_id: str,
    credential: str,
    payload: PerformanceCompleteRequest,
    event_stream: RedisRunEventStream,
) -> PerformanceCompleteResponse:
    context = _execution_context(
        session, run_id, credential, payload.message_id, payload.case_run_id
    )
    performance_run = session.get(PerformanceRun, run_id)
    if performance_run is None:
        raise ResourceNotFoundError("性能 Run 不存在")
    if performance_run.metrics is not None and performance_run.status in {"SUCCESS", "FAILED"}:
        return PerformanceCompleteResponse(
            run_id=run_id,
            status=performance_run.status,
            metrics=PerformanceMetricSummary.model_validate(performance_run.metrics),
            sla_results=[
                PerformanceSlaResult.model_validate(item)
                for item in performance_run.sla_results or []
            ],
            error_distribution=performance_run.error_distribution or {},
            idempotent=True,
        )
    if context.run.required_slot_type != RunnerSlotType.PERFORMANCE.value:
        raise ResourceConflictError("当前 Run 不是性能执行")
    if RunStatus(context.run.status) != RunStatus.RUNNING:
        raise ResourceConflictError("只有 RUNNING 性能 Run 可以完成")
    maximum = int(performance_run.config_snapshot["iterations"])
    if performance_run.config_snapshot.get("load_mode") in {"FIXED_CONCURRENCY", "STEP_LOAD"}:
        if len(payload.samples) > maximum:
            raise ResourceConflictError(f"性能样本数不能超过配置上限 {maximum}")
    elif len(payload.samples) != maximum:
        raise ResourceConflictError(f"性能样本数必须等于配置的 {maximum}")
    metrics, errors = _summarize(payload)
    sla = performance_run.config_snapshot.get("sla", {})
    sla_results = _evaluate_sla(metrics, sla)
    passed = _passes_performance_run(metrics, sla_results, sla)
    final_status = RunStatus.SUCCESS if passed else RunStatus.FAILED
    node_status = RunNodeStatus.SUCCESS if passed else RunNodeStatus.FAILED
    update = NodeStatusUpdateRequest(
        status=node_status,
        duration=payload.wall_duration_ms,
        error_type=None if passed else "PERFORMANCE_SLA_FAILED",
        error_message=None if passed else "性能请求失败或未满足确定性 SLA",
    )
    for step in context.case_run.step_runs:
        _set_node_status(step, node_status, update)
    _set_node_status(context.case_run, node_status, update)
    _recompute_summary(session, context.run)
    previous = RunStatus(context.run.status)
    _set_run_status(
        context.run,
        final_status,
        RunStatusUpdateRequest(
            status=final_status,
            error_type=None if passed else "PERFORMANCE_SLA_FAILED",
            error_message=None if passed else "性能请求失败或未满足确定性 SLA",
        ),
    )
    performance_run.status = final_status.value
    performance_run.metrics = metrics.model_dump(mode="json")
    performance_run.sla_results = [item.model_dump(mode="json") for item in sla_results]
    performance_run.error_distribution = errors
    performance_run.completed_at = utc_now_naive()
    session.commit()
    PERFORMANCE_RUNS_COMPLETED.labels(
        engine=str(performance_run.config_snapshot.get("engine", "PYTHON_HTTP")),
        status=final_status.value,
        sla_verdict=_analysis_verdict([item.model_dump(mode="json") for item in sla_results]),
    ).inc()
    publish_run_event_best_effort(
        event_stream,
        project_id=context.run.project_id,
        run_id=context.run.id,
        from_status=previous,
        to_status=final_status,
        case_run_id=context.case_run.id,
        total=context.run.total,
        pass_count=context.run.pass_count,
        fail_count=context.run.fail_count,
        review_count=context.run.review_count,
        timeout_count=context.run.timeout_count,
    )
    return PerformanceCompleteResponse(
        run_id=run_id,
        status=final_status.value,
        metrics=metrics,
        sla_results=sla_results,
        error_distribution=errors,
        idempotent=False,
    )


def _run_response(performance_run: PerformanceRun, run: TestRun) -> PerformanceRunResponse:
    return PerformanceRunResponse(
        run_id=run.id,
        profile_id=performance_run.profile_id,
        project_id=performance_run.project_id,
        run_code=run.run_code,
        status=run.status,
        config_snapshot=performance_run.config_snapshot,
        metrics=(
            PerformanceMetricSummary.model_validate(performance_run.metrics)
            if performance_run.metrics
            else None
        ),
        sla_results=[
            PerformanceSlaResult.model_validate(item) for item in performance_run.sla_results or []
        ],
        error_distribution=performance_run.error_distribution or {},
        created_at=performance_run.created_at,
        completed_at=performance_run.completed_at or run.ended_at,
    )


def list_runs(session: Session, user: CurrentUser, project_id: int) -> PerformanceRunListResponse:
    get_project(session, user, project_id)
    rows = list(
        session.execute(
            select(PerformanceRun, TestRun)
            .join(TestRun, TestRun.id == PerformanceRun.run_id)
            .where(PerformanceRun.project_id == project_id)
            .order_by(PerformanceRun.created_at.desc())
        ).all()
    )
    return PerformanceRunListResponse(
        items=[_run_response(performance_run, run) for performance_run, run in rows],
        total=len(rows),
    )


def get_run(session: Session, user: CurrentUser, run_id: str) -> PerformanceRunResponse:
    row = session.execute(
        select(PerformanceRun, TestRun)
        .join(TestRun, TestRun.id == PerformanceRun.run_id)
        .where(PerformanceRun.run_id == run_id)
    ).one_or_none()
    if row is None:
        raise ResourceNotFoundError("性能 Run 不存在")
    performance_run, run = row
    get_project(session, user, performance_run.project_id)
    return _run_response(performance_run, run)


def compare_runs(
    session: Session, user: CurrentUser, project_id: int, run_ids: list[str]
) -> PerformanceComparisonResponse:
    get_project(session, user, project_id)
    unique_ids = list(dict.fromkeys(run_ids))
    if not 2 <= len(unique_ids) <= 5:
        raise ResourceConflictError("运行对比必须选择 2 到 5 个不同的 Run")
    rows = list(
        session.execute(
            select(PerformanceRun, TestRun)
            .join(TestRun, TestRun.id == PerformanceRun.run_id)
            .where(
                PerformanceRun.project_id == project_id,
                PerformanceRun.run_id.in_(unique_ids),
            )
        ).all()
    )
    by_id = {performance_run.run_id: (performance_run, run) for performance_run, run in rows}
    if set(by_id) != set(unique_ids):
        raise ResourceConflictError("对比 Run 不存在或不属于当前项目")
    if any(performance_run.metrics is None for performance_run, _ in by_id.values()):
        raise ResourceConflictError("仅支持对比已产生指标的性能 Run")
    baseline = PerformanceMetricSummary.model_validate(by_id[unique_ids[0]][0].metrics)
    keys = ("rps", "average_ms", "p95_ms", "p99_ms", "error_rate", "success_rate")
    items: list[PerformanceComparisonItem] = []
    for run_id in unique_ids:
        performance_run, run = by_id[run_id]
        metrics = PerformanceMetricSummary.model_validate(performance_run.metrics)
        delta = {
            key: round(float(getattr(metrics, key)) - float(getattr(baseline, key)), 6)
            for key in keys
        }
        items.append(
            PerformanceComparisonItem(
                run_id=run_id,
                run_code=run.run_code,
                status=run.status,
                metrics=metrics,
                delta_from_baseline=delta,
            )
        )
    return PerformanceComparisonResponse(baseline_run_id=unique_ids[0], items=items)


_ANALYSIS_CONFIG_FIELDS = (
    "target_type",
    "concurrency",
    "iterations",
    "load_mode",
    "target_rps",
    "duration_seconds",
    "step_stages",
    "warmup_iterations",
    "request_timeout_ms",
    "engine",
    "cleanup_mode",
)
_SAFE_ERROR_TYPE = re.compile(r"^[A-Za-z0-9_:-]{1,100}$")
_MAX_ANALYSIS_SNAPSHOT_BYTES = 64 * 1024


def _analysis_verdict(sla_results: list[dict]) -> str:
    if not sla_results:
        return "NO_SLA"
    return "MEETS_SLA" if all(bool(item.get("passed")) for item in sla_results) else "MISSES_SLA"


def _trend_summary(trends: list[dict]) -> dict[str, float | int]:
    if not trends:
        return {"point_count": 0}
    p95_values = [float(item.get("p95_ms") or 0) for item in trends]
    average_values = [float(item.get("average_ms") or 0) for item in trends]
    midpoint = max(1, len(p95_values) // 2)
    first = p95_values[:midpoint]
    second = p95_values[midpoint:] or first
    return {
        "point_count": len(trends),
        "peak_requests": max(int(item.get("requests") or 0) for item in trends),
        "max_p95_ms": max(p95_values),
        "max_average_ms": max(average_values),
        "first_half_p95_average_ms": round(sum(first) / len(first), 3),
        "second_half_p95_average_ms": round(sum(second) / len(second), 3),
        "failed_requests": sum(int(item.get("fail") or 0) for item in trends),
    }


def _analysis_source_snapshot(
    performance_run: PerformanceRun,
) -> tuple[dict[str, Any], str, int, dict[str, float], str]:
    if performance_run.status not in {"SUCCESS", "FAILED"} or performance_run.metrics is None:
        raise ResourceConflictError("只有已产生确定性指标的终态性能 Run 可以生成 AI 分析")
    metrics = PerformanceMetricSummary.model_validate(performance_run.metrics)
    metrics_payload = metrics.model_dump(mode="json", exclude={"trends"})
    error_distribution: dict[str, int] = {}
    unclassified_errors = 0
    for key, count in (performance_run.error_distribution or {}).items():
        if _SAFE_ERROR_TYPE.fullmatch(str(key)):
            error_distribution[str(key)] = int(count)
        else:
            unclassified_errors += int(count)
    if unclassified_errors:
        error_distribution["UNCLASSIFIED"] = unclassified_errors
    sla_results = [
        PerformanceSlaResult.model_validate(item).model_dump(mode="json")
        for item in performance_run.sla_results or []
    ]
    verdict = _analysis_verdict(sla_results)
    trend_summary = _trend_summary(metrics.trends)
    snapshot: dict[str, Any] = {
        "schema_version": 1,
        "run_id": performance_run.run_id,
        "status": performance_run.status,
        "config": {
            key: performance_run.config_snapshot.get(key) for key in _ANALYSIS_CONFIG_FIELDS
        },
        "metrics": metrics_payload,
        "trend_summary": trend_summary,
        "error_distribution": dict(sorted(error_distribution.items())),
        "sla_results": sla_results,
        "sla_verdict": verdict,
    }
    encoded = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > _MAX_ANALYSIS_SNAPSHOT_BYTES:
        raise ResourceConflictError("性能分析来源快照超过大小限制")
    metric_values: dict[str, float] = {
        key: float(value)
        for key, value in metrics_payload.items()
        if isinstance(value, int | float) and not isinstance(value, bool)
    }
    metric_values.update(
        {
            f"trend.{key}": float(value)
            for key, value in trend_summary.items()
            if isinstance(value, int | float)
        }
    )
    metric_values.update(
        {f"error.{key}": float(value) for key, value in error_distribution.items()}
    )
    return snapshot, hashlib.sha256(encoded).hexdigest(), len(encoded), metric_values, verdict


def _performance_analysis_errors(
    value: Any, *, metric_values: dict[str, float], expected_verdict: str
) -> list[str]:
    try:
        parsed = PerformanceAnalysisResult.model_validate(value)
    except (ValidationError, TypeError, ValueError):
        return ["$: 性能分析结果不符合安全结构"]
    errors: list[str] = []
    if parsed.verdict != expected_verdict:
        errors.append("$.verdict: 必须与平台确定性 SLA 结论完全一致")
    seen: set[tuple[str, str]] = set()
    for index, finding in enumerate(parsed.findings):
        expected = metric_values.get(finding.metric)
        if expected is None:
            errors.append(f"$.findings[{index}].metric: 只能引用来源快照中的指标")
        elif not math.isclose(finding.observed, expected, rel_tol=1e-9, abs_tol=1e-6):
            errors.append(f"$.findings[{index}].observed: 必须等于来源快照中的指标值")
        identity = (finding.category, finding.metric)
        if identity in seen:
            errors.append(f"$.findings[{index}]: 同类指标发现不能重复")
        seen.add(identity)
    allowed_metrics = set(metric_values)
    for index, hypothesis in enumerate(parsed.bottleneck_hypotheses):
        if not set(hypothesis.evidence_metrics).issubset(allowed_metrics):
            errors.append(f"$.bottleneck_hypotheses[{index}].evidence_metrics: 包含来源外指标")
    return errors


def _validate_analysis_prompt(session: Session, prompt_id: int, project_id: int) -> tuple[int, int]:
    prompt = session.get(PromptDefinition, prompt_id)
    if prompt is None or not prompt.enabled:
        raise ResourceNotFoundError("性能分析 Prompt 不存在或已停用")
    if prompt.task_type != AiTaskType.PERFORMANCE_ANALYSIS.value:
        raise ResourceConflictError("Prompt 与性能分析任务类型不匹配")
    prompt = resolve_effective_prompt(session, project_id, prompt)
    if not prompt.enabled or prompt.task_type != AiTaskType.PERFORMANCE_ANALYSIS.value:
        raise ResourceConflictError("项目性能分析 Prompt 不可用")
    if prompt.current_version_id is None:
        raise ResourceConflictError("性能分析 Prompt 没有当前版本")
    version = session.get(PromptVersion, prompt.current_version_id)
    if version is None or version.output_schema_id is None:
        raise ResourceConflictError("性能分析 Prompt 必须绑定 Output Schema")
    schema = session.get(OutputSchema, version.output_schema_id)
    if schema is None or not schema.enabled:
        raise ResourceConflictError("性能分析 Output Schema 不存在或已停用")
    return version.id, schema.id


def _performance_analysis_response(
    analysis: PerformanceAnalysis,
) -> PerformanceAnalysisResponse:
    structured = (
        PerformanceAnalysisResult.model_validate(analysis.structured_result)
        if analysis.structured_result
        else None
    )
    return PerformanceAnalysisResponse(
        id=analysis.id,
        project_id=analysis.project_id,
        run_id=analysis.run_id,
        status=analysis.status,
        ai_call_id=analysis.ai_call_id,
        actual_model=analysis.actual_model,
        prompt_version_id=analysis.prompt_version_id,
        output_schema_id=analysis.output_schema_id,
        fallback_used=analysis.fallback_used,
        repair_used=analysis.repair_used,
        source_snapshot_sha256=analysis.source_snapshot_sha256,
        source_snapshot_size=analysis.source_snapshot_size,
        source_sla_verdict=analysis.source_sla_verdict,
        structured_result=structured,
        created_by=analysis.created_by,
        created_at=analysis.created_at,
    )


def _delete_analysis_placeholder(session: Session, analysis_id: int) -> None:
    try:
        analysis = session.get(PerformanceAnalysis, analysis_id)
        if analysis is not None:
            session.delete(analysis)
            session.commit()
    except Exception:
        session.rollback()


def generate_performance_analysis(
    session: Session,
    user: CurrentUser,
    run_id: str,
    payload: PerformanceAnalysisCreateRequest,
) -> PerformanceAnalysisResponse:
    performance_run = session.get(PerformanceRun, run_id)
    if performance_run is None:
        raise ResourceNotFoundError("性能 Run 不存在")
    project = get_project(session, user, performance_run.project_id)
    ensure_project_writable(session, project, user)
    prompt_version_id, output_schema_id = _validate_analysis_prompt(
        session, payload.prompt_id, performance_run.project_id
    )
    snapshot, digest, size, metric_values, verdict = _analysis_source_snapshot(performance_run)
    existing = session.scalar(
        select(PerformanceAnalysis)
        .where(
            PerformanceAnalysis.run_id == run_id,
            PerformanceAnalysis.status == "DRAFT",
        )
        .with_for_update()
    )
    if existing is not None:
        if existing.created_at >= utc_now_naive() - timedelta(minutes=10):
            raise ResourceConflictError("该性能 Run 已有进行中的 AI 分析")
        session.delete(existing)
        session.flush()
    placeholder = PerformanceAnalysis(
        project_id=performance_run.project_id,
        run_id=run_id,
        prompt_version_id=prompt_version_id,
        output_schema_id=output_schema_id,
        status="DRAFT",
        draft_key="DRAFT",
        source_snapshot=snapshot,
        source_snapshot_sha256=digest,
        source_snapshot_size=size,
        source_sla_verdict=verdict,
        additional_instructions=payload.additional_instructions,
        created_by=user.id,
    )
    session.add(placeholder)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ResourceConflictError("该性能 Run 已有进行中的 AI 分析") from exc
    session.refresh(placeholder)

    service_constraints = json.dumps(
        {
            "expected_verdict": verdict,
            "allowed_metric_values": metric_values,
            "untrusted_user_instructions": payload.additional_instructions or "无",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        ai_result = generate(
            session,
            user,
            AiGenerateRequest(
                project_id=performance_run.project_id,
                task_type=AiTaskType.PERFORMANCE_ANALYSIS,
                prompt_id=payload.prompt_id,
                variables={
                    "source_snapshot": json.dumps(
                        snapshot,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "additional_instructions": service_constraints,
                },
                entity_type="PERFORMANCE_ANALYSIS",
                entity_id=digest,
            ),
            result_validator=lambda value: _performance_analysis_errors(
                value,
                metric_values=metric_values,
                expected_verdict=verdict,
            ),
            timeout_seconds=180,
        )
        call = session.get(AiCallLog, ai_result.ai_call_id)
        if (
            not ai_result.success
            or call is None
            or not call.success
            or call.project_id != performance_run.project_id
            or call.task_type != AiTaskType.PERFORMANCE_ANALYSIS.value
            or call.entity_type != "PERFORMANCE_ANALYSIS"
            or call.entity_id != digest
            or call.prompt_version_id != prompt_version_id
            or call.output_schema_id != output_schema_id
        ):
            raise ResourceConflictError("AI 性能分析调用记录不可用")
        parsed = PerformanceAnalysisResult.model_validate(ai_result.parsed_result)
        errors = _performance_analysis_errors(
            parsed.model_dump(mode="json"),
            metric_values=metric_values,
            expected_verdict=verdict,
        )
        if errors:
            raise ResourceConflictError("AI 性能分析结果与原始指标不一致")
    except (AppError, ValidationError, TypeError, ValueError) as exc:
        _delete_analysis_placeholder(session, placeholder.id)
        if isinstance(exc, ResourceConflictError):
            raise
        raise ResourceConflictError("AI 性能分析输出不符合安全结构") from exc
    except Exception as exc:
        _delete_analysis_placeholder(session, placeholder.id)
        raise ResourceConflictError("AI 性能分析生成失败") from exc

    locked = session.scalar(
        select(PerformanceAnalysis)
        .where(PerformanceAnalysis.id == placeholder.id)
        .with_for_update()
    )
    if locked is None or locked.status != "DRAFT":
        _delete_analysis_placeholder(session, placeholder.id)
        raise ResourceConflictError("性能分析已被其他操作处理")
    locked.ai_call_id = call.id
    locked.structured_result = parsed.model_dump(mode="json")
    locked.actual_model = call.actual_model
    locked.fallback_used = call.fallback_used
    locked.repair_used = call.repair_used
    locked.confidence = parsed.confidence
    locked.needs_human_review = parsed.needs_human_review
    locked.status = "COMPLETED"
    locked.draft_key = None
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        _delete_analysis_placeholder(session, placeholder.id)
        raise ResourceConflictError("性能分析保存冲突") from exc
    session.refresh(locked)
    return _performance_analysis_response(locked)


def list_performance_analyses(
    session: Session, user: CurrentUser, run_id: str
) -> PerformanceAnalysisListResponse:
    performance_run = session.get(PerformanceRun, run_id)
    if performance_run is None:
        raise ResourceNotFoundError("性能 Run 不存在")
    get_project(session, user, performance_run.project_id)
    analyses = list(
        session.scalars(
            select(PerformanceAnalysis)
            .where(
                PerformanceAnalysis.run_id == run_id,
                PerformanceAnalysis.project_id == performance_run.project_id,
                PerformanceAnalysis.status == "COMPLETED",
            )
            .order_by(PerformanceAnalysis.created_at.desc(), PerformanceAnalysis.id.desc())
        ).all()
    )
    return PerformanceAnalysisListResponse(
        items=[_performance_analysis_response(item) for item in analyses],
        total=len(analyses),
    )
