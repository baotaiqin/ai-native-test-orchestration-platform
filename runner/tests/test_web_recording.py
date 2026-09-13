from __future__ import annotations

import json
from datetime import UTC, datetime
from threading import Event

import pytest

from runner.consumer import ConsumeOutcome, RunnerTaskConsumer
from runner.envelope import (
    WebRecordingTaskEnvelopeV1,
    parse_task_or_recording_envelope,
    parse_web_recording_envelope,
)
from runner.errors import EnvelopeError, ProtocolError
from runner.executors.web_recording import WebRecordingExecutor, _build_locator_candidates
from runner.isolation import IsolatedOutcome
from runner.models import (
    HttpResponse,
    RunnerIdentity,
    WebRecordingClaimResult,
    WebRecordingCompleteResult,
    WebRecordingControlResult,
    WebRecordingExecutionPlanResult,
    WebRecordingExecutionStartResult,
    WebRecordingSession,
)
from runner.protocol import RunnerClient

IDENTITY = RunnerIdentity("runner-001", "credential-value-123456789")
NOW = datetime(2026, 9, 4, 1, 0, tzinfo=UTC)


def _envelope(**changes: object) -> bytes:
    body: dict[str, object] = {
        "schema_version": 1,
        "task_type": "WEB_RECORDING",
        "recording_id": "recording-001",
        "message_id": "message-001",
        "runner_id": "runner-001",
        "project_id": 7,
        "attempt": 1,
        "enqueued_at": "2026-09-04T01:00:00Z",
    }
    body.update(changes)
    return json.dumps(body).encode()


def _plan() -> WebRecordingExecutionPlanResult:
    return WebRecordingExecutionPlanResult(
        1,
        "WEB_RECORDING",
        "recording-001",
        "message-001",
        "runner-001",
        7,
        None,
        "https://example.test/start?ordinary=1",
        "CHROME",
        False,
        WebRecordingSession(3, {"cookies": []}),
        False,
        None,
        None,
    )


class _Transport:
    def __init__(self, *responses: HttpResponse) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def post(self, url, *, json_body, headers, timeout):
        self.calls.append({"method": "POST", "url": url, "json": dict(json_body)})
        return self.responses.pop(0)

    def get(self, url, *, headers, timeout):
        self.calls.append({"method": "GET", "url": url, "json": None})
        return self.responses.pop(0)


def _claim(status: str = "QUEUED") -> dict[str, object]:
    return {
        "schema_version": 1,
        "recording_id": "recording-001",
        "message_id": "message-001",
        "runner_id": "runner-001",
        "status": status,
        "claimed_at": None if status == "CANCELLED" else NOW.isoformat(),
        "idempotent": False,
    }


def _start(status: str = "RUNNING") -> dict[str, object]:
    return {
        "schema_version": 1,
        "recording_id": "recording-001",
        "message_id": "message-001",
        "runner_id": "runner-001",
        "status": status,
        "started_at": NOW.isoformat() if status == "RUNNING" else None,
        "idempotent": False,
    }


def _control() -> dict[str, object]:
    return {
        "schema_version": 1,
        "recording_id": "recording-001",
        "message_id": "message-001",
        "runner_id": "runner-001",
        "status": "RUNNING",
        "stop_requested": False,
        "cancellation_requested": False,
    }


def _complete(outcome: str = "COMPLETED", count: int = 1) -> dict[str, object]:
    return {
        "schema_version": 1,
        "recording_id": "recording-001",
        "message_id": "message-001",
        "runner_id": "runner-001",
        "outcome": outcome,
        "status": outcome,
        "event_count": count,
        "saved_session_profile_id": None,
        "completed_at": NOW.isoformat(),
        "idempotent": False,
    }


def test_recording_envelope_strict_union_and_runner_check() -> None:
    parsed = parse_task_or_recording_envelope(_envelope(), expected_runner_id="runner-001")
    assert isinstance(parsed, WebRecordingTaskEnvelopeV1)
    with pytest.raises(EnvelopeError):
        parse_web_recording_envelope(_envelope(extra="nope"))
    with pytest.raises(EnvelopeError):
        parse_web_recording_envelope(_envelope(runner_id="other"), expected_runner_id="runner-001")


