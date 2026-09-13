"""RabbitMQ 单次/持续消费：严格校验、claim 后确认。"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from enum import StrEnum
from inspect import signature
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from typing import Any

from runner.envelope import (
    RunTaskEnvelopeV1,
    WebExplorationTaskEnvelopeV1,
    WebRecordingTaskEnvelopeV1,
    parse_task_envelope,
    parse_task_or_recording_envelope,
)
from runner.errors import (
    AuthenticationError,
    EnvelopeError,
    ExecutionError,
    ProtocolError,
    RunnerError,
    TargetExecutionError,
    TargetTimeoutError,
    TransportError,
)
from runner.evidence import (
    ValidatedEvidenceArtifact,
    canonical_json,
    validate_manifest,
)
from runner.executors.api import (
    ApiExecutionResult,
    RetryPolicy,
    validate_retry_policy,
)
from runner.executors.base import BaseExecutor
from runner.executors.performance import PerformanceExecutor
from runner.executors.web_exploration import WebExplorationExecutor
from runner.isolation import (
    FORCE_STOPPED_ERROR_MESSAGE,
    FORCE_STOPPED_ERROR_TYPE,
    PIPE_POLL_SECONDS,
    TOTAL_TIMEOUT_ERROR_MESSAGE,
    TOTAL_TIMEOUT_ERROR_TYPE,
    IsolatedOutcome,
    TaskProcess,
    spawn_scenario_cleanup_task_process,
    spawn_scenario_task_process,
    spawn_web_recording_task_process,
    spawn_web_task_process,
)
from runner.models import (
    ExecutionCancellationResult,
    ExecutionCompleteResult,
    ExecutionStartResult,
    PerformanceExecutionPlanResult,
    RunnerIdentity,
    ScenarioExecutionCompleteResult,
    ScenarioExecutionPlanResult,
    ScenarioExecutionStartResult,
    WebExecutionCompleteResult,
    WebExecutionPlanResult,
    WebExecutionStartResult,
    WebExplorationCompleteResult,
    WebExplorationExecutionPlanResult,
    WebRecordingCompleteResult,
    WebRecordingExecutionPlanResult,
)
from runner.performance_store import PerformanceResultStore
from runner.protocol import RunnerClient
from runner.redaction import redact_text
from runner.scenario import ScenarioCleanupTask
from runner.slots import SlotLease, SlotState
from runner.topology import RunnerTaskTopology, declare_runner_topology

CANCEL_REQUESTED_ERROR_MESSAGE = "Runner 检测到取消请求"
CANCEL_UNRESPONSIVE_ERROR_MESSAGE = (
    "取消已请求，但隔离进程未在限定窗口内响应，已终止；Cleanup 可能未完成"
)
WEB_EVIDENCE_ERROR_TYPE = "WEB_EVIDENCE_ERROR"
WEB_EVIDENCE_ERROR_MESSAGE = "Web 执行证据处理失败"


def _deadline_from(started_at: datetime | None, total_timeout_ms: int) -> float | None:
    """Run 总超时截止时间（wall clock 秒）；没有开始时间时无法判定则不限制。"""

    if started_at is None:
        return None
    return started_at.timestamp() + total_timeout_ms / 1000.0


def _web_terminal_traces(plan: WebExecutionPlanResult, status: str) -> list[dict[str, object]]:
    """Build a complete, body-free trace for a parent-side terminal boundary."""

    traces: list[dict[str, object]] = []
    first = True
    for index in range(1, len(plan.actions) + 1):
        node_status = status if first else "SKIPPED"
        traces.append(
            {
                "node_id": f"action_{index}",
                "status": node_status,
                "duration_ms": 0,
                "error_type": None,
                "error_message": None,
            }
        )
        first = False
    for index in range(1, len(plan.assertions) + 1):
        node_status = status if first else "SKIPPED"
        traces.append(
            {
                "node_id": f"assertion_{index}",
                "status": node_status,
                "duration_ms": 0,
                "error_type": None,
                "error_message": None,
            }
        )
        first = False
    return traces


def _call_web_isolation_factory(
    factory: Callable[..., TaskProcess], plan: WebExecutionPlanResult, evidence_dir: str
) -> TaskProcess:
    """Pass the parent-owned directory while preserving one-argument test factories."""

    try:
        parameters = tuple(signature(factory).parameters.values())
    except (TypeError, ValueError):
        return factory(plan, evidence_dir)
    accepts_varargs = any(parameter.kind is parameter.VAR_POSITIONAL for parameter in parameters)
    positional = tuple(
        parameter
        for parameter in parameters
        if parameter.kind in {parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD}
    )
    if accepts_varargs or len(positional) >= 2:
        return factory(plan, evidence_dir)
    return factory(plan)


class ConsumeOutcome(StrEnum):
    EMPTY = "EMPTY"
    ACKED = "ACKED"
    REQUEUED = "REQUEUED"
    REJECTED = "REJECTED"
    STOPPED_AUTH = "STOPPED_AUTH"


class RunnerTaskConsumer:
    """提供兼容的 claim-only 消费，以及 API_CASE execute-once。"""

    def __init__(
        self,
        channel: object,
        client: RunnerClient,
        identity: RunnerIdentity,
        slots: dict[str, int],
        executor: BaseExecutor | None = None,
        stop_event: Event | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        slot_state: SlotState | None = None,
        isolation_factory: Callable[[object], TaskProcess] | None = None,
        scenario_isolation_factory: Callable[[object], TaskProcess] | None = None,
        scenario_cleanup_isolation_factory: Callable[[object], TaskProcess] | None = None,
        performance_isolation_factory: Callable[[object], TaskProcess] | None = None,
        performance_result_store: PerformanceResultStore | None = None,
        web_isolation_factory: Callable[..., TaskProcess] | None = None,
        web_recording_isolation_factory: Callable[[object], TaskProcess] | None = None,
        force_stop_poll_seconds: float = 1.0,
        scenario_cancel_wait_seconds: float = 5.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not 0.1 <= force_stop_poll_seconds <= 30:
            raise RunnerError("Force Stop 轮询间隔必须在 0.1 到 30 秒之间")
        if not 0.1 <= scenario_cancel_wait_seconds <= 60:
            raise RunnerError("Scenario 取消等待时间必须在 0.1 到 60 秒之间")
        self.channel = channel
        self.client = client
        self.identity = identity
        self.slots = slots
        self.executor = executor
        self._stop_event = stop_event
        self._sleeper = sleeper
        self._slot_state = slot_state
        self._isolation_factory = isolation_factory
        self._scenario_isolation_factory = scenario_isolation_factory
        self._scenario_cleanup_isolation_factory = scenario_cleanup_isolation_factory
        self._performance_isolation_factory = performance_isolation_factory
        self._performance_result_store = performance_result_store
        self._web_isolation_factory = web_isolation_factory
        self._web_recording_isolation_factory = web_recording_isolation_factory
        self._force_stop_poll_seconds = force_stop_poll_seconds
        self._scenario_cancel_wait_seconds = scenario_cancel_wait_seconds
        self._clock = clock
        self._performance_result_cache: dict[tuple[str, str], IsolatedOutcome] = {}
        self.topology: RunnerTaskTopology | None = None

    def set_stop_event(self, stop_event: Event) -> None:
        """由前台 Worker 注入停止事件，支持执行中的 Ctrl+C 收口。"""

        self._stop_event = stop_event

    @property
    def queue(self) -> str:
        if self.topology is None:
            raise RunnerError("Runner RabbitMQ topology 尚未配置")
        return self.topology.queue

    def configure(self) -> RunnerTaskTopology:
        if self.topology is not None:
            return self.topology
        self.topology = declare_runner_topology(
            self.channel,
            self.identity.runner_id,
            self.slots,
        )
        self.channel.basic_qos(prefetch_count=1)
        return self.topology

    def consume_once(self) -> ConsumeOutcome:
        topology = self.configure()
        return self.consume_once_configured(topology)

    def consume_once_configured(self, topology: RunnerTaskTopology | None = None) -> ConsumeOutcome:
        """在 topology 已配置后非阻塞地消费并 claim 一条消息。"""

        active_topology = topology or self._require_topology()
        delivery = self._basic_get(active_topology)
        if delivery is None:
            return ConsumeOutcome.EMPTY
        delivery_tag, body = delivery
        return self.process_delivery(delivery_tag, body)

    def _basic_get(self, topology: RunnerTaskTopology) -> tuple[object, object] | None:
        delivery = self.channel.basic_get(queue=topology.queue, auto_ack=False)
        if not isinstance(delivery, tuple) or len(delivery) != 3:
            raise RunnerError("RabbitMQ basic_get 返回格式无效")
        method, _properties, body = delivery
        if method is None:
            return None
        delivery_tag = getattr(method, "delivery_tag", None)
        if delivery_tag is None:
            raise RunnerError("RabbitMQ delivery_tag 缺失")
        return delivery_tag, body

    def _require_topology(self) -> RunnerTaskTopology:
        if self.topology is None:
            raise RunnerError("Runner RabbitMQ topology 尚未配置")
        return self.topology

    def execute_once(self) -> ConsumeOutcome:
        """兼容入口：配置 topology 后执行一条 API_CASE。"""

        topology = self.configure()
        return self.execute_once_configured(topology)

    def execute_once_configured(self, topology: RunnerTaskTopology | None = None) -> ConsumeOutcome:
        """在 topology 已配置后非阻塞地执行一条 API_CASE。"""

        active_topology = topology or self._require_topology()
        delivery = self._basic_get(active_topology)
        if delivery is None:
            return ConsumeOutcome.EMPTY
        delivery_tag, body = delivery
        return self.process_execution_delivery(delivery_tag, body)

    def consume_forever(self) -> None:
        topology = self.configure()
        self.channel.basic_consume(
            queue=topology.queue,
            on_message_callback=self._on_message,
            auto_ack=False,
        )
        self.channel.start_consuming()

    def process_delivery(self, delivery_tag: object, body: object) -> ConsumeOutcome:
        try:
            envelope = parse_task_envelope(body, expected_runner_id=self.identity.runner_id)
        except EnvelopeError:
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        try:
            claim = self.client.claim(
                self.identity,
                envelope.run_id,
                envelope.message_id,
            )
        except AuthenticationError:
            self._reject(delivery_tag)
            self._stop_consuming()
            return ConsumeOutcome.STOPPED_AUTH
        except TransportError:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        except (ProtocolError, RunnerError):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        except Exception:
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        if claim.status == "CANCELLED":
            self.channel.basic_ack(delivery_tag=delivery_tag)
            return ConsumeOutcome.ACKED
        self.channel.basic_ack(delivery_tag=delivery_tag)
        return ConsumeOutcome.ACKED

    def process_execution_delivery(self, delivery_tag: object, body: object) -> ConsumeOutcome:
        try:
            envelope = parse_task_or_recording_envelope(
                body, expected_runner_id=self.identity.runner_id
            )
            if isinstance(envelope, WebRecordingTaskEnvelopeV1):
                expected_slot = "WEB"
            elif isinstance(envelope, WebExplorationTaskEnvelopeV1):
                expected_slot = "WEB"
            else:
                expected_slot = envelope.required_slot_type
                if envelope.required_slot_type != expected_slot:
                    raise EnvelopeError("任务信封 required_slot_type 与 run_type 不匹配")
                if envelope.run_type == "API_CASE" and self.executor is None:
                    raise RunnerError("API Executor 尚未配置")
                if envelope.run_type not in {"API_CASE", "SCENARIO", "WEB_CASE"}:
                    raise EnvelopeError("任务信封 run_type 不受当前执行入口支持")
        except EnvelopeError:
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        lease: SlotLease | None = None
        if self._slot_state is not None:
            lease = self._slot_state.try_acquire(expected_slot)
            if lease is None:
                self._nack(delivery_tag)
                return ConsumeOutcome.REQUEUED
        try:
            if isinstance(envelope, WebRecordingTaskEnvelopeV1):
                return self._process_web_recording_delivery(delivery_tag, envelope)
            if isinstance(envelope, WebExplorationTaskEnvelopeV1):
                return self._process_web_exploration_delivery(delivery_tag, envelope)
            # Performance is an execution mode identified by its dedicated slot.
            # A Scenario performance run still has run_type=SCENARIO, so mode must
            # win over the ordinary run-type dispatch here.
            if envelope.required_slot_type == "PERFORMANCE":
                return self._process_performance_delivery(delivery_tag, envelope)
            if envelope.run_type == "SCENARIO":
                return self._process_scenario_execution_delivery(delivery_tag, envelope)
            if envelope.run_type == "WEB_CASE":
                return self._process_web_execution_delivery(delivery_tag, envelope)
            return self._process_execution_delivery(delivery_tag, envelope)
        finally:
            if lease is not None:
                lease.release()

    def process_scenario_execution_delivery(
        self, delivery_tag: object, body: object
    ) -> ConsumeOutcome:
        """Explicit Scenario entry point; useful for configured integrations/tests."""

        return self.process_execution_delivery(delivery_tag, body)

    def _process_scenario_execution_delivery(
        self, delivery_tag: object, envelope: RunTaskEnvelopeV1
    ) -> ConsumeOutcome:
        try:
            claim = self.client.claim(self.identity, envelope.run_id, envelope.message_id)
            if claim.status == "CANCELLED":
                self.channel.basic_ack(delivery_tag=delivery_tag)
                return ConsumeOutcome.ACKED
            plan = self.client.get_scenario_execution_plan(
                self.identity, envelope.run_id, envelope.message_id, None
            )
            started = self.client.scenario_execution_start(
                self.identity, envelope.run_id, envelope.message_id, plan.case_run_id
            )
        except AuthenticationError:
            self._reject(delivery_tag)
            self._stop_consuming()
            return ConsumeOutcome.STOPPED_AUTH
        except TransportError:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        except (ProtocolError, RunnerError):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        except Exception:
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED

        if started.status == "CANCELLING":
            return self._complete_scenario_cancelled(delivery_tag, envelope, plan.case_run_id)
        control, control_failure = self._check_control(
            delivery_tag, envelope.run_id, envelope.message_id, plan.case_run_id
        )
        if control_failure is not None:
            return control_failure
        if control is not None:
            return self._complete_scenario_control(
                delivery_tag, envelope, plan.case_run_id, control
            )

        outcome, execution_failure = self._execute_scenario_isolated(
            delivery_tag, envelope, plan, started
        )
        if execution_failure is not None:
            return execution_failure
        if outcome is None:
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        control, control_failure = self._check_control(
            delivery_tag, envelope.run_id, envelope.message_id, plan.case_run_id
        )
        if control_failure is not None:
            return control_failure
        if control is not None:
            return self._complete_scenario_control(
                delivery_tag, envelope, plan.case_run_id, control
            )
        return self._complete_scenario_result(delivery_tag, envelope, plan, outcome, started)

    def _execute_scenario_isolated(
        self,
        delivery_tag: object,
        envelope: RunTaskEnvelopeV1,
        plan: ScenarioExecutionPlanResult,
        started: ScenarioExecutionStartResult,
    ) -> tuple[IsolatedOutcome | None, ConsumeOutcome | None]:
        factory = self._scenario_isolation_factory or spawn_scenario_task_process
        deadline = _deadline_from(started.started_at, plan.total_timeout_ms)
        process = factory(plan)
        try:
            process.start()
            last_force_check = 0.0
            cancel_requested = False
            cancel_deadline: float | None = None
            while process.is_alive():
                if process.poll(PIPE_POLL_SECONDS):
                    break
                now = self._clock()
                if deadline is not None and now >= deadline:
                    process.terminate()
                    process.join(timeout=5.0)
                    return IsolatedOutcome(kind="TOTAL_TIMEOUT"), None
                if cancel_requested:
                    if cancel_deadline is not None and now >= cancel_deadline:
                        process.terminate()
                        process.join(timeout=5.0)
                        return IsolatedOutcome(
                            kind="CANCEL_UNRESPONSIVE",
                            error_type="CANCEL_REQUESTED",
                            error_message=CANCEL_UNRESPONSIVE_ERROR_MESSAGE,
                        ), None
                    continue
                if now - last_force_check >= self._force_stop_poll_seconds:
                    last_force_check = now
                    control, failure = self._check_control(
                        delivery_tag,
                        envelope.run_id,
                        envelope.message_id,
                        plan.case_run_id,
                    )
                    if failure is not None:
                        process.terminate()
                        process.join(timeout=5.0)
                        return None, failure
                    if control == "FORCE_STOP":
                        process.terminate()
                        process.join(timeout=5.0)
                        return IsolatedOutcome(kind="FORCE_STOPPED"), None
                    if control == "CANCEL":
                        request_cancel = getattr(process, "request_cancel", None)
                        if not callable(request_cancel):
                            process.terminate()
                            process.join(timeout=5.0)
                            return IsolatedOutcome(
                                kind="CANCEL_UNRESPONSIVE",
                                error_type="CANCEL_REQUESTED",
                                error_message=CANCEL_UNRESPONSIVE_ERROR_MESSAGE,
                            ), None
                        try:
                            request_cancel()
                        except Exception:  # noqa: BLE001 - cancellation is fail-closed
                            process.terminate()
                            process.join(timeout=5.0)
                            return IsolatedOutcome(
                                kind="CANCEL_UNRESPONSIVE",
                                error_type="CANCEL_REQUESTED",
                                error_message=CANCEL_UNRESPONSIVE_ERROR_MESSAGE,
                            ), None
                        cancel_requested = True
                        cancel_deadline = now + self._scenario_cancel_wait_seconds
            process.poll(PIPE_POLL_SECONDS)
            outcome = process.outcome()
            if outcome is None:
                if cancel_requested:
                    return IsolatedOutcome(
                        kind="CANCEL_UNRESPONSIVE",
                        error_type="CANCEL_REQUESTED",
                        error_message=CANCEL_UNRESPONSIVE_ERROR_MESSAGE,
                    ), None
                if deadline is not None and self._clock() >= deadline:
                    return IsolatedOutcome(kind="TOTAL_TIMEOUT"), None
                return IsolatedOutcome(
                    kind="EXECUTION_ERROR",
                    error_type="SCENARIO_EXECUTOR_ERROR",
                    error_message="Scenario 隔离执行异常终止",
                ), None
            return outcome, None
        finally:
            process.close()

    def _complete_scenario_result(
        self,
        delivery_tag: object,
        envelope: RunTaskEnvelopeV1,
        plan: ScenarioExecutionPlanResult,
        outcome: IsolatedOutcome,
        started: ScenarioExecutionStartResult,
    ) -> ConsumeOutcome:
        evaluation_token: str | None = None
        if outcome.kind == "SCENARIO_RESULT" and isinstance(outcome.result, dict):
            result = outcome.result
            final_outcome = result.get("outcome")
            traces = result.get("traces")
            if final_outcome not in {"SUCCESS", "FAILED", "TIMEOUT", "CANCELLED"}:
                self._reject(delivery_tag)
                return ConsumeOutcome.REJECTED
            if not isinstance(traces, list):
                self._reject(delivery_tag)
                return ConsumeOutcome.REJECTED
            error_type = result.get("error_type")
            error_message = result.get("error_message")
            pending_ai = result.get("pending_ai", [])
            cleanup_context = result.get("cleanup_context")
            if pending_ai:
                if (
                    final_outcome != "SUCCESS"
                    or not isinstance(pending_ai, list)
                    or not isinstance(cleanup_context, Mapping)
                ):
                    self._reject(delivery_tag)
                    return ConsumeOutcome.REJECTED
                try:
                    evaluation = self.client.scenario_execution_evaluate(
                        self.identity,
                        envelope.run_id,
                        envelope.message_id,
                        plan.case_run_id,
                        pending_ai,
                    )
                except AuthenticationError:
                    self._reject(delivery_tag)
                    self._stop_consuming()
                    return ConsumeOutcome.STOPPED_AUTH
                except TransportError:
                    self._nack(delivery_tag)
                    return ConsumeOutcome.REQUEUED
                except (ProtocolError, RunnerError):
                    self._reject(delivery_tag)
                    return ConsumeOutcome.REJECTED
                except Exception:
                    self._nack(delivery_tag)
                    return ConsumeOutcome.REQUEUED
                status_map = {"PASS": "PASSED", "FAIL": "FAILED", "REVIEW": "REVIEW"}
                trace_by_node = {
                    item.get("node_id"): item for item in traces if isinstance(item, dict)
                }
                for node_result in evaluation.node_results:
                    node_trace = trace_by_node.get(node_result["node_id"])
                    if node_trace is None:
                        self._reject(delivery_tag)
                        return ConsumeOutcome.REJECTED
                    node_trace["status"] = status_map[node_result["status"]]
                    if node_result["status"] != "PASS":
                        node_trace["error_type"] = (
                            "ASSERTION_REVIEW"
                            if node_result["status"] == "REVIEW"
                            else "ASSERTION_FAILED"
                        )
                        node_trace["error_message"] = (
                            "AI 断言需要人工复核"
                            if node_result["status"] == "REVIEW"
                            else "AI 断言未通过"
                        )
                cleanup = self._execute_scenario_cleanup_isolated(
                    ScenarioCleanupTask(plan, cleanup_context, evaluation.cleanup_outcome),
                    started,
                )
                if (
                    cleanup is None
                    or cleanup.kind != "SCENARIO_CLEANUP_RESULT"
                    or not isinstance(cleanup.result, Mapping)
                ):
                    self._reject(delivery_tag)
                    return ConsumeOutcome.REJECTED
                cleanup_traces = cleanup.result.get("traces")
                if not isinstance(cleanup_traces, list):
                    self._reject(delivery_tag)
                    return ConsumeOutcome.REJECTED
                traces.extend(cleanup_traces)
                node_order = {node.node_id: index for index, node in enumerate(plan.nodes)}
                traces.sort(key=lambda item: node_order.get(item.get("node_id"), len(node_order)))
                evaluation_token = evaluation.evaluation_token
                if evaluation.assertion_status != "PASS":
                    final_outcome = "FAILED"
                    error_type = (
                        "ASSERTION_REVIEW"
                        if evaluation.assertion_status == "REVIEW"
                        else "ASSERTION_FAILED"
                    )
                    error_message = (
                        "一个或多个 AI 断言需要人工复核"
                        if evaluation.assertion_status == "REVIEW"
                        else "一个或多个 AI 断言未通过"
                    )
                if cleanup.result.get("outcome") != "SUCCESS":
                    final_outcome = "FAILED"
                    error_type = cleanup.result.get("error_type") or "CLEANUP_EXECUTION_ERROR"
                    error_message = "Scenario Cleanup 执行失败"
        elif outcome.kind == "FORCE_STOPPED":
            final_outcome = "CANCELLED"
            traces = []
            error_type = "FORCE_STOP_REQUESTED"
            error_message = FORCE_STOPPED_ERROR_MESSAGE
        elif outcome.kind == "TOTAL_TIMEOUT":
            final_outcome = "TIMEOUT"
            traces = []
            error_type = TOTAL_TIMEOUT_ERROR_TYPE
            error_message = TOTAL_TIMEOUT_ERROR_MESSAGE
        elif outcome.kind == "CANCEL_UNRESPONSIVE":
            final_outcome = "CANCELLED"
            traces = []
            error_type = "CANCEL_REQUESTED"
            error_message = CANCEL_UNRESPONSIVE_ERROR_MESSAGE
        else:
            final_outcome = "FAILED"
            traces = []
            error_type = outcome.error_type or "SCENARIO_EXECUTOR_ERROR"
            error_message = "Scenario 执行失败"
        if final_outcome == "CANCELLED" and error_type not in {
            "CANCEL_REQUESTED",
            "FORCE_STOP_REQUESTED",
        }:
            error_type = "CANCEL_REQUESTED"
            error_message = CANCEL_REQUESTED_ERROR_MESSAGE
        try:
            completion_arguments: dict[str, Any] = {
                "outcome": final_outcome,
                "traces": traces,
                "error_type": error_type,
                "error_message": error_message,
            }
            if evaluation_token is not None:
                completion_arguments["evaluation_token"] = evaluation_token
            complete = self.client.scenario_execution_complete(
                self.identity,
                envelope.run_id,
                envelope.message_id,
                plan.case_run_id,
                **completion_arguments,
            )
        except AuthenticationError:
            self._reject(delivery_tag)
            self._stop_consuming()
            return ConsumeOutcome.STOPPED_AUTH
        except TransportError:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        except (ProtocolError, RunnerError):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        except Exception:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        return self._complete_scenario_disposition(delivery_tag, complete)

    def _execute_scenario_cleanup_isolated(
        self,
        task: ScenarioCleanupTask,
        started: ScenarioExecutionStartResult,
    ) -> IsolatedOutcome | None:
        factory = self._scenario_cleanup_isolation_factory or spawn_scenario_cleanup_task_process
        process = factory(task)
        deadline = _deadline_from(started.started_at, task.plan.total_timeout_ms)
        try:
            process.start()
            while process.is_alive():
                if process.poll(PIPE_POLL_SECONDS):
                    break
                if deadline is not None and self._clock() >= deadline:
                    process.terminate()
                    process.join(timeout=5.0)
                    return IsolatedOutcome(
                        kind="SCENARIO_CLEANUP_RESULT",
                        result={
                            "outcome": "FAILED",
                            "traces": [],
                            "error_type": "TOTAL_TIMEOUT",
                            "error_message": TOTAL_TIMEOUT_ERROR_MESSAGE,
                        },
                    )
            process.poll(PIPE_POLL_SECONDS)
            return process.outcome()
        finally:
            process.close()

    def _complete_scenario_control(
        self,
        delivery_tag: object,
        envelope: RunTaskEnvelopeV1,
        case_run_id: int,
        control: str,
    ) -> ConsumeOutcome:
        error_type = FORCE_STOPPED_ERROR_TYPE if control == "FORCE_STOP" else "CANCEL_REQUESTED"
        error_message = (
            FORCE_STOPPED_ERROR_MESSAGE
            if control == "FORCE_STOP"
            else CANCEL_REQUESTED_ERROR_MESSAGE
        )
        return self._complete_scenario_cancelled(
            delivery_tag,
            envelope,
            case_run_id,
            error_type=error_type,
            error_message=error_message,
        )

    def _complete_scenario_cancelled(
        self,
        delivery_tag: object,
        envelope: RunTaskEnvelopeV1,
        case_run_id: int,
        *,
        error_type: str = "CANCEL_REQUESTED",
        error_message: str = CANCEL_REQUESTED_ERROR_MESSAGE,
    ) -> ConsumeOutcome:
        try:
            complete = self.client.scenario_execution_complete(
                self.identity,
                envelope.run_id,
                envelope.message_id,
                case_run_id,
                outcome="CANCELLED",
                traces=[],
                error_type=error_type,
                error_message=error_message,
            )
        except AuthenticationError:
            self._reject(delivery_tag)
            self._stop_consuming()
            return ConsumeOutcome.STOPPED_AUTH
        except TransportError:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        except (ProtocolError, RunnerError):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        except Exception:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        return self._complete_scenario_disposition(delivery_tag, complete)

    def _complete_scenario_disposition(
        self, delivery_tag: object, complete: object
    ) -> ConsumeOutcome:
        if not isinstance(complete, ScenarioExecutionCompleteResult):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        valid_status = {
            "SUCCESS": {"SUCCESS"},
            "FAILED": {"FAILED", "REVIEW"},
            "TIMEOUT": {"TIMEOUT"},
            "CANCELLED": {"CANCELLED"},
        }.get(complete.outcome, set())
        valid_case_status = {"FAILED", "REVIEW"} if complete.outcome == "FAILED" else valid_status
        if (
            complete.run_status not in valid_status
            or complete.case_run_status not in valid_case_status
        ):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        self.channel.basic_ack(delivery_tag=delivery_tag)
        return ConsumeOutcome.ACKED

    def _process_web_recording_delivery(
        self, delivery_tag: object, envelope: WebRecordingTaskEnvelopeV1
    ) -> ConsumeOutcome:
        """Claim and execute one headed recording; the broker channel stays parent-owned."""

        try:
            claim = self.client.web_recording_claim(
                self.identity, envelope.recording_id, envelope.message_id
            )
            if claim.status == "CANCELLED":
                self.channel.basic_ack(delivery_tag=delivery_tag)
                return ConsumeOutcome.ACKED
            if claim.idempotent and claim.status in {"COMPLETED", "FAILED"}:
                self.channel.basic_ack(delivery_tag=delivery_tag)
                return ConsumeOutcome.ACKED
            if claim.status == "STOP_REQUESTED":
                # The backend accepts a terminal completion from STOP_REQUESTED;
                # settle it explicitly so the recording does not remain pending.
                return self._complete_web_recording(
                    delivery_tag, envelope, "COMPLETED", [], None, None, None
                )
            control = self._web_recording_control(envelope)
            if control[1]:
                return self._complete_web_recording(
                    delivery_tag,
                    envelope,
                    "CANCELLED",
                    [],
                    "CANCEL_REQUESTED",
                    CANCEL_REQUESTED_ERROR_MESSAGE,
                    None,
                )
            if control[0]:
                return self._complete_web_recording(
                    delivery_tag, envelope, "COMPLETED", [], None, None, None
                )
            try:
                plan = self.client.get_web_recording_execution_plan(
                    self.identity, envelope.recording_id, envelope.message_id
                )
            except ProtocolError:
                # A stop/cancel can land after the first control read but before
                # the plan read.  Re-read only the control state so a backend
                # conflict in that narrow window can still be settled safely.
                race_control = self._web_recording_control(envelope)
                if race_control[1]:
                    return self._complete_web_recording(
                        delivery_tag,
                        envelope,
                        "CANCELLED",
                        [],
                        "CANCEL_REQUESTED",
                        CANCEL_REQUESTED_ERROR_MESSAGE,
                        None,
                    )
                if race_control[0]:
                    return self._complete_web_recording(
                        delivery_tag, envelope, "COMPLETED", [], None, None, None
                    )
                raise
            started = self.client.web_recording_execution_start(
                self.identity, envelope.recording_id, envelope.message_id
            )
            if started.status == "STOP_REQUESTED":
                return self._complete_web_recording(
                    delivery_tag, envelope, "COMPLETED", [], None, None, None
                )
            control = self._web_recording_control(envelope)
            if control[1]:
                return self._complete_web_recording(
                    delivery_tag,
                    envelope,
                    "CANCELLED",
                    [],
                    "CANCEL_REQUESTED",
                    CANCEL_REQUESTED_ERROR_MESSAGE,
                    None,
                )
            if control[0]:
                return self._complete_web_recording(
                    delivery_tag, envelope, "COMPLETED", [], None, None, None
                )
        except AuthenticationError:
            self._reject(delivery_tag)
            self._stop_consuming()
            return ConsumeOutcome.STOPPED_AUTH
        except TransportError:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        except (ProtocolError, RunnerError):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        except Exception:
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED

        result, failure = self._execute_web_recording_isolated(delivery_tag, envelope, plan)
        if failure is not None:
            return failure
        if result is None:
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        try:
            server_stop, server_cancel = self._web_recording_control(envelope)
        except AuthenticationError:
            self._reject(delivery_tag)
            self._stop_consuming()
            return ConsumeOutcome.STOPPED_AUTH
        except TransportError:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        except (ProtocolError, RunnerError):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        if server_cancel:
            return self._complete_web_recording(
                delivery_tag,
                envelope,
                "CANCELLED",
                [],
                "CANCEL_REQUESTED",
                CANCEL_REQUESTED_ERROR_MESSAGE,
                None,
            )
        if result.kind == "WEB_RECORDING_RESULT" and isinstance(result.result, dict):
            raw = result.result
            final_outcome = raw.get("outcome")
            events = raw.get("events")
            error_type = raw.get("error_type")
            error_message = raw.get("error_message")
            storage_state = raw.get("storage_state")
            if final_outcome not in {"COMPLETED", "FAILED", "CANCELLED"} or not isinstance(
                events, list
            ):
                self._reject(delivery_tag)
                return ConsumeOutcome.REJECTED
            if final_outcome == "CANCELLED":
                events = []
                storage_state = None
                error_type = "CANCEL_REQUESTED"
                error_message = error_message or CANCEL_REQUESTED_ERROR_MESSAGE
            return self._complete_web_recording(
                delivery_tag,
                envelope,
                final_outcome,
                events,
                error_type,
                error_message,
                storage_state,
            )
        if result.kind == "TOTAL_TIMEOUT":
            return self._complete_web_recording(
                delivery_tag, envelope, "FAILED", [], "RECORDING_TIMEOUT", "Web 录制等待超时", None
            )
        if result.kind == "STOP_UNRESPONSIVE":
            return self._complete_web_recording(
                delivery_tag,
                envelope,
                "FAILED",
                [],
                "STOP_UNRESPONSIVE",
                "Web 录制停止请求未在限定窗口内响应",
                None,
            )
        if result.kind in {"CANCEL_UNRESPONSIVE", "FORCE_STOPPED"}:
            return self._complete_web_recording(
                delivery_tag,
                envelope,
                "CANCELLED",
                [],
                "CANCEL_REQUESTED",
                result.error_message or CANCEL_UNRESPONSIVE_ERROR_MESSAGE,
                None,
            )
        return self._complete_web_recording(
            delivery_tag,
            envelope,
            "FAILED",
            [],
            result.error_type or "WEB_RECORDING_EXECUTOR_ERROR",
            "Web 录制执行失败",
            None,
        )

    def _web_recording_control(self, envelope: WebRecordingTaskEnvelopeV1) -> tuple[bool, bool]:
        control = self.client.get_web_recording_control(
            self.identity, envelope.recording_id, envelope.message_id
        )
        return control.stop_requested, control.cancellation_requested

    def _execute_web_recording_isolated(
        self,
        delivery_tag: object,
        envelope: WebRecordingTaskEnvelopeV1,
        plan: WebRecordingExecutionPlanResult,
    ) -> tuple[IsolatedOutcome | None, ConsumeOutcome | None]:
        factory = self._web_recording_isolation_factory or spawn_web_recording_task_process
        process = factory(plan)
        cancel_requested = False
        stop_requested = False
        control_deadline: float | None = None
        last_control_check = 0.0
        try:
            process.start()
            while process.is_alive():
                if process.poll(PIPE_POLL_SECONDS):
                    break
                now = self._clock()
                if (
                    self._stop_event is not None
                    and self._stop_event.is_set()
                    and not stop_requested
                ):
                    request_stop = getattr(process, "request_stop", None)
                    if callable(request_stop):
                        request_stop()
                        stop_requested = True
                        control_deadline = now + self._scenario_cancel_wait_seconds
                if (cancel_requested or stop_requested) and control_deadline is not None:
                    if now >= control_deadline:
                        process.terminate()
                        process.join(timeout=5.0)
                        return IsolatedOutcome(
                            kind=(
                                "CANCEL_UNRESPONSIVE" if cancel_requested else "STOP_UNRESPONSIVE"
                            ),
                            error_type=(
                                "CANCEL_REQUESTED" if cancel_requested else "STOP_REQUESTED"
                            ),
                            error_message=(
                                CANCEL_UNRESPONSIVE_ERROR_MESSAGE
                                if cancel_requested
                                else "Web 录制停止请求未在限定窗口内响应"
                            ),
                        ), None
                    continue
                if now - last_control_check >= self._force_stop_poll_seconds:
                    last_control_check = now
                    try:
                        server_stop, server_cancel = self._web_recording_control(envelope)
                    except AuthenticationError:
                        process.terminate()
                        process.join(timeout=5.0)
                        self._reject(delivery_tag)
                        self._stop_consuming()
                        return None, ConsumeOutcome.STOPPED_AUTH
                    except TransportError:
                        process.terminate()
                        process.join(timeout=5.0)
                        self._nack(delivery_tag)
                        return None, ConsumeOutcome.REQUEUED
                    except (ProtocolError, RunnerError):
                        process.terminate()
                        process.join(timeout=5.0)
                        self._reject(delivery_tag)
                        return None, ConsumeOutcome.REJECTED
                    if server_cancel and not cancel_requested:
                        request_cancel = getattr(process, "request_cancel", None)
                        if not callable(request_cancel):
                            process.terminate()
                            process.join(timeout=5.0)
                            return IsolatedOutcome(
                                kind="CANCEL_UNRESPONSIVE",
                                error_type="CANCEL_REQUESTED",
                                error_message=CANCEL_UNRESPONSIVE_ERROR_MESSAGE,
                            ), None
                        request_cancel()
                        cancel_requested = True
                        control_deadline = now + self._scenario_cancel_wait_seconds
                    elif server_stop and not stop_requested:
                        request_stop = getattr(process, "request_stop", None)
                        if callable(request_stop):
                            request_stop()
                            stop_requested = True
                            control_deadline = now + self._scenario_cancel_wait_seconds
                        else:
                            process.terminate()
                            process.join(timeout=5.0)
                            return IsolatedOutcome(
                                kind="STOP_UNRESPONSIVE",
                                error_type="STOP_REQUESTED",
                                error_message="Web 录制停止请求未在限定窗口内响应",
                            ), None
            process.poll(PIPE_POLL_SECONDS)
            outcome = process.outcome()
            if outcome is None:
                return IsolatedOutcome(
                    kind="EXECUTION_ERROR",
                    error_type="WEB_RECORDING_EXECUTOR_ERROR",
                    error_message="Web 录制隔离执行异常终止",
                ), None
            return outcome, None
        finally:
            process.close()

    def _complete_web_recording(
        self,
        delivery_tag: object,
        envelope: WebRecordingTaskEnvelopeV1,
        outcome: str,
        events: list[object],
        error_type: str | None,
        error_message: str | None,
        storage_state: Mapping[str, Any] | None,
    ) -> ConsumeOutcome:
        try:
            complete = self.client.web_recording_execution_complete(
                self.identity,
                envelope.recording_id,
                envelope.message_id,
                outcome=outcome,
                events=events,
                error_type=error_type,
                error_message=error_message,
                storage_state=storage_state,
            )
        except AuthenticationError:
            self._reject(delivery_tag)
            self._stop_consuming()
            return ConsumeOutcome.STOPPED_AUTH
        except TransportError:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        except (ProtocolError, RunnerError):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        except Exception:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        if not isinstance(complete, WebRecordingCompleteResult):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        if complete.outcome != outcome or complete.status != outcome:
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        self.channel.basic_ack(delivery_tag=delivery_tag)
        return ConsumeOutcome.ACKED

    def _process_web_exploration_delivery(
        self, delivery_tag: object, envelope: WebExplorationTaskEnvelopeV1
    ) -> ConsumeOutcome:
        """Execute one restricted MCP exploration while retaining broker ownership."""

        claimed = False
        try:
            claim = self.client.web_exploration_claim(
                self.identity, envelope.exploration_id, envelope.message_id
            )
            claimed = True
            if claim.status in {"COMPLETED", "FAILED", "CANCELLED"}:
                self.channel.basic_ack(delivery_tag=delivery_tag)
                return ConsumeOutcome.ACKED
            if claim.status == "STOP_REQUESTED":
                return self._complete_web_exploration(
                    delivery_tag,
                    envelope,
                    "COMPLETED",
                    [],
                    [],
                    {"reason": "任务开始前已请求停止"},
                    None,
                    None,
                )
            control = self.client.get_web_exploration_control(
                self.identity, envelope.exploration_id, envelope.message_id
            )
            if control.cancellation_requested:
                return self._complete_web_exploration(
                    delivery_tag,
                    envelope,
                    "CANCELLED",
                    [],
                    [],
                    None,
                    "CANCEL_REQUESTED",
                    CANCEL_REQUESTED_ERROR_MESSAGE,
                )
            plan = self.client.get_web_exploration_execution_plan(
                self.identity, envelope.exploration_id, envelope.message_id
            )
            started = self.client.web_exploration_execution_start(
                self.identity, envelope.exploration_id, envelope.message_id
            )
            if started.status != "RUNNING":
                raise ProtocolError("Web 探索未进入 RUNNING")
        except AuthenticationError:
            self._reject(delivery_tag)
            self._stop_consuming()
            return ConsumeOutcome.STOPPED_AUTH
        except TransportError:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        except (ProtocolError, RunnerError) as exc:
            if claimed:
                return self._complete_web_exploration(
                    delivery_tag,
                    envelope,
                    "FAILED",
                    [],
                    [],
                    None,
                    "WEB_EXPLORATION_PROTOCOL_ERROR",
                    redact_text(exc, (self.identity.credential,), limit=900),
                )
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        except Exception:
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED

        if not isinstance(plan, WebExplorationExecutionPlanResult):
            return self._complete_web_exploration(
                delivery_tag,
                envelope,
                "FAILED",
                [],
                [],
                None,
                "WEB_EXPLORATION_PROTOCOL_ERROR",
                "Web 探索执行计划格式无效",
            )
        executor = WebExplorationExecutor(
            decide=lambda observation: self.client.request_web_exploration_decision(
                self.identity,
                envelope.exploration_id,
                envelope.message_id,
                observation,
            ),
            control=lambda: self.client.get_web_exploration_control(
                self.identity, envelope.exploration_id, envelope.message_id
            ),
            upload_evidence=lambda evidence, sequence, kind, label: (
                self.client.upload_web_exploration_evidence(
                    self.identity,
                    envelope.exploration_id,
                    envelope.message_id,
                    evidence,
                    sequence=sequence,
                    kind=kind,
                    label=label,
                ).evidence_id
            ),
        )
        try:
            with TemporaryDirectory(prefix="web-exploration-") as temporary:
                result = executor.execute(plan, Path(temporary))
        except AuthenticationError:
            self._reject(delivery_tag)
            self._stop_consuming()
            return ConsumeOutcome.STOPPED_AUTH
        except TransportError:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        except (ProtocolError, RunnerError) as exc:
            return self._complete_web_exploration(
                delivery_tag,
                envelope,
                "FAILED",
                [],
                [],
                None,
                "WEB_EXPLORATION_PROTOCOL_ERROR",
                redact_text(exc, (self.identity.credential,), limit=900),
            )
        except Exception:
            result = None
        if result is None:
            return self._complete_web_exploration(
                delivery_tag,
                envelope,
                "FAILED",
                [],
                [],
                None,
                "WEB_EXPLORATION_ERROR",
                "Web 探索执行失败",
            )
        return self._complete_web_exploration(
            delivery_tag,
            envelope,
            result.outcome,
            result.observations,
            result.action_trace,
            result.result_summary,
            result.error_type,
            result.error_message,
        )

    def _complete_web_exploration(
        self,
        delivery_tag: object,
        envelope: WebExplorationTaskEnvelopeV1,
        outcome: str,
        observations: list[Mapping[str, Any]],
        action_trace: list[Mapping[str, Any]],
        result_summary: Mapping[str, Any] | None,
        error_type: str | None,
        error_message: str | None,
    ) -> ConsumeOutcome:
        try:
            complete = self.client.web_exploration_execution_complete(
                self.identity,
                envelope.exploration_id,
                envelope.message_id,
                outcome=outcome,
                observations=observations,
                action_trace=action_trace,
                result_summary=result_summary,
                error_type=error_type,
                error_message=error_message,
            )
        except AuthenticationError:
            self._reject(delivery_tag)
            self._stop_consuming()
            return ConsumeOutcome.STOPPED_AUTH
        except TransportError:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        except (ProtocolError, RunnerError):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        except Exception:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        if not isinstance(complete, WebExplorationCompleteResult) or complete.status != outcome:
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        self.channel.basic_ack(delivery_tag=delivery_tag)
        return ConsumeOutcome.ACKED

    def _process_web_execution_delivery(
        self, delivery_tag: object, envelope: RunTaskEnvelopeV1
    ) -> ConsumeOutcome:
        try:
            claim = self.client.claim(self.identity, envelope.run_id, envelope.message_id)
            if claim.status == "CANCELLED":
                self.channel.basic_ack(delivery_tag=delivery_tag)
                return ConsumeOutcome.ACKED
            plan = self.client.get_web_execution_plan(
                self.identity, envelope.run_id, envelope.message_id, None
            )
            started = self.client.web_execution_start(
                self.identity, envelope.run_id, envelope.message_id, plan.case_run_id
            )
        except AuthenticationError:
            self._reject(delivery_tag)
            self._stop_consuming()
            return ConsumeOutcome.STOPPED_AUTH
        except TransportError:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        except (ProtocolError, RunnerError):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        except Exception:
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED

        if started.status == "CANCELLING":
            return self._complete_web_cancelled(delivery_tag, envelope, plan.case_run_id)
        control, control_failure = self._check_control(
            delivery_tag, envelope.run_id, envelope.message_id, plan.case_run_id
        )
        if control_failure is not None:
            return control_failure
        if control is not None:
            return self._complete_web_control(delivery_tag, envelope, plan.case_run_id, control)
        with TemporaryDirectory(prefix="ai-test-runner-web-") as evidence_dir:
            try:
                outcome, execution_failure = self._execute_web_isolated(
                    delivery_tag, envelope, plan, started, evidence_dir
                )
            except AuthenticationError:
                self._reject(delivery_tag)
                self._stop_consuming()
                return ConsumeOutcome.STOPPED_AUTH
            except TransportError:
                self._nack(delivery_tag)
                return ConsumeOutcome.REQUEUED
            except (ProtocolError, RunnerError):
                self._reject(delivery_tag)
                return ConsumeOutcome.REJECTED
            except Exception:
                self._reject(delivery_tag)
                return ConsumeOutcome.REJECTED
            if execution_failure is not None:
                return execution_failure
            if outcome is None:
                self._reject(delivery_tag)
                return ConsumeOutcome.REJECTED
            child_result = outcome.kind == "WEB_RESULT" and isinstance(outcome.result, dict)
            pending_control: str | None = None
            if (
                outcome.kind
                not in {
                    "FORCE_STOPPED",
                    "TOTAL_TIMEOUT",
                    "CANCEL_UNRESPONSIVE",
                    "EXECUTION_ERROR",
                }
                and not child_result
            ):
                control, control_failure = self._check_control(
                    delivery_tag, envelope.run_id, envelope.message_id, plan.case_run_id
                )
                if control_failure is not None:
                    return control_failure
                if control is not None:
                    return self._complete_web_control(
                        delivery_tag, envelope, plan.case_run_id, control
                    )
            elif child_result:
                pending_control, control_failure = self._check_control(
                    delivery_tag, envelope.run_id, envelope.message_id, plan.case_run_id
                )
                if control_failure is not None:
                    return control_failure
            try:
                evidence = self._prepare_web_evidence(outcome, plan, evidence_dir)
                self._upload_web_evidence(envelope, plan.case_run_id, evidence)
            except AuthenticationError:
                self._reject(delivery_tag)
                self._stop_consuming()
                return ConsumeOutcome.STOPPED_AUTH
            except TransportError:
                return self._complete_web_evidence_failure(
                    delivery_tag,
                    envelope,
                    plan,
                    outcome,
                    pending_control=pending_control,
                    fallback=ConsumeOutcome.REQUEUED,
                )
            except RunnerError:
                return self._complete_web_evidence_failure(
                    delivery_tag,
                    envelope,
                    plan,
                    outcome,
                    pending_control=pending_control,
                    fallback=ConsumeOutcome.REJECTED,
                )
            except Exception:
                return self._complete_web_evidence_failure(
                    delivery_tag,
                    envelope,
                    plan,
                    outcome,
                    pending_control=pending_control,
                    fallback=ConsumeOutcome.REQUEUED,
                )
            if pending_control is not None:
                return self._complete_web_control(
                    delivery_tag, envelope, plan.case_run_id, pending_control
                )
            return self._complete_web_result(delivery_tag, envelope, plan, outcome)

    def _execute_web_isolated(
        self,
        delivery_tag: object,
        envelope: RunTaskEnvelopeV1,
        plan: WebExecutionPlanResult,
        started: WebExecutionStartResult,
        evidence_dir: str,
    ) -> tuple[IsolatedOutcome | None, ConsumeOutcome | None]:
        factory = self._web_isolation_factory or spawn_web_task_process
        deadline = _deadline_from(started.started_at, plan.total_timeout_ms)
        process = _call_web_isolation_factory(factory, plan, evidence_dir)
        try:
            process.start()
            last_control_check = 0.0
            cancel_requested = False
            cancel_deadline: float | None = None
            while process.is_alive():
                if process.poll(PIPE_POLL_SECONDS):
                    break
                now = self._clock()
                if deadline is not None and now >= deadline:
                    process.terminate()
                    process.join(timeout=5.0)
                    return IsolatedOutcome(kind="TOTAL_TIMEOUT"), None
                if cancel_requested:
                    if cancel_deadline is not None and now >= cancel_deadline:
                        process.terminate()
                        process.join(timeout=5.0)
                        return IsolatedOutcome(
                            kind="CANCEL_UNRESPONSIVE",
                            error_type="CANCEL_REQUESTED",
                            error_message=CANCEL_UNRESPONSIVE_ERROR_MESSAGE,
                        ), None
                    continue
                if now - last_control_check >= self._force_stop_poll_seconds:
                    last_control_check = now
                    control, failure = self._check_control(
                        delivery_tag,
                        envelope.run_id,
                        envelope.message_id,
                        plan.case_run_id,
                    )
                    if failure is not None:
                        process.terminate()
                        process.join(timeout=5.0)
                        return None, failure
                    if control == "FORCE_STOP":
                        process.terminate()
                        process.join(timeout=5.0)
                        return IsolatedOutcome(kind="FORCE_STOPPED"), None
                    if control == "CANCEL":
                        request_cancel = getattr(process, "request_cancel", None)
                        if not callable(request_cancel):
                            process.terminate()
                            process.join(timeout=5.0)
                            return IsolatedOutcome(
                                kind="CANCEL_UNRESPONSIVE",
                                error_type="CANCEL_REQUESTED",
                                error_message=CANCEL_UNRESPONSIVE_ERROR_MESSAGE,
                            ), None
                        try:
                            request_cancel()
                        except Exception:  # noqa: BLE001 - fail closed
                            process.terminate()
                            process.join(timeout=5.0)
                            return IsolatedOutcome(
                                kind="CANCEL_UNRESPONSIVE",
                                error_type="CANCEL_REQUESTED",
                                error_message=CANCEL_UNRESPONSIVE_ERROR_MESSAGE,
                            ), None
                        cancel_requested = True
                        cancel_deadline = now + self._scenario_cancel_wait_seconds
            process.poll(PIPE_POLL_SECONDS)
            outcome = process.outcome()
            if outcome is None:
                if cancel_requested:
                    return IsolatedOutcome(
                        kind="CANCEL_UNRESPONSIVE",
                        error_type="CANCEL_REQUESTED",
                        error_message=CANCEL_UNRESPONSIVE_ERROR_MESSAGE,
                    ), None
                if deadline is not None and self._clock() >= deadline:
                    return IsolatedOutcome(kind="TOTAL_TIMEOUT"), None
                return IsolatedOutcome(
                    kind="EXECUTION_ERROR",
                    error_type="WEB_EXECUTOR_ERROR",
                    error_message="Web 隔离执行异常终止",
                ), None
            return outcome, None
        finally:
            process.close()

    @staticmethod
    def _prepare_web_evidence(
        outcome: IsolatedOutcome,
        plan: WebExecutionPlanResult,
        evidence_dir: str,
    ) -> tuple[ValidatedEvidenceArtifact, ...]:
        if outcome.kind == "WEB_RESULT" and isinstance(outcome.result, dict):
            raw = outcome.result.get("evidence", [])
            if raw is None:
                raw = []
            evidence = list(validate_manifest(evidence_dir, raw))
            if any(item.artifact_type == "WEB_SUMMARY" for item in evidence):
                return tuple(evidence)
            status = outcome.result.get("outcome")
            if status not in {"SUCCESS", "FAILED", "TIMEOUT", "CANCELLED"}:
                status = "FAILED"
        else:
            status = {
                "TOTAL_TIMEOUT": "TIMEOUT",
                "FORCE_STOPPED": "CANCELLED",
                "CANCEL_UNRESPONSIVE": "CANCELLED",
            }.get(outcome.kind, "FAILED")
            evidence = []
        metadata = {
            "title": None,
            "final_url": None,
            "page_count": 0,
            "duration_ms": 0,
            "error_count": 0,
            "status": status,
        }
        summary_path = Path(evidence_dir) / "web-summary.json"
        summary_path.write_bytes(canonical_json(metadata))
        summary = validate_manifest(
            evidence_dir,
            [
                {
                    "artifact_type": "WEB_SUMMARY",
                    "relative_path": "web-summary.json",
                    "metadata": metadata,
                }
            ],
        )[0]
        evidence.append(summary)
        return tuple(evidence)

    def _upload_web_evidence(
        self,
        envelope: RunTaskEnvelopeV1,
        case_run_id: int,
        evidence: tuple[ValidatedEvidenceArtifact, ...],
    ) -> None:
        for artifact in evidence:
            self.client.upload_web_evidence(
                self.identity,
                envelope.run_id,
                envelope.message_id,
                case_run_id,
                artifact,
            )

    def _complete_web_evidence_failure(
        self,
        delivery_tag: object,
        envelope: RunTaskEnvelopeV1,
        plan: WebExecutionPlanResult,
        outcome: IsolatedOutcome,
        *,
        pending_control: str | None,
        fallback: ConsumeOutcome,
    ) -> ConsumeOutcome:
        """Settle an executed Web run without weakening the Evidence boundary."""

        if pending_control is not None:
            return self._complete_web_control(
                delivery_tag, envelope, plan.case_run_id, pending_control
            )
        if self._web_outcome_is_cancelled(outcome):
            return self._complete_web_result(delivery_tag, envelope, plan, outcome)
        traces = self._web_evidence_failure_traces(outcome, plan)
        if traces is None:
            return self._apply_web_failure_disposition(delivery_tag, fallback)
        try:
            complete = self.client.web_execution_complete(
                self.identity,
                envelope.run_id,
                envelope.message_id,
                plan.case_run_id,
                outcome="FAILED",
                traces=traces,
                error_type=WEB_EVIDENCE_ERROR_TYPE,
                error_message=WEB_EVIDENCE_ERROR_MESSAGE,
            )
        except AuthenticationError:
            self._reject(delivery_tag)
            self._stop_consuming()
            return ConsumeOutcome.STOPPED_AUTH
        except TransportError:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        except (ProtocolError, RunnerError):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        except Exception:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        return self._complete_web_disposition(delivery_tag, complete)

    @staticmethod
    def _web_outcome_is_cancelled(outcome: IsolatedOutcome) -> bool:
        if outcome.kind in {"FORCE_STOPPED", "CANCEL_UNRESPONSIVE"}:
            return True
        return (
            outcome.kind == "WEB_RESULT"
            and isinstance(outcome.result, Mapping)
            and outcome.result.get("outcome") == "CANCELLED"
        )

    @staticmethod
    def _web_evidence_failure_traces(
        outcome: IsolatedOutcome,
        plan: WebExecutionPlanResult,
    ) -> list[Mapping[str, Any]] | None:
        if outcome.kind == "WEB_RESULT" and isinstance(outcome.result, Mapping):
            raw_traces = outcome.result.get("traces")
            if not isinstance(raw_traces, list):
                return None
            traces = raw_traces
        else:
            return None
        expected_node_ids = {
            *(f"action_{index}" for index in range(1, len(plan.actions) + 1)),
            *(f"assertion_{index}" for index in range(1, len(plan.assertions) + 1)),
        }
        if len(traces) != len(expected_node_ids) or any(
            not isinstance(trace, Mapping) for trace in traces
        ):
            return None
        actual_node_ids = [trace.get("node_id") for trace in traces]
        if any(not isinstance(node_id, str) for node_id in actual_node_ids):
            return None
        if len(set(actual_node_ids)) != len(actual_node_ids):
            return None
        if set(actual_node_ids) != expected_node_ids:
            return None
        return traces

    def _apply_web_failure_disposition(
        self, delivery_tag: object, fallback: ConsumeOutcome
    ) -> ConsumeOutcome:
        if fallback is ConsumeOutcome.REQUEUED:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        self._reject(delivery_tag)
        return ConsumeOutcome.REJECTED

    def _complete_web_result(
        self,
        delivery_tag: object,
        envelope: RunTaskEnvelopeV1,
        plan: WebExecutionPlanResult,
        outcome: IsolatedOutcome,
    ) -> ConsumeOutcome:
        if outcome.kind == "WEB_RESULT" and isinstance(outcome.result, dict):
            final_outcome = outcome.result.get("outcome")
            traces = outcome.result.get("traces")
            error_type = outcome.result.get("error_type")
            error_message = outcome.result.get("error_message")
            refreshed_session = outcome.result.get("refreshed_session")
            session_recovery = outcome.result.get("session_recovery")
            pending_ai = outcome.result.get("pending_ai", [])
            if final_outcome not in {"SUCCESS", "FAILED", "TIMEOUT", "CANCELLED"}:
                self._reject(delivery_tag)
                return ConsumeOutcome.REJECTED
            if not isinstance(traces, list):
                self._reject(delivery_tag)
                return ConsumeOutcome.REJECTED
            if not isinstance(pending_ai, list):
                self._reject(delivery_tag)
                return ConsumeOutcome.REJECTED
        elif outcome.kind == "FORCE_STOPPED":
            final_outcome = "CANCELLED"
            traces = []
            error_type = "CANCEL_REQUESTED"
            error_message = "Web 执行已被强制停止"
            refreshed_session = None
            session_recovery = None
            pending_ai = []
        elif outcome.kind == "TOTAL_TIMEOUT":
            final_outcome = "TIMEOUT"
            traces = _web_terminal_traces(plan, "TIMEOUT")
            error_type = "TOTAL_TIMEOUT"
            error_message = TOTAL_TIMEOUT_ERROR_MESSAGE
            refreshed_session = None
            session_recovery = None
            pending_ai = []
        elif outcome.kind == "CANCEL_UNRESPONSIVE":
            final_outcome = "CANCELLED"
            traces = []
            error_type = "CANCEL_REQUESTED"
            error_message = CANCEL_UNRESPONSIVE_ERROR_MESSAGE
            refreshed_session = None
            session_recovery = None
            pending_ai = []
        else:
            final_outcome = "FAILED"
            traces = _web_terminal_traces(plan, "FAILED")
            error_type = "WEB_EXECUTOR_ERROR"
            error_message = "Web 执行失败"
            refreshed_session = None
            session_recovery = None
            pending_ai = []
        if final_outcome == "CANCELLED":
            error_type = "CANCEL_REQUESTED"
            error_message = error_message or CANCEL_REQUESTED_ERROR_MESSAGE
        evaluation_token: str | None = None
        try:
            if pending_ai and final_outcome not in {"CANCELLED", "TIMEOUT"}:
                evaluation = self.client.web_execution_evaluate(
                    self.identity,
                    envelope.run_id,
                    envelope.message_id,
                    plan.case_run_id,
                    pending_ai,
                )
                evaluation_token = evaluation.evaluation_token
                status_by_node = {
                    item["node_id"]: item["status"] for item in evaluation.node_results
                }
                trace_status = {
                    "PASS": ("SUCCESS", None, None),
                    "FAIL": ("FAILED", "ASSERTION_FAILED", "Web AI 语义断言未通过"),
                    "REVIEW": (
                        "REVIEW",
                        "ASSERTION_REVIEW",
                        "Web AI 语义断言需要人工复核",
                    ),
                }
                for trace in traces:
                    status = status_by_node.get(trace.get("node_id"))
                    if status is not None:
                        trace["status"], trace["error_type"], trace["error_message"] = trace_status[
                            status
                        ]
                if evaluation.assertion_status != "PASS":
                    final_outcome = "FAILED"
                    error_type = (
                        "ASSERTION_REVIEW"
                        if evaluation.assertion_status == "REVIEW"
                        else "ASSERTION_FAILED"
                    )
                    error_message = (
                        "Web AI 语义断言需要人工复核"
                        if evaluation.assertion_status == "REVIEW"
                        else "Web AI 语义断言未通过"
                    )
            complete_kwargs = {
                "outcome": final_outcome,
                "traces": traces,
                "error_type": error_type,
                "error_message": error_message,
                "refreshed_session": refreshed_session,
                "session_recovery": session_recovery,
            }
            if evaluation_token is not None:
                complete_kwargs["evaluation_token"] = evaluation_token
            complete = self.client.web_execution_complete(
                self.identity,
                envelope.run_id,
                envelope.message_id,
                plan.case_run_id,
                **complete_kwargs,
            )
        except AuthenticationError:
            self._reject(delivery_tag)
            self._stop_consuming()
            return ConsumeOutcome.STOPPED_AUTH
        except TransportError:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        except (ProtocolError, RunnerError):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        except Exception:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        return self._complete_web_disposition(delivery_tag, complete)

    def _complete_web_control(
        self,
        delivery_tag: object,
        envelope: RunTaskEnvelopeV1,
        case_run_id: int,
        control: str,
    ) -> ConsumeOutcome:
        return self._complete_web_cancelled(delivery_tag, envelope, case_run_id)

    def _complete_web_cancelled(
        self,
        delivery_tag: object,
        envelope: RunTaskEnvelopeV1,
        case_run_id: int,
    ) -> ConsumeOutcome:
        try:
            complete = self.client.web_execution_complete(
                self.identity,
                envelope.run_id,
                envelope.message_id,
                case_run_id,
                outcome="CANCELLED",
                traces=[],
                error_type="CANCEL_REQUESTED",
                error_message=CANCEL_REQUESTED_ERROR_MESSAGE,
            )
        except AuthenticationError:
            self._reject(delivery_tag)
            self._stop_consuming()
            return ConsumeOutcome.STOPPED_AUTH
        except TransportError:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        except (ProtocolError, RunnerError):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        except Exception:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        return self._complete_web_disposition(delivery_tag, complete)

    def _complete_web_disposition(self, delivery_tag: object, complete: object) -> ConsumeOutcome:
        if not isinstance(complete, WebExecutionCompleteResult):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        valid_status = {
            "SUCCESS": {"SUCCESS"},
            "FAILED": {"FAILED", "REVIEW"},
            "TIMEOUT": {"TIMEOUT"},
            "CANCELLED": {"CANCELLED"},
        }.get(complete.outcome, set())
        if complete.run_status not in valid_status or complete.case_run_status not in valid_status:
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        self.channel.basic_ack(delivery_tag=delivery_tag)
        return ConsumeOutcome.ACKED

    def _process_execution_delivery(
        self, delivery_tag: object, envelope: RunTaskEnvelopeV1
    ) -> ConsumeOutcome:
        try:
            claim = self.client.claim(self.identity, envelope.run_id, envelope.message_id)
        except AuthenticationError:
            self._reject(delivery_tag)
            self._stop_consuming()
            return ConsumeOutcome.STOPPED_AUTH
        except TransportError:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        except (ProtocolError, RunnerError):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        except Exception:
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED

        if claim.status == "CANCELLED":
            self.channel.basic_ack(delivery_tag=delivery_tag)
            return ConsumeOutcome.ACKED

        while True:
            try:
                plan = self.client.get_execution_plan(
                    self.identity,
                    envelope.run_id,
                    envelope.message_id,
                )
                retry_policy = validate_retry_policy(
                    getattr(plan.request, "retry_policy", RetryPolicy())
                )
                started = self.client.execution_start(
                    self.identity,
                    envelope.run_id,
                    envelope.message_id,
                    plan.case_run_id,
                )
            except AuthenticationError:
                self._reject(delivery_tag)
                self._stop_consuming()
                return ConsumeOutcome.STOPPED_AUTH
            except TransportError:
                self._nack(delivery_tag)
                return ConsumeOutcome.REQUEUED
            except (ProtocolError, RunnerError):
                self._reject(delivery_tag)
                return ConsumeOutcome.REJECTED

            except Exception:
                self._reject(delivery_tag)
                return ConsumeOutcome.REJECTED

            if started.status == "CANCELLING":
                self._performance_result_cache.pop((envelope.run_id, envelope.message_id), None)
                return self._complete_cancelled(
                    delivery_tag,
                    envelope.run_id,
                    envelope.message_id,
                    plan.case_run_id,
                )

            control, control_failure = self._check_control(
                delivery_tag,
                envelope.run_id,
                envelope.message_id,
                plan.case_run_id,
            )
            if control_failure is not None:
                return control_failure
            if control is not None:
                self._performance_result_cache.pop((envelope.run_id, envelope.message_id), None)
                return self._complete_control_result(
                    delivery_tag,
                    envelope.run_id,
                    envelope.message_id,
                    plan.case_run_id,
                    control,
                )

            execution_deadline = _deadline_from(started.started_at, plan.total_timeout_ms)
            completion_kwargs, execution_failure = self._execute_with_retries(
                delivery_tag,
                envelope.run_id,
                envelope.message_id,
                plan.case_run_id,
                plan if getattr(plan, "has_pipeline", False) else plan.request,
                RetryPolicy() if getattr(plan, "has_pipeline", False) else retry_policy,
                deadline=execution_deadline,
            )
            if execution_failure is not None:
                return execution_failure
            if completion_kwargs is None:
                self._reject(delivery_tag)
                return ConsumeOutcome.REJECTED

            cleanup_pending = bool(completion_kwargs.pop("_cleanup_pending", False))
            cleanup_context = completion_kwargs.pop("_cleanup_context", None)
            if cleanup_pending and not isinstance(cleanup_context, Mapping):
                self._reject(delivery_tag)
                return ConsumeOutcome.REJECTED

            control, control_failure = self._check_control(
                delivery_tag,
                envelope.run_id,
                envelope.message_id,
                plan.case_run_id,
            )
            if control_failure is not None:
                return control_failure
            if control is not None:
                if cleanup_pending:
                    resources, cleanup_failure = self._execute_deferred_cleanup(
                        delivery_tag,
                        envelope.run_id,
                        envelope.message_id,
                        plan.case_run_id,
                        plan,
                        cleanup_context,
                        completion_kwargs.get("resources", []),
                        "CANCELLED",
                        execution_deadline,
                    )
                    if cleanup_failure is not None:
                        return cleanup_failure
                    completion_kwargs["resources"] = resources or []
                return self._complete_control_result(
                    delivery_tag,
                    envelope.run_id,
                    envelope.message_id,
                    plan.case_run_id,
                    control,
                    retry_count=completion_kwargs["retry_count"],
                    resources=completion_kwargs.get("resources"),
                )

            if cleanup_pending:
                response = completion_kwargs.get("response")
                if not isinstance(response, Mapping):
                    self._reject(delivery_tag)
                    return ConsumeOutcome.REJECTED
                try:
                    evaluation = self.client.execution_evaluate(
                        self.identity,
                        envelope.run_id,
                        envelope.message_id,
                        plan.case_run_id,
                        response,
                    )
                except AuthenticationError:
                    self._reject(delivery_tag)
                    self._stop_consuming()
                    return ConsumeOutcome.STOPPED_AUTH
                except TransportError:
                    self._nack(delivery_tag)
                    return ConsumeOutcome.REQUEUED
                except (ProtocolError, RunnerError):
                    self._reject(delivery_tag)
                    return ConsumeOutcome.REJECTED
                except Exception:
                    self._nack(delivery_tag)
                    return ConsumeOutcome.REQUEUED
                resources, cleanup_failure = self._execute_deferred_cleanup(
                    delivery_tag,
                    envelope.run_id,
                    envelope.message_id,
                    plan.case_run_id,
                    plan,
                    cleanup_context,
                    completion_kwargs.get("resources", []),
                    evaluation.cleanup_outcome,
                    execution_deadline,
                )
                if cleanup_failure is not None:
                    return cleanup_failure
                completion_kwargs["resources"] = resources or []
                completion_kwargs["evaluation_token"] = evaluation.evaluation_token

            try:
                complete = self.client.execution_complete(
                    self.identity,
                    envelope.run_id,
                    envelope.message_id,
                    plan.case_run_id,
                    **completion_kwargs,
                )
            except AuthenticationError:
                self._reject(delivery_tag)
                self._stop_consuming()
                return ConsumeOutcome.STOPPED_AUTH
            except TransportError:
                self._nack(delivery_tag)
                return ConsumeOutcome.REQUEUED
            except (ProtocolError, RunnerError):
                self._reject(delivery_tag)
                return ConsumeOutcome.REJECTED
            except Exception:
                self._nack(delivery_tag)
                return ConsumeOutcome.REQUEUED
            if complete.run_status != "RUNNING":
                return self._complete_disposition(delivery_tag, complete)
            if complete.case_run_status not in {
                "SUCCESS",
                "FAILED",
                "REVIEW",
                "TIMEOUT",
            }:
                self._reject(delivery_tag)
                return ConsumeOutcome.REJECTED

    def _process_performance_delivery(
        self, delivery_tag: object, envelope: RunTaskEnvelopeV1
    ) -> ConsumeOutcome:
        if self.executor is None:
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        plan = None
        try:
            claim = self.client.claim(self.identity, envelope.run_id, envelope.message_id)
            if claim.status == "CANCELLED":
                self._forget_performance_result(envelope.run_id, envelope.message_id)
                self.channel.basic_ack(delivery_tag=delivery_tag)
                return ConsumeOutcome.ACKED
            plan = self.client.get_performance_execution_plan(
                self.identity, envelope.run_id, envelope.message_id
            )
            if plan.schema_version >= 3:
                if plan.target_type == "SCENARIO":
                    target_plan = self.client.get_scenario_execution_plan(
                        self.identity, envelope.run_id, envelope.message_id, plan.case_run_id
                    )
                else:
                    target_plan = self.client.get_execution_plan(
                        self.identity, envelope.run_id, envelope.message_id, plan.case_run_id
                    )
                    target_plan = replace(target_plan, request=plan.request)
                plan = replace(plan, target_plan=target_plan)
            if plan.target_type == "SCENARIO":
                started = self.client.scenario_execution_start(
                    self.identity,
                    envelope.run_id,
                    envelope.message_id,
                    plan.case_run_id,
                )
            else:
                started = self.client.execution_start(
                    self.identity,
                    envelope.run_id,
                    envelope.message_id,
                    plan.case_run_id,
                )
            if started.status == "CANCELLING":
                return self._complete_cancelled(
                    delivery_tag,
                    envelope.run_id,
                    envelope.message_id,
                    plan.case_run_id,
                )
            control, control_failure = self._check_control(
                delivery_tag,
                envelope.run_id,
                envelope.message_id,
                plan.case_run_id,
            )
            if control_failure is not None:
                return control_failure
            if control is not None:
                return self._complete_control_result(
                    delivery_tag,
                    envelope.run_id,
                    envelope.message_id,
                    plan.case_run_id,
                    control,
                )
            cache_key = (envelope.run_id, envelope.message_id)
            outcome = self._performance_result_cache.get(cache_key)
            if outcome is None and self._performance_result_store is not None:
                outcome = self._performance_result_store.load(*cache_key)
                if outcome is not None:
                    self._performance_result_cache[cache_key] = outcome
            execution_failure = None
            if outcome is None:
                outcome, execution_failure = self._execute_performance_isolated(
                    delivery_tag, envelope, plan, started
                )
                if outcome is not None and outcome.kind == "PERFORMANCE_RESULT":
                    self._performance_result_cache[cache_key] = outcome
                    if self._performance_result_store is not None:
                        try:
                            self._performance_result_store.save(*cache_key, outcome)
                        except RunnerError:
                            # Keep the in-memory result and requeue. A redelivery in this
                            # process can retry persistence without applying the load again.
                            self._nack(delivery_tag)
                            return ConsumeOutcome.REQUEUED
            if execution_failure is not None:
                return execution_failure
            if outcome is None:
                self._reject(delivery_tag)
                return ConsumeOutcome.REJECTED
            return self._complete_performance_outcome(delivery_tag, envelope, plan, outcome)
        except AuthenticationError:
            self._forget_performance_result(envelope.run_id, envelope.message_id)
            self._reject(delivery_tag)
            self._stop_consuming()
            return ConsumeOutcome.STOPPED_AUTH
        except TransportError:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        except ExecutionError as exc:
            if exc.error_type == "CANCEL_REQUESTED" and plan is not None:
                return self._complete_cancelled(
                    delivery_tag,
                    envelope.run_id,
                    envelope.message_id,
                    plan.case_run_id,
                )
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        except (ProtocolError, RunnerError):
            self._forget_performance_result(envelope.run_id, envelope.message_id)
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        except Exception:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED

    def _execute_performance_isolated(
        self,
        delivery_tag: object,
        envelope: RunTaskEnvelopeV1,
        plan: PerformanceExecutionPlanResult,
        started: ExecutionStartResult,
    ) -> tuple[IsolatedOutcome | None, ConsumeOutcome | None]:
        if self._performance_isolation_factory is None:

            def should_cancel() -> bool:
                control = self.client.get_execution_cancellation(
                    self.identity,
                    envelope.run_id,
                    envelope.message_id,
                    plan.case_run_id,
                )
                return control.cancellation_requested or control.force_stop_requested

            result = PerformanceExecutor(self.executor).execute(  # type: ignore[arg-type]
                plan.request,
                concurrency=plan.concurrency,
                iterations=plan.iterations,
                warmup_iterations=plan.warmup_iterations,
                load_mode=plan.load_mode,
                target_rps=plan.target_rps,
                duration_seconds=plan.duration_seconds,
                step_stages=plan.step_stages,
                target_type=plan.target_type,
                engine=plan.engine,
                target_plan=plan.target_plan,
                cleanup_mode=plan.cleanup_mode,
                stream_config=plan.stream_config,
                request_timeout_ms=plan.request_timeout_ms,
                total_timeout_ms=plan.total_timeout_ms,
                should_cancel=should_cancel,
            )
            return IsolatedOutcome(kind="PERFORMANCE_RESULT", result=result.to_worker_wire()), None

        process = self._performance_isolation_factory(plan)
        deadline = _deadline_from(started.started_at, plan.total_timeout_ms)
        try:
            process.start()
            last_control_check = 0.0
            cancel_requested = False
            cancel_deadline: float | None = None
            while process.is_alive():
                if process.poll(PIPE_POLL_SECONDS):
                    break
                now = self._clock()
                if deadline is not None and now >= deadline:
                    process.terminate()
                    process.join(timeout=5.0)
                    return IsolatedOutcome(kind="TOTAL_TIMEOUT"), None
                if cancel_requested:
                    if cancel_deadline is not None and now >= cancel_deadline:
                        process.terminate()
                        process.join(timeout=5.0)
                        return IsolatedOutcome(
                            kind="CANCEL_UNRESPONSIVE",
                            error_type="CANCEL_REQUESTED",
                            error_message=CANCEL_UNRESPONSIVE_ERROR_MESSAGE,
                        ), None
                    continue
                if now - last_control_check >= self._force_stop_poll_seconds:
                    last_control_check = now
                    control, failure = self._check_control(
                        delivery_tag,
                        envelope.run_id,
                        envelope.message_id,
                        plan.case_run_id,
                    )
                    if failure is not None:
                        process.terminate()
                        process.join(timeout=5.0)
                        return None, failure
                    if control == "FORCE_STOP":
                        process.terminate()
                        process.join(timeout=5.0)
                        return IsolatedOutcome(kind="FORCE_STOPPED"), None
                    if control == "CANCEL":
                        try:
                            process.request_cancel()
                        except Exception:  # noqa: BLE001 - cancellation is fail-closed
                            process.terminate()
                            process.join(timeout=5.0)
                            return IsolatedOutcome(
                                kind="CANCEL_UNRESPONSIVE",
                                error_type="CANCEL_REQUESTED",
                                error_message=CANCEL_UNRESPONSIVE_ERROR_MESSAGE,
                            ), None
                        cancel_requested = True
                        cancel_deadline = now + self._scenario_cancel_wait_seconds
            process.poll(PIPE_POLL_SECONDS)
            outcome = process.outcome()
            if outcome is None:
                if cancel_requested:
                    return IsolatedOutcome(
                        kind="CANCEL_UNRESPONSIVE",
                        error_type="CANCEL_REQUESTED",
                        error_message=CANCEL_UNRESPONSIVE_ERROR_MESSAGE,
                    ), None
                if deadline is not None and self._clock() >= deadline:
                    return IsolatedOutcome(kind="TOTAL_TIMEOUT"), None
                return IsolatedOutcome(
                    kind="EXECUTION_ERROR",
                    error_type="PERFORMANCE_EXECUTOR_ERROR",
                    error_message="性能隔离执行异常终止",
                ), None
            return outcome, None
        finally:
            process.close()

    def _complete_performance_outcome(
        self,
        delivery_tag: object,
        envelope: RunTaskEnvelopeV1,
        plan: PerformanceExecutionPlanResult,
        outcome: IsolatedOutcome,
    ) -> ConsumeOutcome:
        if outcome.kind == "TOTAL_TIMEOUT":
            return self._complete_total_timeout(
                delivery_tag,
                envelope.run_id,
                envelope.message_id,
                plan.case_run_id,
            )
        if outcome.kind in {"CANCELLED", "CANCEL_UNRESPONSIVE", "FORCE_STOPPED"}:
            return self._complete_cancelled(
                delivery_tag,
                envelope.run_id,
                envelope.message_id,
                plan.case_run_id,
                error_type=(
                    FORCE_STOPPED_ERROR_TYPE
                    if outcome.kind == "FORCE_STOPPED"
                    else "CANCEL_REQUESTED"
                ),
                error_message=(
                    FORCE_STOPPED_ERROR_MESSAGE
                    if outcome.kind == "FORCE_STOPPED"
                    else outcome.error_message or CANCEL_REQUESTED_ERROR_MESSAGE
                ),
            )
        if outcome.kind != "PERFORMANCE_RESULT" or not isinstance(outcome.result, Mapping):
            self._forget_performance_result(envelope.run_id, envelope.message_id)
            return self._complete_execution_error(
                delivery_tag,
                envelope.run_id,
                envelope.message_id,
                plan.case_run_id,
                outcome.error_type or "PERFORMANCE_EXECUTOR_ERROR",
                outcome.error_message or "性能执行失败",
            )
        wall_duration_ms = outcome.result.get("wall_duration_ms")
        samples = outcome.result.get("samples")
        if type(wall_duration_ms) is not int or not isinstance(samples, list):
            self._forget_performance_result(envelope.run_id, envelope.message_id)
            return self._complete_execution_error(
                delivery_tag,
                envelope.run_id,
                envelope.message_id,
                plan.case_run_id,
                "PERFORMANCE_RESULT_INVALID",
                "性能执行结果无效",
            )
        try:
            complete = self.client.performance_complete(
                self.identity,
                envelope.run_id,
                envelope.message_id,
                plan.case_run_id,
                wall_duration_ms=wall_duration_ms,
                samples=samples,
            )
        except AuthenticationError:
            self._forget_performance_result(envelope.run_id, envelope.message_id)
            self._reject(delivery_tag)
            self._stop_consuming()
            return ConsumeOutcome.STOPPED_AUTH
        except TransportError:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        except (ProtocolError, RunnerError):
            self._forget_performance_result(envelope.run_id, envelope.message_id)
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        except Exception:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        if complete.status not in {"SUCCESS", "FAILED"}:
            self._forget_performance_result(envelope.run_id, envelope.message_id)
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        self._forget_performance_result(envelope.run_id, envelope.message_id)
        self.channel.basic_ack(delivery_tag=delivery_tag)
        return ConsumeOutcome.ACKED

    def _forget_performance_result(self, run_id: str, message_id: str) -> None:
        self._performance_result_cache.pop((run_id, message_id), None)
        if self._performance_result_store is not None:
            try:
                self._performance_result_store.delete(run_id, message_id)
            except RunnerError:
                # Backend is already terminal in the success path. A stale safe sample spool
                # must not turn that acknowledgement into a repeated load.
                pass

    def _check_cancellation(
        self,
        delivery_tag: object,
        run_id: str,
        message_id: str,
        case_run_id: int,
    ) -> tuple[bool, ConsumeOutcome | None]:
        result, failure = self._check_control_state(delivery_tag, run_id, message_id, case_run_id)
        if failure is not None or result is None:
            return False, failure
        return result.cancellation_requested, None

    def _check_control(
        self,
        delivery_tag: object,
        run_id: str,
        message_id: str,
        case_run_id: int,
    ) -> tuple[str | None, ConsumeOutcome | None]:
        """返回 'FORCE_STOP' | 'CANCEL' | None 与已应用的失败处置。"""

        result, failure = self._check_control_state(delivery_tag, run_id, message_id, case_run_id)
        if failure is not None or result is None:
            return None, failure
        if result.force_stop_requested:
            return "FORCE_STOP", None
        if result.cancellation_requested:
            return "CANCEL", None
        return None, None

    def _check_control_state(
        self,
        delivery_tag: object,
        run_id: str,
        message_id: str,
        case_run_id: int,
    ) -> tuple[ExecutionCancellationResult | None, ConsumeOutcome | None]:
        try:
            cancellation = self.client.get_execution_cancellation(
                self.identity,
                run_id,
                message_id,
                case_run_id,
            )
        except AuthenticationError:
            self._reject(delivery_tag)
            self._stop_consuming()
            return None, ConsumeOutcome.STOPPED_AUTH
        except TransportError:
            self._nack(delivery_tag)
            return None, ConsumeOutcome.REQUEUED
        except (ProtocolError, RunnerError):
            self._reject(delivery_tag)
            return None, ConsumeOutcome.REJECTED
        except Exception:
            self._reject(delivery_tag)
            return None, ConsumeOutcome.REJECTED
        if not isinstance(cancellation, ExecutionCancellationResult):
            self._reject(delivery_tag)
            return None, ConsumeOutcome.REJECTED
        return cancellation, None

    def _execute_with_retries(
        self,
        delivery_tag: object,
        run_id: str,
        message_id: str,
        case_run_id: int,
        request: object,
        retry_policy: RetryPolicy,
        *,
        deadline: float | None = None,
    ) -> tuple[dict[str, object] | None, ConsumeOutcome | None]:
        try:
            retry_policy = validate_retry_policy(retry_policy)
        except ProtocolError:
            self._reject(delivery_tag)
            return None, ConsumeOutcome.REJECTED
        retry_count = 0
        while True:
            if deadline is not None and self._clock() >= deadline:
                return None, self._complete_total_timeout(
                    delivery_tag,
                    run_id,
                    message_id,
                    case_run_id,
                    retry_count=retry_count,
                )
            result: ApiExecutionResult | None = None
            try:
                if self.executor is None:
                    raise RunnerError("API Executor 尚未配置")
                outcome, control_failure = self._execute_isolated(
                    delivery_tag,
                    run_id,
                    message_id,
                    case_run_id,
                    request,
                    deadline,
                )
                if control_failure is not None:
                    return None, control_failure
                if outcome.kind == "FORCE_STOPPED":
                    return None, self._complete_cancelled(
                        delivery_tag,
                        run_id,
                        message_id,
                        case_run_id,
                        retry_count=retry_count,
                        error_type=FORCE_STOPPED_ERROR_TYPE,
                        error_message=FORCE_STOPPED_ERROR_MESSAGE,
                    )
                if outcome.kind == "TOTAL_TIMEOUT":
                    return None, self._complete_total_timeout(
                        delivery_tag,
                        run_id,
                        message_id,
                        case_run_id,
                        retry_count=retry_count,
                    )
                if deadline is not None and self._clock() >= deadline:
                    return None, self._complete_total_timeout(
                        delivery_tag,
                        run_id,
                        message_id,
                        case_run_id,
                        retry_count=retry_count,
                    )
                response_wire = (
                    outcome.result.get("response")
                    if outcome.kind == "API_PIPELINE_RESULT" and outcome.result is not None
                    else outcome.result
                )
                if outcome.kind in {"HTTP_RESPONSE", "API_PIPELINE_RESULT"} and isinstance(
                    response_wire, Mapping
                ):
                    result = ApiExecutionResult(
                        status_code=response_wire["status_code"],
                        json_body=response_wire.get("json_body"),
                        text=response_wire.get("text"),
                        headers=dict(response_wire.get("headers") or {}),
                        cookies=dict(response_wire.get("cookies") or {}),
                        elapsed_ms=int(response_wire.get("elapsed_ms") or 0),
                        response_time_ms=int(response_wire.get("response_time_ms") or 0),
                        body_size=0,
                    )
                completion_kwargs = self._completion_kwargs_from_outcome(outcome)
            except KeyboardInterrupt:
                if self._stop_event is not None:
                    self._stop_event.set()
                completion_kwargs = {
                    "outcome": "EXECUTION_ERROR",
                    "error_type": "EXECUTOR_INTERRUPTED",
                    "error_message": "Runner 收到停止信号",
                }
            except TargetTimeoutError as exc:
                completion_kwargs = {
                    "outcome": "TIMEOUT",
                    "error_type": exc.error_type,
                    "error_message": str(exc),
                }
                action_traces = list(getattr(exc, "action_traces", ()))
                if action_traces:
                    completion_kwargs["action_traces"] = action_traces
            except TargetExecutionError as exc:
                completion_kwargs = {
                    "outcome": "EXECUTION_ERROR",
                    "error_type": exc.error_type,
                    "error_message": str(exc),
                }
                action_traces = list(getattr(exc, "action_traces", ()))
                if action_traces:
                    completion_kwargs["action_traces"] = action_traces
            except ExecutionError as exc:
                completion_kwargs = {
                    "outcome": "EXECUTION_ERROR",
                    "error_type": exc.error_type,
                    "error_message": str(exc),
                }
                action_traces = list(getattr(exc, "action_traces", ()))
                if action_traces:
                    completion_kwargs["action_traces"] = action_traces
            except Exception:
                completion_kwargs = {
                    "outcome": "EXECUTION_ERROR",
                    "error_type": "EXECUTOR_ERROR",
                    "error_message": "API Executor 未处理异常",
                }

            retry_reason = self._retry_reason(result, completion_kwargs)
            if (
                retry_count >= retry_policy.max_retries
                or retry_reason is None
                or retry_reason not in retry_policy.retry_on
            ):
                internal_retry_count = completion_kwargs.get("retry_count", 0)
                if type(internal_retry_count) is not int:
                    internal_retry_count = 0
                completion_kwargs["retry_count"] = min(3, retry_count + internal_retry_count)
                return completion_kwargs, None

            control, control_failure = self._check_control(
                delivery_tag,
                run_id,
                message_id,
                case_run_id,
            )
            if control_failure is not None:
                return None, control_failure
            if control is not None:
                return None, self._complete_control_result(
                    delivery_tag,
                    run_id,
                    message_id,
                    case_run_id,
                    control,
                    retry_count=retry_count,
                )

            finish, control, control_failure = self._wait_retry_with_control(
                delivery_tag,
                run_id,
                message_id,
                case_run_id,
                retry_policy,
                retry_count,
                deadline=deadline,
            )
            if control_failure is not None:
                return None, control_failure
            if control is not None:
                return None, self._complete_control_result(
                    delivery_tag,
                    run_id,
                    message_id,
                    case_run_id,
                    control,
                    retry_count=retry_count,
                )
            if finish:
                completion_kwargs["retry_count"] = retry_count
                return completion_kwargs, None
            retry_count += 1

    @staticmethod
    def _completion_kwargs_from_outcome(outcome: IsolatedOutcome) -> dict[str, object]:
        if outcome.kind == "HTTP_RESPONSE" and outcome.result is not None:
            return {"outcome": "HTTP_RESPONSE", "response": outcome.result}
        if outcome.kind == "API_PIPELINE_RESULT" and outcome.result is not None:
            response = outcome.result.get("response")
            resources = outcome.result.get("resources", [])
            action_traces = outcome.result.get("action_traces", [])
            if (
                not isinstance(response, Mapping)
                or not isinstance(resources, list)
                or not isinstance(action_traces, list)
            ):
                return {
                    "outcome": "EXECUTION_ERROR",
                    "error_type": "EXECUTOR_ERROR",
                    "error_message": "API pipeline 响应无效",
                }
            return {
                "outcome": "HTTP_RESPONSE",
                "response": response,
                "resources": resources,
                "action_traces": action_traces,
                "retry_count": outcome.result.get("retry_count", 0),
                "_cleanup_pending": outcome.result.get("cleanup_pending", False),
                "_cleanup_context": outcome.result.get("cleanup_context"),
            }
        action_traces = (
            outcome.result.get("action_traces", []) if isinstance(outcome.result, Mapping) else []
        )
        retry_count = (
            outcome.result.get("retry_count", 0) if isinstance(outcome.result, Mapping) else 0
        )
        if outcome.kind == "TARGET_TIMEOUT":
            result = {
                "outcome": "TIMEOUT",
                "error_type": "TARGET_TIMEOUT",
                "error_message": "目标执行超时",
                "retry_count": retry_count,
            }
            if action_traces:
                result["action_traces"] = action_traces
            return result
        if outcome.kind == "TARGET_NETWORK_ERROR":
            result = {
                "outcome": "EXECUTION_ERROR",
                "error_type": "TARGET_NETWORK_ERROR",
                "error_message": "目标 API 网络请求失败",
                "retry_count": retry_count,
            }
            if action_traces:
                result["action_traces"] = action_traces
            return result
        result = {
            "outcome": "EXECUTION_ERROR",
            "error_type": outcome.error_type or "EXECUTOR_ERROR",
            "error_message": outcome.error_message or "API Executor 未处理异常",
            "retry_count": retry_count,
        }
        if action_traces:
            result["action_traces"] = action_traces
        return result

    def _execute_isolated(
        self,
        delivery_tag: object,
        run_id: str,
        message_id: str,
        case_run_id: int,
        request: object,
        deadline: float | None,
    ) -> tuple[IsolatedOutcome, ConsumeOutcome | None]:
        """执行一次目标请求；有隔离边界时监控 Force Stop 与 Run 总超时。"""

        if self._isolation_factory is None:
            from runner.api_pipeline import ApiPipelineCleanupTask

            if self.executor is None:
                raise RunnerError("API Executor 尚未配置")
            if isinstance(request, ApiPipelineCleanupTask):
                from runner.api_pipeline import ApiCasePipelineExecutor

                result = ApiCasePipelineExecutor.execute_cleanup_task(
                    request,
                    api_executor=self.executor,  # type: ignore[arg-type]
                )
                return IsolatedOutcome(kind="API_CLEANUP_RESULT", result=result.to_wire()), None
            if getattr(request, "has_pipeline", False):
                from runner.api_pipeline import (
                    ApiCasePipelineExecutor,
                    ApiPipelineExecutionResult,
                )

                result = ApiCasePipelineExecutor(request, api_executor=self.executor).execute()
                if not isinstance(result, ApiPipelineExecutionResult):
                    raise RunnerError("API pipeline 返回类型无效")
                return IsolatedOutcome(
                    kind="API_PIPELINE_RESULT", result=result.to_worker_wire()
                ), None
            else:
                result = self.executor.execute(request)  # type: ignore[arg-type]
            return IsolatedOutcome(kind="HTTP_RESPONSE", result=result.to_wire()), None
        process = self._isolation_factory(request)
        try:
            process.start()
            last_force_check = 0.0
            while process.is_alive():
                if process.poll(PIPE_POLL_SECONDS):
                    break
                now = self._clock()
                if now - last_force_check >= self._force_stop_poll_seconds:
                    last_force_check = now
                    control, failure = self._check_control(
                        delivery_tag,
                        run_id,
                        message_id,
                        case_run_id,
                    )
                    if failure is not None:
                        process.terminate()
                        process.join(timeout=5.0)
                        return (
                            IsolatedOutcome(
                                kind="EXECUTION_ERROR",
                                error_type="EXECUTOR_ERROR",
                                error_message="执行已中断",
                            ),
                            failure,
                        )
                    if control == "FORCE_STOP":
                        process.terminate()
                        process.join(timeout=5.0)
                        return IsolatedOutcome(kind="FORCE_STOPPED"), None
                    # 协作取消不中断阻塞中的 HTTP（沿用安全检查点语义）。
                if deadline is not None and self._clock() >= deadline:
                    process.terminate()
                    process.join(timeout=5.0)
                    return IsolatedOutcome(kind="TOTAL_TIMEOUT"), None
            process.poll(PIPE_POLL_SECONDS)
            outcome = process.outcome()
            if outcome is None:
                if deadline is not None and self._clock() >= deadline:
                    return IsolatedOutcome(kind="TOTAL_TIMEOUT"), None
                return (
                    IsolatedOutcome(
                        kind="EXECUTION_ERROR",
                        error_type="EXECUTOR_ERROR",
                        error_message="隔离执行异常终止",
                    ),
                    None,
                )
            return outcome, None
        finally:
            process.close()

    def _execute_deferred_cleanup(
        self,
        delivery_tag: object,
        run_id: str,
        message_id: str,
        case_run_id: int,
        plan: object,
        context: object,
        resources: object,
        outcome: str,
        deadline: float | None,
    ) -> tuple[list[Mapping[str, Any]] | None, ConsumeOutcome | None]:
        from runner.api_pipeline import ApiPipelineCleanupTask

        if not isinstance(context, Mapping) or not isinstance(resources, list):
            return None, ConsumeOutcome.REJECTED
        task = ApiPipelineCleanupTask(
            plan=plan,
            context=context,
            resources=tuple(item for item in resources if isinstance(item, Mapping)),
            outcome=outcome,
        )
        if len(task.resources) != len(resources):
            return None, ConsumeOutcome.REJECTED
        cleanup_result, failure = self._execute_isolated(
            delivery_tag,
            run_id,
            message_id,
            case_run_id,
            task,
            deadline,
        )
        if failure is not None:
            return None, failure
        if cleanup_result.kind == "FORCE_STOPPED":
            return None, self._complete_cancelled(
                delivery_tag,
                run_id,
                message_id,
                case_run_id,
                error_type=FORCE_STOPPED_ERROR_TYPE,
                error_message=FORCE_STOPPED_ERROR_MESSAGE,
            )
        if cleanup_result.kind == "TOTAL_TIMEOUT":
            return None, self._complete_total_timeout(delivery_tag, run_id, message_id, case_run_id)
        if (
            cleanup_result.kind != "API_CLEANUP_RESULT"
            or not isinstance(cleanup_result.result, Mapping)
            or not isinstance(cleanup_result.result.get("resources"), list)
        ):
            return None, self._complete_execution_error(
                delivery_tag,
                run_id,
                message_id,
                case_run_id,
                "CLEANUP_EXECUTION_ERROR",
                "一个或多个 Cleanup 执行失败",
            )
        return list(cleanup_result.result["resources"]), None

    def _complete_total_timeout(
        self,
        delivery_tag: object,
        run_id: str,
        message_id: str,
        case_run_id: int,
        *,
        retry_count: int = 0,
    ) -> ConsumeOutcome:
        try:
            complete = self.client.execution_complete(
                self.identity,
                run_id,
                message_id,
                case_run_id,
                outcome="TIMEOUT",
                error_type=TOTAL_TIMEOUT_ERROR_TYPE,
                error_message=TOTAL_TIMEOUT_ERROR_MESSAGE,
                retry_count=retry_count,
            )
        except AuthenticationError:
            self._reject(delivery_tag)
            self._stop_consuming()
            return ConsumeOutcome.STOPPED_AUTH
        except TransportError:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        except (ProtocolError, RunnerError):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        except Exception:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        return self._complete_disposition(delivery_tag, complete)

    def _complete_execution_error(
        self,
        delivery_tag: object,
        run_id: str,
        message_id: str,
        case_run_id: int,
        error_type: str,
        error_message: str,
        *,
        retry_count: int = 0,
    ) -> ConsumeOutcome:
        try:
            complete = self.client.execution_complete(
                self.identity,
                run_id,
                message_id,
                case_run_id,
                outcome="EXECUTION_ERROR",
                error_type=error_type,
                error_message=error_message,
                retry_count=retry_count,
            )
        except AuthenticationError:
            self._reject(delivery_tag)
            self._stop_consuming()
            return ConsumeOutcome.STOPPED_AUTH
        except TransportError:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        except (ProtocolError, RunnerError):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        except Exception:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        return self._complete_disposition(delivery_tag, complete)

    def _complete_control_result(
        self,
        delivery_tag: object,
        run_id: str,
        message_id: str,
        case_run_id: int,
        control: str,
        *,
        retry_count: int = 0,
        resources: object = None,
    ) -> ConsumeOutcome:
        if control == "FORCE_STOP":
            return self._complete_cancelled(
                delivery_tag,
                run_id,
                message_id,
                case_run_id,
                retry_count=retry_count,
                error_type=FORCE_STOPPED_ERROR_TYPE,
                error_message=FORCE_STOPPED_ERROR_MESSAGE,
                resources=resources,
            )
        return self._complete_cancelled(
            delivery_tag,
            run_id,
            message_id,
            case_run_id,
            retry_count=retry_count,
            resources=resources,
        )

    @staticmethod
    def _retry_reason(
        result: ApiExecutionResult | None, completion_kwargs: dict[str, object]
    ) -> str | None:
        if result is not None and 500 <= result.status_code <= 599:
            return "HTTP_5XX"
        if completion_kwargs.get("outcome") == "TIMEOUT":
            return "TARGET_TIMEOUT"
        if (
            completion_kwargs.get("outcome") == "EXECUTION_ERROR"
            and completion_kwargs.get("error_type") == "TARGET_NETWORK_ERROR"
        ):
            return "TARGET_NETWORK_ERROR"
        return None

    def _wait_retry_with_control(
        self,
        delivery_tag: object,
        run_id: str,
        message_id: str,
        case_run_id: int,
        retry_policy: RetryPolicy,
        retry_count: int,
        *,
        deadline: float | None = None,
    ) -> tuple[bool, str | None, ConsumeOutcome | None]:
        """退避等待；(finish, control, failure)：finish 表示停止并直接完成。"""

        delay_ms = min(30_000, retry_policy.backoff_ms * (2**retry_count))
        if delay_ms <= 0:
            if deadline is not None and self._clock() >= deadline:
                return (
                    False,
                    None,
                    self._complete_total_timeout(
                        delivery_tag,
                        run_id,
                        message_id,
                        case_run_id,
                        retry_count=retry_count,
                    ),
                )
            if self._stop_event is not None and self._stop_event.is_set():
                return True, None, None
            control, failure = self._check_control(delivery_tag, run_id, message_id, case_run_id)
            return False, control, failure
        delay_seconds = delay_ms / 1000.0
        if self._stop_event is None:
            # execute-once 调试路径：保持原有单次 sleep 语义，sleep 后复查控制状态。
            if deadline is not None:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return (
                        False,
                        None,
                        self._complete_total_timeout(
                            delivery_tag,
                            run_id,
                            message_id,
                            case_run_id,
                            retry_count=retry_count,
                        ),
                    )
                delay_seconds = min(delay_seconds, remaining)
            self._sleeper(delay_seconds)
            if deadline is not None and self._clock() >= deadline:
                return (
                    False,
                    None,
                    self._complete_total_timeout(
                        delivery_tag,
                        run_id,
                        message_id,
                        case_run_id,
                        retry_count=retry_count,
                    ),
                )
            control, failure = self._check_control(delivery_tag, run_id, message_id, case_run_id)
            return False, control, failure
        remaining = delay_seconds
        while True:
            if deadline is not None:
                deadline_remaining = deadline - self._clock()
                if deadline_remaining <= 0:
                    return (
                        False,
                        None,
                        self._complete_total_timeout(
                            delivery_tag,
                            run_id,
                            message_id,
                            case_run_id,
                            retry_count=retry_count,
                        ),
                    )
                step = min(0.5, remaining, deadline_remaining)
            else:
                step = min(0.5, remaining)
            if self._stop_event.wait(step):
                return True, None, None
            remaining -= step
            if deadline is not None and self._clock() >= deadline:
                return (
                    False,
                    None,
                    self._complete_total_timeout(
                        delivery_tag,
                        run_id,
                        message_id,
                        case_run_id,
                        retry_count=retry_count,
                    ),
                )
            control, failure = self._check_control(delivery_tag, run_id, message_id, case_run_id)
            if failure is not None:
                return True, None, failure
            if control is not None:
                return True, control, None
            if remaining <= 0:
                return False, None, None

    def _complete_disposition(self, delivery_tag: object, complete: object) -> ConsumeOutcome:
        """把 completion 的结果分类；未确认时绝不 ACK。"""

        if not isinstance(complete, ExecutionCompleteResult):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        if complete.outcome == "TIMEOUT":
            valid_statuses = {"TIMEOUT"}
        elif complete.outcome == "CANCELLED":
            valid_statuses = {"CANCELLED"}
        elif complete.outcome in {"HTTP_RESPONSE", "EXECUTION_ERROR"}:
            valid_statuses = {"SUCCESS", "FAILED", "REVIEW"}
        else:
            valid_statuses = set()
        if (
            complete.run_status not in valid_statuses - {"REVIEW"}
            or complete.case_run_status not in valid_statuses
        ):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        self.channel.basic_ack(delivery_tag=delivery_tag)
        return ConsumeOutcome.ACKED

    def _complete_cancelled(
        self,
        delivery_tag: object,
        run_id: str,
        message_id: str,
        case_run_id: int,
        *,
        retry_count: int = 0,
        error_type: str = "CANCEL_REQUESTED",
        error_message: str = CANCEL_REQUESTED_ERROR_MESSAGE,
        resources: object = None,
    ) -> ConsumeOutcome:
        safe_resources = (
            list(resources)
            if isinstance(resources, list) and all(isinstance(item, Mapping) for item in resources)
            else None
        )
        completion_kwargs: dict[str, object] = {
            "outcome": "CANCELLED",
            "error_type": error_type,
            "error_message": error_message,
            "retry_count": retry_count,
        }
        if safe_resources is not None:
            completion_kwargs["resources"] = safe_resources
        try:
            complete = self.client.execution_complete(
                self.identity,
                run_id,
                message_id,
                case_run_id,
                **completion_kwargs,
            )
        except AuthenticationError:
            self._reject(delivery_tag)
            self._stop_consuming()
            return ConsumeOutcome.STOPPED_AUTH
        except TransportError:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        except (ProtocolError, RunnerError):
            self._reject(delivery_tag)
            return ConsumeOutcome.REJECTED
        except Exception:
            self._nack(delivery_tag)
            return ConsumeOutcome.REQUEUED
        return self._complete_disposition(delivery_tag, complete)

    def _on_message(self, channel: object, method: Any, properties: Any, body: object) -> None:
        del channel, properties
        self.process_delivery(method.delivery_tag, body)

    def _reject(self, delivery_tag: object) -> None:
        self.channel.basic_reject(delivery_tag=delivery_tag, requeue=False)

    def _nack(self, delivery_tag: object) -> None:
        self.channel.basic_nack(delivery_tag=delivery_tag, requeue=True)

    def _stop_consuming(self) -> None:
        stop = getattr(self.channel, "stop_consuming", None)
        if callable(stop):
            stop()
