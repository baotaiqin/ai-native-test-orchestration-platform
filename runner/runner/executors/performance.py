"""Bounded performance runtime for API, Scenario and streaming targets."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, replace
from typing import Any

from runner.errors import ExecutionError, TargetExecutionError, TargetTimeoutError
from runner.executors.api import ApiExecutor, ApiRequestTemplate
from runner.executors.jmeter import JMeterExecutor
from runner.executors.streaming import StreamingApiExecutor, estimate_request_bytes


@dataclass(frozen=True)
class PerformanceSample:
    duration_ms: int
    status_code: int | None = None
    bytes_received: int = 0
    bytes_sent: int = 0
    started_offset_ms: int = 0
    error_type: str | None = None
    ttft_ms: int | None = None
    stream_duration_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    chunk_count: int | None = None
    max_chunk_gap_ms: int | None = None
    stream_completed: bool | None = None

    def to_wire(self) -> dict[str, object]:
        value: dict[str, object] = {
            "duration_ms": self.duration_ms,
            "bytes_received": self.bytes_received,
            "bytes_sent": self.bytes_sent,
            "started_offset_ms": self.started_offset_ms,
        }
        for name in (
            "ttft_ms",
            "stream_duration_ms",
            "input_tokens",
            "output_tokens",
            "chunk_count",
            "max_chunk_gap_ms",
            "stream_completed",
        ):
            current = getattr(self, name)
            if current is not None:
                value[name] = current
        if self.status_code is not None:
            value["status_code"] = self.status_code
        else:
            value["error_type"] = self.error_type or "EXECUTION_ERROR"
        return value


@dataclass(frozen=True)
class PerformanceExecutionResult:
    wall_duration_ms: int
    samples: tuple[PerformanceSample, ...]

    def to_worker_wire(self) -> dict[str, object]:
        return {
            "wall_duration_ms": self.wall_duration_ms,
            "samples": [sample.to_wire() for sample in self.samples],
        }


@dataclass(frozen=True)
class _OneResult:
    sample: PerformanceSample
    cleanup_task: Any | None = None


class PerformanceExecutor:
    def __init__(
        self,
        api_executor: ApiExecutor,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_executor = api_executor
        self._clock = clock
        self._sleeper = sleeper

    def execute(
        self,
        request: ApiRequestTemplate | None,
        *,
        concurrency: int,
        iterations: int,
        warmup_iterations: int = 0,
        load_mode: str = "FIXED_ITERATIONS",
        target_rps: float | None = None,
        duration_seconds: int | None = None,
        step_stages: tuple[Mapping[str, int], ...] = (),
        target_type: str = "API_CASE",
        engine: str = "PYTHON_HTTP",
        target_plan: Any | None = None,
        cleanup_mode: str = "NONE",
        stream_config: Mapping[str, Any] | None = None,
        request_timeout_ms: int = 30_000,
        total_timeout_ms: int = 900_000,
        should_cancel: Callable[[], bool] | None = None,
    ) -> PerformanceExecutionResult:
        self._validate(
            request,
            concurrency=concurrency,
            iterations=iterations,
            warmup_iterations=warmup_iterations,
            load_mode=load_mode,
            target_rps=target_rps,
            duration_seconds=duration_seconds,
            step_stages=step_stages,
            target_type=target_type,
            engine=engine,
            target_plan=target_plan,
            cleanup_mode=cleanup_mode,
        )
        if engine == "JMETER":
            assert request is not None
            result = JMeterExecutor().execute(
                request,
                concurrency=concurrency,
                iterations=iterations,
                warmup_iterations=warmup_iterations,
                load_mode=load_mode,
                target_rps=target_rps,
                request_timeout_ms=request_timeout_ms,
                total_timeout_ms=total_timeout_ms,
            )
            return PerformanceExecutionResult(
                result.wall_duration_ms,
                tuple(PerformanceSample(**dict(sample)) for sample in result.samples),
            )
        warmup_base = self._clock()
        self._execute_iterations(
            lambda: self._execute_one(
                request,
                base=warmup_base,
                target_type=target_type,
                engine=engine,
                target_plan=target_plan,
                cleanup_mode="RESOURCE_CLEANUP",
                stream_config=stream_config or {},
            ),
            concurrency=min(concurrency, max(1, warmup_iterations)),
            iterations=warmup_iterations,
            collect=False,
            should_cancel=should_cancel,
        )
        started = self._clock()

        def execute_one() -> _OneResult:
            return self._execute_one(
                request,
                base=started,
                target_type=target_type,
                engine=engine,
                target_plan=target_plan,
                cleanup_mode=cleanup_mode,
                stream_config=stream_config or {},
            )

        if load_mode == "FIXED_RPS":
            results = self._execute_fixed_rps(
                execute_one,
                concurrency=concurrency,
                iterations=iterations,
                target_rps=target_rps or 1,
                should_cancel=should_cancel,
            )
        elif load_mode == "FIXED_CONCURRENCY":
            results = self._execute_duration(
                execute_one,
                concurrency=concurrency,
                duration_seconds=duration_seconds or 1,
                maximum=iterations,
                should_cancel=should_cancel,
            )
        elif load_mode == "STEP_LOAD":
            results = []
            total_stage_duration = sum(stage["duration_seconds"] for stage in step_stages)
            for index, stage in enumerate(step_stages):
                remaining = iterations - len(results)
                if index == len(step_stages) - 1:
                    stage_maximum = remaining
                else:
                    stage_maximum = min(
                        remaining,
                        round(iterations * stage["duration_seconds"] / total_stage_duration),
                    )
                results.extend(
                    self._execute_duration(
                        execute_one,
                        concurrency=stage["concurrency"],
                        duration_seconds=stage["duration_seconds"],
                        maximum=stage_maximum,
                        should_cancel=should_cancel,
                    )
                )
        else:
            results = self._execute_iterations(
                execute_one,
                concurrency=concurrency,
                iterations=iterations,
                collect=True,
                should_cancel=should_cancel,
            )
        wall_duration_ms = max(1, int((self._clock() - started) * 1000))
        if cleanup_mode == "BATCH_CLEANUP":
            for result in reversed(results):
                if result.cleanup_task is not None:
                    self._execute_cleanup(result.cleanup_task)
        return PerformanceExecutionResult(wall_duration_ms, tuple(item.sample for item in results))

    def _validate(
        self,
        request: ApiRequestTemplate | None,
        **values: Any,
    ) -> None:
        concurrency = values["concurrency"]
        iterations = values["iterations"]
        load_mode = values["load_mode"]
        target_rps = values["target_rps"]
        duration_seconds = values["duration_seconds"]
        step_stages = values["step_stages"]
        if not 1 <= concurrency <= 100 or not 1 <= iterations <= 10_000:
            raise ExecutionError("性能执行参数无效", error_type="PERFORMANCE_CONFIG_INVALID")
        if not 0 <= values["warmup_iterations"] <= 1_000:
            raise ExecutionError("Warm Up 参数无效", error_type="PERFORMANCE_CONFIG_INVALID")
        if values["target_type"] not in {"API_CASE", "SCENARIO"}:
            raise ExecutionError("性能目标类型无效", error_type="PERFORMANCE_CONFIG_INVALID")
        if values["target_type"] == "API_CASE" and request is None:
            raise ExecutionError("API 性能目标缺少请求", error_type="PERFORMANCE_CONFIG_INVALID")
        if values["target_type"] == "SCENARIO" and values["target_plan"] is None:
            raise ExecutionError(
                "Scenario 性能目标缺少计划", error_type="PERFORMANCE_CONFIG_INVALID"
            )
        if values["engine"] not in {"PYTHON_HTTP", "PYTHON_STREAM", "JMETER"}:
            raise ExecutionError("性能引擎无效", error_type="PERFORMANCE_CONFIG_INVALID")
        if values["cleanup_mode"] not in {"NONE", "RESOURCE_CLEANUP", "BATCH_CLEANUP"}:
            raise ExecutionError("Cleanup 模式无效", error_type="PERFORMANCE_CONFIG_INVALID")
        if load_mode == "FIXED_ITERATIONS":
            valid = (
                iterations >= concurrency
                and target_rps is None
                and duration_seconds is None
                and not step_stages
            )
        elif load_mode == "FIXED_RPS":
            valid = (
                isinstance(target_rps, int | float)
                and not isinstance(target_rps, bool)
                and 0 < target_rps <= 10_000
                and type(duration_seconds) is int
                and 1 <= duration_seconds <= 3_600
                and iterations == math.ceil(target_rps * duration_seconds)
                and not step_stages
            )
        elif load_mode == "FIXED_CONCURRENCY":
            valid = (
                target_rps is None
                and type(duration_seconds) is int
                and 1 <= duration_seconds <= 3_600
                and not step_stages
            )
        elif load_mode == "STEP_LOAD":
            valid = (
                target_rps is None
                and duration_seconds is None
                and 1 <= len(step_stages) <= 20
                and sum(stage["duration_seconds"] for stage in step_stages) <= 3_600
                and all(
                    1 <= stage["concurrency"] <= 100 and 1 <= stage["duration_seconds"] <= 3_600
                    for stage in step_stages
                )
            )
        else:
            valid = False
        if not valid:
            raise ExecutionError("性能负载参数无效", error_type="PERFORMANCE_CONFIG_INVALID")

    def _execute_iterations(
        self,
        execute_one: Callable[[], _OneResult],
        *,
        concurrency: int,
        iterations: int,
        collect: bool,
        should_cancel: Callable[[], bool] | None,
    ) -> list[_OneResult]:
        results: list[_OneResult] = []
        completed = 0
        while completed < iterations:
            self._check_cancel(should_cancel)
            batch_size = min(concurrency, iterations - completed)
            with ThreadPoolExecutor(max_workers=batch_size, thread_name_prefix="perf") as pool:
                futures = [pool.submit(execute_one) for _ in range(batch_size)]
                for future in as_completed(futures):
                    result = future.result()
                    if collect:
                        results.append(result)
            completed += batch_size
        return results

    def _execute_fixed_rps(
        self,
        execute_one: Callable[[], _OneResult],
        *,
        concurrency: int,
        iterations: int,
        target_rps: float,
        should_cancel: Callable[[], bool] | None,
    ) -> list[_OneResult]:
        results: list[_OneResult] = []
        pending: set[Future[_OneResult]] = set()
        scheduled = 0
        started = self._clock()
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="perf-rps") as pool:
            while scheduled < iterations or pending:
                self._check_cancel(should_cancel)
                now = self._clock()
                while scheduled < iterations and len(pending) < concurrency:
                    due_at = started + scheduled / target_rps
                    if now < due_at:
                        break
                    pending.add(pool.submit(execute_one))
                    scheduled += 1
                    now = self._clock()
                if not pending:
                    due_at = started + scheduled / target_rps
                    self._sleeper(min(0.05, max(0.0, due_at - self._clock())))
                    continue
                timeout = 0.05
                if scheduled < iterations and len(pending) < concurrency:
                    timeout = min(
                        timeout,
                        max(0.0, started + scheduled / target_rps - self._clock()),
                    )
                completed, pending = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
                results.extend(future.result() for future in completed)
        return results

    def _execute_duration(
        self,
        execute_one: Callable[[], _OneResult],
        *,
        concurrency: int,
        duration_seconds: int,
        maximum: int,
        should_cancel: Callable[[], bool] | None,
    ) -> list[_OneResult]:
        results: list[_OneResult] = []
        pending: set[Future[_OneResult]] = set()
        deadline = self._clock() + duration_seconds
        scheduled = 0
        with ThreadPoolExecutor(
            max_workers=concurrency, thread_name_prefix="perf-duration"
        ) as pool:
            while (self._clock() < deadline and scheduled < maximum) or pending:
                self._check_cancel(should_cancel)
                while (
                    self._clock() < deadline and scheduled < maximum and len(pending) < concurrency
                ):
                    pending.add(pool.submit(execute_one))
                    scheduled += 1
                if not pending:
                    break
                completed, pending = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
                results.extend(future.result() for future in completed)
        while self._clock() < deadline:
            self._check_cancel(should_cancel)
            self._sleeper(min(0.05, max(0.0, deadline - self._clock())))
        return results

    def _execute_one(
        self,
        request: ApiRequestTemplate | None,
        *,
        base: float,
        target_type: str,
        engine: str,
        target_plan: Any | None,
        cleanup_mode: str,
        stream_config: Mapping[str, Any],
    ) -> _OneResult:
        started = self._clock()
        offset_ms = max(0, int((started - base) * 1000))
        sent = estimate_request_bytes(request) if request is not None else 0
        try:
            if engine == "PYTHON_STREAM":
                assert request is not None
                result = StreamingApiExecutor(self._api_executor, clock=self._clock).execute(
                    request, stream_config
                )
                return _OneResult(
                    PerformanceSample(
                        duration_ms=result.duration_ms,
                        status_code=result.status_code if result.completed else None,
                        error_type=None if result.completed else "STREAM_INTERRUPTED",
                        bytes_received=result.bytes_received,
                        bytes_sent=sent,
                        started_offset_ms=offset_ms,
                        ttft_ms=result.ttft_ms,
                        stream_duration_ms=result.stream_duration_ms,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        chunk_count=result.chunk_count,
                        max_chunk_gap_ms=result.max_chunk_gap_ms,
                        stream_completed=result.completed,
                    )
                )
            if target_type == "SCENARIO":
                from runner.scenario import ScenarioCleanupTask, ScenarioExecutor

                plan = self._scenario_plan(target_plan, cleanup_mode)
                result = ScenarioExecutor(
                    plan,
                    api_executor=self._api_executor,
                    defer_cleanup=cleanup_mode == "BATCH_CLEANUP",
                ).execute()
                duration = max(0, int((self._clock() - started) * 1000))
                cleanup_task = (
                    ScenarioCleanupTask(
                        plan,
                        result.cleanup_context or {},
                        "SUCCESS" if result.outcome == "SUCCESS" else "FAILURE",
                    )
                    if cleanup_mode == "BATCH_CLEANUP" and result.cleanup_context is not None
                    else None
                )
                return _OneResult(
                    PerformanceSample(
                        duration_ms=duration,
                        status_code=200 if result.outcome == "SUCCESS" else None,
                        error_type=(
                            None
                            if result.outcome == "SUCCESS"
                            else result.error_type or "SCENARIO_FAILED"
                        ),
                        bytes_sent=sent,
                        started_offset_ms=offset_ms,
                    ),
                    cleanup_task,
                )
            assert request is not None
            if target_plan is None:
                response = self._api_executor.execute(request)
                return _OneResult(
                    PerformanceSample(
                        duration_ms=response.elapsed_ms,
                        status_code=response.status_code,
                        bytes_received=response.body_size,
                        bytes_sent=sent,
                        started_offset_ms=offset_ms,
                    )
                )
            plan = self._api_plan(target_plan, cleanup_mode)
            from runner.api_pipeline import ApiCasePipelineExecutor, ApiPipelineCleanupTask

            result = ApiCasePipelineExecutor(plan, api_executor=self._api_executor).execute()
            cleanup_task = (
                ApiPipelineCleanupTask(
                    plan,
                    result.cleanup_context or {},
                    result.resources,
                    "SUCCESS" if result.response.status_code < 400 else "FAILURE",
                )
                if cleanup_mode in {"RESOURCE_CLEANUP", "BATCH_CLEANUP"}
                and result.cleanup_pending
                else None
            )
            if cleanup_mode == "RESOURCE_CLEANUP" and cleanup_task is not None:
                self._execute_cleanup(cleanup_task)
                cleanup_task = None
            return _OneResult(
                PerformanceSample(
                    duration_ms=result.response.elapsed_ms,
                    status_code=result.response.status_code,
                    bytes_received=result.response.body_size,
                    bytes_sent=sent,
                    started_offset_ms=offset_ms,
                ),
                cleanup_task,
            )
        except TargetTimeoutError:
            return _OneResult(self._error_sample(started, offset_ms, sent, "TARGET_TIMEOUT"))
        except TargetExecutionError as exc:
            return _OneResult(
                self._error_sample(
                    started, offset_ms, sent, exc.error_type or "TARGET_NETWORK_ERROR"
                )
            )
        except ExecutionError as exc:
            return _OneResult(
                self._error_sample(started, offset_ms, sent, exc.error_type or "EXECUTION_ERROR")
            )

    @staticmethod
    def _api_plan(plan: Any, cleanup_mode: str) -> Any:
        if cleanup_mode == "NONE":
            return replace(plan, cleanups=(), defer_cleanup_until_assertions=False)
        return replace(plan, defer_cleanup_until_assertions=True)

    @staticmethod
    def _scenario_plan(plan: Any, cleanup_mode: str) -> Any:
        if cleanup_mode != "NONE":
            return plan
        return replace(
            plan,
            nodes=tuple(
                node for node in plan.nodes if node.node_type not in {"API_CLEANUP", "SQL_CLEANUP"}
            ),
        )

    def _execute_cleanup(self, task: Any) -> None:
        from runner.api_pipeline import ApiCasePipelineExecutor, ApiPipelineCleanupTask
        from runner.scenario import ScenarioExecutor

        if isinstance(task, ApiPipelineCleanupTask):
            ApiCasePipelineExecutor.execute_cleanup_task(task, api_executor=self._api_executor)
        else:
            result = ScenarioExecutor.execute_cleanup_task(task, api_executor=self._api_executor)
            if result.status != "SUCCESS":
                raise ExecutionError(
                    "Scenario Cleanup 执行失败",
                    error_type=result.error_type or "CLEANUP_EXECUTION_ERROR",
                )

    def _error_sample(
        self, started: float, offset_ms: int, sent: int, error_type: str
    ) -> PerformanceSample:
        return PerformanceSample(
            duration_ms=max(0, int((self._clock() - started) * 1000)),
            bytes_sent=sent,
            started_offset_ms=offset_ms,
            error_type=error_type,
        )

    @staticmethod
    def _check_cancel(should_cancel: Callable[[], bool] | None) -> None:
        if should_cancel is not None and should_cancel():
            raise ExecutionError("性能执行已取消", error_type="CANCEL_REQUESTED")