def test_recording_protocol_exact_calls_and_safe_models() -> None:
    transport = _Transport(
        HttpResponse(200, _claim()),
        HttpResponse(
            200,
            {
                "schema_version": 1,
                "task_type": "WEB_RECORDING",
                "recording_id": "recording-001",
                "message_id": "message-001",
                "runner_id": "runner-001",
                "project_id": 7,
                "environment_id": None,
                "start_url": "https://example.test/start?ordinary=1",
                "browser": "CHROME",
                "headless": False,
                "session": {"profile_id": 3, "storage_state": {"cookies": []}},
                "save_session": False,
                "save_session_name": None,
                "save_session_expires_at": None,
            },
        ),
        HttpResponse(200, _start()),
        HttpResponse(200, _control()),
        HttpResponse(200, _complete()),
    )
    client = RunnerClient("https://backend.test/", transport, max_attempts=1)
    assert client.web_recording_claim(IDENTITY, "recording-001", "message-001").status == "QUEUED"
    plan = client.get_web_recording_execution_plan(IDENTITY, "recording-001", "message-001")
    client.web_recording_execution_start(IDENTITY, "recording-001", "message-001")
    client.get_web_recording_control(IDENTITY, "recording-001", "message-001")
    event = {
        "event_type": "CLICK",
        "sequence": 1,
        "relative_time_ms": 0,
        "page_url": "https://example.test/start?token=hidden",
        "title": "Start",
        "target_url": None,
        "locator_candidates": [{"strategy": "css", "value": "#start", "priority": 1}],
        "value": None,
        "key": None,
    }
    result = client.web_recording_execution_complete(
        IDENTITY, "recording-001", "message-001", outcome="COMPLETED", events=[event]
    )
    assert plan.session is not None
    assert result.status == "COMPLETED"
    assert "message_id='message-001'" in repr(plan)
    assert "hidden" not in repr(plan)
    assert transport.calls[1]["url"].endswith("message_id=message-001")
    assert transport.calls[-1]["json"]["events"][0]["page_url"] == "https://example.test/start"


def test_recording_protocol_rejects_invalid_datetime_and_complete_status() -> None:
    transport = _Transport(HttpResponse(200, {**_claim(), "claimed_at": "2026-09-04T01:00:00"}))
    client = RunnerClient("https://backend.test", transport, max_attempts=1)
    with pytest.raises(ProtocolError):
        client.web_recording_claim(IDENTITY, "recording-001", "message-001")


def test_recording_complete_redacts_credential_from_error_wire() -> None:
    transport = _Transport(HttpResponse(200, _complete("FAILED", 0)))
    client = RunnerClient("https://backend.test", transport, max_attempts=1)
    client.web_recording_execution_complete(
        IDENTITY,
        "recording-001",
        "message-001",
        outcome="FAILED",
        events=[],
        error_type="TARGET_ERROR",
        error_message=f"credential={IDENTITY.credential}",
    )
    wire_message = transport.calls[0]["json"]["error_message"]
    assert IDENTITY.credential not in wire_message
    assert "REDACTED" in wire_message


class _Page:
    def __init__(self) -> None:
        self.closed = False
        self.url = "https://example.test/start?token=not-recorded"
        self.binding: object | None = None

    def on(self, _name: str, _callback: object) -> None:
        return None

    def goto(self, _url: str) -> None:
        return None

    def is_closed(self) -> bool:
        return self.closed

    def title(self) -> str:
        return "Safe title"


class _Context:
    def __init__(self) -> None:
        self.page = _Page()
        self.binding = None
        self.closed = False

    def new_page(self) -> _Page:
        return self.page

    def expose_binding(self, _name: str, callback: object) -> None:
        self.binding = callback

    def add_init_script(self, **_kwargs: object) -> None:
        return None

    def on(self, _name: str, _callback: object) -> None:
        return None

    def storage_state(self) -> dict[str, object]:
        return {"cookies": []}

    def close(self) -> None:
        self.closed = True


class _Browser:
    def __init__(self, context: _Context) -> None:
        self.context = context
        self.closed = False

    def new_context(self, **_kwargs: object) -> _Context:
        return self.context

    def is_connected(self) -> bool:
        return not self.closed

    def close(self) -> None:
        self.closed = True


class _Playwright:
    def __init__(self, browser: _Browser) -> None:
        self.browser = browser
        self.chromium = self

    def __enter__(self) -> _Playwright:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def launch(self, **kwargs: object) -> _Browser:
        assert kwargs["headless"] is False
        return self.browser


def test_recording_executor_captures_safe_events_and_stop_preserves_them() -> None:
    context = _Context()
    browser = _Browser(context)
    calls = 0

    def sleeper(_seconds: float) -> None:
        nonlocal calls
        calls += 1
        assert context.binding is not None
        context.binding(
            None,
            {
                "event_type": "FILL",
                "page_url": "https://example.test/form?secret=hidden",
                "title": "Form",
                "name": "email",
                "id": "email",
                "sensitive": False,
            },
        )
        context.page.closed = True

    result = WebRecordingExecutor(
        playwright_factory=lambda: _Playwright(browser), sleeper=sleeper
    ).execute(_plan(), stop_event=Event())
    assert calls == 1
    assert result.outcome == "COMPLETED"
    assert result.events[0]["event_type"] == "NAVIGATE"
    assert result.events[1]["value"] == "{{recording.email}}"
    assert "hidden" not in repr(result)


def test_recording_executor_cancel_discards_events_and_locator_redacts() -> None:
    event = {
        "event_type": "FILL",
        "name": "password",
        "id": "password",
        "value": "secret-value",
        "sensitive": True,
    }
    assert _build_locator_candidates(event) == []
    cancel = Event()
    cancel.set()
    result = WebRecordingExecutor().execute(_plan(), cancel_event=cancel)
    assert result.outcome == "CANCELLED"
    assert result.events == []
    assert result.storage_state is None


class _Channel:
    def __init__(self) -> None:
        self.acks: list[object] = []
        self.nacks: list[object] = []
        self.rejects: list[object] = []

    def basic_ack(self, **kwargs: object) -> None:
        self.acks.append(kwargs["delivery_tag"])

    def basic_nack(self, **kwargs: object) -> None:
        self.nacks.append(kwargs["delivery_tag"])

    def basic_reject(self, **kwargs: object) -> None:
        self.rejects.append(kwargs["delivery_tag"])


class _Process:
    def __init__(self) -> None:
        self.started = False
        self.closed = 0

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return True

    def poll(self, _timeout: float) -> bool:
        return True

    def outcome(self) -> IsolatedOutcome:
        return IsolatedOutcome(
            "WEB_RECORDING_RESULT",
            {"outcome": "COMPLETED", "events": [], "error_type": None, "error_message": None},
        )

    def close(self) -> None:
        self.closed += 1

    def terminate(self) -> None:
        return None

    def join(self, timeout: float | None = None) -> None:
        del timeout


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def web_recording_claim(self, *_args: object) -> WebRecordingClaimResult:
        self.calls.append("claim")
        return WebRecordingClaimResult(
            1, "recording-001", "message-001", "runner-001", "QUEUED", NOW, False
        )

    def get_web_recording_execution_plan(self, *_args: object) -> WebRecordingExecutionPlanResult:
        self.calls.append("plan")
        return _plan()

    def web_recording_execution_start(self, *_args: object) -> WebRecordingExecutionStartResult:
        self.calls.append("start")
        return WebRecordingExecutionStartResult(
            1, "recording-001", "message-001", "runner-001", "RUNNING", NOW, False
        )

    def get_web_recording_control(self, *_args: object) -> WebRecordingControlResult:
        self.calls.append("control")
        return WebRecordingControlResult(
            1, "recording-001", "message-001", "runner-001", "RUNNING", False, False
        )

    def web_recording_execution_complete(
        self, *_args: object, **kwargs: object
    ) -> WebRecordingCompleteResult:
        self.calls.append("complete")
        assert kwargs["outcome"] == "COMPLETED"
        return WebRecordingCompleteResult(
            1, "recording-001", "message-001", "runner-001", "COMPLETED",
            "COMPLETED", 0, None, NOW, False
        )


class _PlanRaceClient(_RecordingClient):
    def __init__(self) -> None:
        super().__init__()
        self.control_count = 0
        self.completed_outcome: str | None = None

    def get_web_recording_execution_plan(self, *_args: object) -> WebRecordingExecutionPlanResult:
        self.calls.append("plan")
        raise ProtocolError("录制计划当前不可用")

    def get_web_recording_control(self, *_args: object) -> WebRecordingControlResult:
        self.calls.append("control")
        self.control_count += 1
        return WebRecordingControlResult(
            1,
            "recording-001",
            "message-001",
            "runner-001",
            "STOP_REQUESTED",
            self.control_count >= 2,
            self.control_count >= 2,
        )

    def web_recording_execution_complete(
        self, *_args: object, **kwargs: object
    ) -> WebRecordingCompleteResult:
        self.calls.append("complete")
        self.completed_outcome = str(kwargs["outcome"])
        return WebRecordingCompleteResult(
            1,
            "recording-001",
            "message-001",
            "runner-001",
            "CANCELLED",
            "CANCELLED",
            0,
            None,
            NOW,
            False,
        )


def test_recording_consumer_claim_plan_start_child_complete_then_ack() -> None:
    channel = _Channel()
    client = _RecordingClient()
    process = _Process()
    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        IDENTITY,
        {"API": 0, "WEB": 1, "PERFORMANCE": 0},
        web_recording_isolation_factory=lambda _plan: process,
        force_stop_poll_seconds=1.0,
    )
    outcome = consumer.process_execution_delivery(10, _envelope())
    assert outcome is ConsumeOutcome.ACKED
    assert client.calls == [
        "claim", "control", "plan", "start", "control", "control", "complete"
    ]
    assert channel.acks == [10]
    assert channel.nacks == []
    assert channel.rejects == []
    assert process.closed == 1


def test_recording_plan_conflict_rechecks_control_and_settles_cancel() -> None:
    channel = _Channel()
    client = _PlanRaceClient()
    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        IDENTITY,
        {"API": 0, "WEB": 1, "PERFORMANCE": 0},
    )
    outcome = consumer.process_execution_delivery(11, _envelope())
    assert outcome is ConsumeOutcome.ACKED
    assert client.completed_outcome == "CANCELLED"
    assert client.calls == ["claim", "control", "plan", "control", "complete"]
    assert channel.acks == [11]
    assert channel.rejects == []
