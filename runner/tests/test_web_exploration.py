from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from runner.consumer import ConsumeOutcome, RunnerTaskConsumer
from runner.envelope import (
    WebExplorationTaskEnvelopeV1,
    parse_task_or_recording_envelope,
    parse_web_exploration_envelope,
)
from runner.errors import ConfigurationError, EnvelopeError, ProtocolError
from runner.evidence import ValidatedEvidenceArtifact
from runner.executors import web_exploration
from runner.executors.web_exploration import WebExplorationExecutor
from runner.mcp_client import PlaywrightMcpClient
from runner.models import (
    HttpResponse,
    RunnerIdentity,
    WebExecutionSecret,
    WebExplorationClaimResult,
    WebExplorationCompleteResult,
    WebExplorationControlResult,
    WebExplorationDecisionResult,
    WebExplorationExecutionPlanResult,
    WebExplorationStartResult,
)
from runner.protocol import RunnerClient


def _envelope(**changes: object) -> bytes:
    payload: dict[str, object] = {
        "schema_version": 1,
        "task_type": "WEB_EXPLORATION",
        "exploration_id": "exploration-001",
        "message_id": "message-001",
        "runner_id": "runner-001",
        "project_id": 7,
        "attempt": 1,
        "enqueued_at": "2026-09-12T01:00:00Z",
    }
    payload.update(changes)
    return json.dumps(payload).encode()


def _plan() -> WebExplorationExecutionPlanResult:
    return WebExplorationExecutionPlanResult(
        1,
        "WEB_EXPLORATION",
        "exploration-001",
        "message-001",
        "runner-001",
        7,
        "https://example.test/start",
        ("https://example.test",),
        3,
        "查看商品列表",
        ("打开页面",),
        ("商品列表可见",),
        None,
    )


def _control() -> WebExplorationControlResult:
    return WebExplorationControlResult(
        1, "exploration-001", "message-001", "runner-001", "RUNNING", False, False
    )


class _Transport:
    def __init__(self, *responses: HttpResponse) -> None:
        self.responses = list(responses)
        self.last_timeout = None

    def get(self, *_: object, **kwargs: object) -> HttpResponse:
        self.last_timeout = kwargs.get("timeout")
        return self.responses.pop(0)

    def post(self, *_: object, **kwargs: object) -> HttpResponse:
        self.last_timeout = kwargs.get("timeout")
        return self.responses.pop(0)


def _plan_body(start_url: str = "https://example.test/start") -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_type": "WEB_EXPLORATION",
        "exploration_id": "exploration-001",
        "message_id": "message-001",
        "runner_id": "runner-001",
        "project_id": 7,
        "start_url": start_url,
        "allowed_origins": ["https://EXAMPLE.test/"],
        "max_steps": 3,
        "objective": "查看商品列表",
        "planned_steps": ["打开页面"],
        "expected_outcomes": ["商品列表可见"],
        "session": None,
    }


def test_exploration_envelope_uses_a_strict_union_branch() -> None:
    parsed = parse_task_or_recording_envelope(_envelope(), expected_runner_id="runner-001")
    assert isinstance(parsed, WebExplorationTaskEnvelopeV1)
    with pytest.raises(EnvelopeError):
        parse_web_exploration_envelope(_envelope(extra="not-allowed"))
    with pytest.raises(EnvelopeError):
        parse_web_exploration_envelope(
            _envelope(runner_id="other"), expected_runner_id="runner-001"
        )


def test_mcp_client_denies_unsafe_tools_before_start(tmp_path: Path) -> None:
    client = PlaywrightMcpClient(
        runtime_dir=tmp_path.resolve(),
        work_dir=(tmp_path / "work").resolve(),
        allowed_origins=("https://example.test",),
    )
    with pytest.raises(ConfigurationError, match="白名单"):
        client.call_tool("browser_run_code_unsafe", {"code": "() => process.env"})


def test_protocol_normalizes_origins_and_rejects_mismatched_start_url() -> None:
    identity = RunnerIdentity("runner-001", "credential-value-123456789")
    client = RunnerClient(
        "https://backend.test/", _Transport(HttpResponse(200, _plan_body())), max_attempts=1
    )
    plan = client.get_web_exploration_execution_plan(
        identity, "exploration-001", "message-001"
    )
    assert plan.allowed_origins == ("https://example.test",)
    assert plan.headless is True

    visual_body = _plan_body()
    visual_body["headless"] = False
    visual = RunnerClient(
        "https://backend.test/", _Transport(HttpResponse(200, visual_body)), max_attempts=1
    ).get_web_exploration_execution_plan(identity, "exploration-001", "message-001")
    assert visual.headless is False

    mismatched = RunnerClient(
        "https://backend.test/",
        _Transport(HttpResponse(200, _plan_body("https://outside.test/start"))),
        max_attempts=1,
    )
    with pytest.raises(ProtocolError, match="allowed_origins"):
        mismatched.get_web_exploration_execution_plan(
            identity, "exploration-001", "message-001"
        )


def test_protocol_accepts_bounded_exploration_login_secrets_without_repr_leak() -> None:
    identity = RunnerIdentity("runner-001", "credential-value-123456789")
    body = _plan_body()
    body["secrets"] = [
        {"name": "AI_TEST_USERNAME", "value": "managed-login-user"},
        {"name": "AI_TEST_PASSWORD", "value": "managed-login-password"},
    ]
    client = RunnerClient(
        "https://backend.test/", _Transport(HttpResponse(200, body)), max_attempts=1
    )

    plan = client.get_web_exploration_execution_plan(
        identity, "exploration-001", "message-001"
    )

    assert [item.name for item in plan.secrets] == [
        "AI_TEST_USERNAME",
        "AI_TEST_PASSWORD",
    ]
    assert "managed-login" not in repr(plan)


def test_exploration_decision_allows_model_timeout_window() -> None:
    identity = RunnerIdentity("runner-001", "credential-value-123456789")
    transport = _Transport(
        HttpResponse(
            200,
            {
                "schema_version": 1,
                "exploration_id": "exploration-001",
                "message_id": "message-001",
                "sequence": 1,
                "ai_call_id": 44,
                "decision": {
                    "action": "FINISH",
                    "reason": "目标已满足",
                    "element": None,
                    "ref": None,
                    "value": None,
                    "url": None,
                    "expected_observation": None,
                },
            },
        )
    )
    client = RunnerClient(
        "https://backend.test/",
        transport,
        read_timeout_seconds=15,
        max_attempts=1,
    )

    client.request_web_exploration_decision(
        identity,
        "exploration-001",
        "message-001",
        {
            "sequence": 1,
            "page_url": "https://example.test/start",
            "title": "Store",
            "accessibility_snapshot": "- list 'Products'",
            "network_events": [],
            "previous_action": None,
        },
    )

    assert transport.last_timeout is not None
    assert transport.last_timeout.connect == 5.0
    assert transport.last_timeout.read == 630.0


def test_exploration_protocol_error_keeps_safe_backend_detail() -> None:
    identity = RunnerIdentity("runner-001", "credential-value-123456789")
    client = RunnerClient(
        "https://backend.test/",
        _Transport(
            HttpResponse(
                422,
                {
                    "detail": "模型调用超时",
                    "credential": "credential-value-123456789",
                },
            )
        ),
        max_attempts=1,
    )

    with pytest.raises(ProtocolError) as error:
        client.request_web_exploration_decision(
            identity,
            "exploration-001",
            "message-001",
            {
                "sequence": 1,
                "page_url": "https://example.test/start",
                "title": "Store",
                "accessibility_snapshot": "- list 'Products'",
                "network_events": [],
                "previous_action": None,
            },
        )

    assert "模型调用超时" in str(error.value)
    assert "credential-value-123456789" not in str(error.value)


class _Channel:
    def __init__(self) -> None:
        self.acked = False

    def basic_ack(self, **_: object) -> None:
        self.acked = True


class _ExplorationConsumerClient:
    def __init__(self) -> None:
        self.completed: dict[str, object] | None = None

    def web_exploration_claim(self, *_: object) -> WebExplorationClaimResult:
        return WebExplorationClaimResult(
            1, "exploration-001", "message-001", "runner-001", "RUNNING", True
        )

    def get_web_exploration_control(self, *_: object) -> WebExplorationControlResult:
        return _control()

    def get_web_exploration_execution_plan(
        self, *_: object
    ) -> WebExplorationExecutionPlanResult:
        return _plan()

    def web_exploration_execution_start(self, *_: object) -> WebExplorationStartResult:
        return WebExplorationStartResult(
            1, "exploration-001", "message-001", "runner-001", "RUNNING", True
        )

    def request_web_exploration_decision(self, *_: object) -> None:
        raise ProtocolError("模型调用超时")

    def web_exploration_execution_complete(
        self, *_: object, **kwargs: object
    ) -> WebExplorationCompleteResult:
        self.completed = kwargs
        return WebExplorationCompleteResult(
            1, "exploration-001", "message-001", "runner-001", "FAILED", False
        )


def test_consumer_terminalizes_exploration_decision_protocol_failure(
    monkeypatch, tmp_path: Path
) -> None:
    del tmp_path
    channel = _Channel()
    client = _ExplorationConsumerClient()
    consumer = RunnerTaskConsumer(
        channel,
        client,  # type: ignore[arg-type]
        RunnerIdentity("runner-001", "credential-value-123456789"),
        {"WEB": 1},
    )
    monkeypatch.setattr(web_exploration, "PlaywrightMcpClient", _FakeMcp)
    envelope = parse_web_exploration_envelope(
        _envelope(), expected_runner_id="runner-001"
    )

    outcome = consumer._process_web_exploration_delivery(1, envelope)

    assert outcome == ConsumeOutcome.ACKED
    assert channel.acked
    assert client.completed is not None
    assert client.completed["outcome"] == "FAILED"
    assert client.completed["error_type"] == "WEB_EXPLORATION_PROTOCOL_ERROR"
    assert "模型调用超时" in str(client.completed["error_message"])
    assert len(client.completed["observations"]) == 1


class _FakeMcp:
    calls: list[tuple[str, dict[str, object]]] = []
    init_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        self.calls = []
        self.work_dir = Path(str(kwargs["work_dir"]))
        type(self).calls = self.calls
        type(self).init_kwargs = kwargs

    def __enter__(self) -> _FakeMcp:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def call_tool(self, name: str, arguments: dict[str, object]) -> str:
        self.calls.append((name, arguments))
        if name == "browser_snapshot":
            return "- Page URL: https://example.test/start\n- Page Title: Store\n- list 'Products'"
        if name == "browser_network_requests":
            return "GET https://example.test/api?token=plain-secret 200"
        return "ok"

    def capture_screenshot(self, artifact_name: str) -> Path:
        path = self.work_dir / f"{artifact_name}.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        return path


def test_executor_finishes_with_redacted_observation(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(web_exploration, "PlaywrightMcpClient", _FakeMcp)
    decision = WebExplorationDecisionResult(
        1,
        "exploration-001",
        "message-001",
        1,
        33,
        {
            "action": "FINISH",
            "reason": "商品列表已经可见",
            "element": None,
            "ref": None,
            "value": None,
            "url": None,
            "expected_observation": None,
        },
    )
    result = WebExplorationExecutor(decide=lambda _: decision, control=_control).execute(
        _plan(), tmp_path.resolve()
    )

    assert result.outcome == "COMPLETED"
    assert result.action_trace[0]["ai_call_id"] == 33
    assert "plain-secret" not in result.observations[0]["network_events"][0]["summary"]
    assert [call[0] for call in _FakeMcp.calls[:3]] == [
        "browser_navigate",
        "browser_snapshot",
        "browser_network_requests",
    ]
    assert _FakeMcp.init_kwargs["headless"] is True


def test_executor_forwards_visual_exploration_mode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(web_exploration, "PlaywrightMcpClient", _FakeMcp)
    decision = WebExplorationDecisionResult(
        1,
        "exploration-001",
        "message-001",
        1,
        35,
        {
            "action": "FINISH",
            "reason": "页面已验证",
            "element": None,
            "ref": None,
            "value": None,
            "url": None,
            "expected_observation": None,
        },
    )

    result = WebExplorationExecutor(decide=lambda _: decision, control=_control).execute(
        replace(_plan(), headless=False), tmp_path.resolve()
    )

    assert result.outcome == "COMPLETED"
    assert _FakeMcp.init_kwargs["headless"] is False


def test_executor_uploads_runner_controlled_final_screenshot(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(web_exploration, "PlaywrightMcpClient", _FakeMcp)
    uploaded: list[tuple[str, int, str, str]] = []
    decision = WebExplorationDecisionResult(
        1,
        "exploration-001",
        "message-001",
        1,
        36,
        {
            "action": "FINISH",
            "reason": "登录成功并进入工作台",
            "element": None,
            "ref": None,
            "value": None,
            "url": None,
            "expected_observation": None,
        },
    )

    def upload(
        evidence: ValidatedEvidenceArtifact, sequence: int, kind: str, label: str
    ) -> str:
        uploaded.append((evidence.artifact_type, sequence, kind, label))
        return "webexp-evidence-1"

    result = WebExplorationExecutor(
        decide=lambda _: decision,
        control=_control,
        upload_evidence=upload,
    ).execute(
        replace(_plan(), permissions=("PAGE_READ", "SCREENSHOT_CAPTURE")),
        tmp_path.resolve(),
    )

    assert result.outcome == "COMPLETED"
    assert uploaded == [("SCREENSHOT", 1, "FINAL", "登录成功并进入工作台")]
    assert result.action_trace[0]["evidence_id"] == "webexp-evidence-1"


def test_executor_blocks_navigation_outside_allowed_origin(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(web_exploration, "PlaywrightMcpClient", _FakeMcp)
    decision = WebExplorationDecisionResult(
        1,
        "exploration-001",
        "message-001",
        1,
        34,
        {
            "action": "NAVIGATE",
            "reason": "继续",
            "element": None,
            "ref": None,
            "value": None,
            "url": "https://outside.test/",
            "expected_observation": None,
        },
    )
    result = WebExplorationExecutor(decide=lambda _: decision, control=_control).execute(
        _plan(), tmp_path.resolve()
    )

    assert result.outcome == "FAILED"
    assert result.error_type == "MCP_EXECUTION_ERROR"
    assert not any(
        name == "browser_navigate" and arguments.get("url") == "https://outside.test/"
        for name, arguments in _FakeMcp.calls
    )


def test_executor_does_not_treat_explanatory_reason_as_destructive(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(web_exploration, "PlaywrightMcpClient", _FakeMcp)
    decisions = iter(
        [
            {
                "action": "NAVIGATE",
                "reason": "访问受保护页面，以确认会话已被彻底清除",
                "element": None,
                "ref": None,
                "value": None,
                "url": "https://example.test/resources",
                "expected_observation": "返回登录页",
            },
            {
                "action": "FINISH",
                "reason": "验证完成",
                "element": None,
                "ref": None,
                "value": None,
                "url": None,
                "expected_observation": None,
            },
        ]
    )

    def decide(observation: dict[str, object]) -> WebExplorationDecisionResult:
        sequence = int(observation["sequence"])
        return WebExplorationDecisionResult(
            1,
            "exploration-001",
            "message-001",
            sequence,
            50 + sequence,
            next(decisions),
        )

    result = WebExplorationExecutor(decide=decide, control=_control).execute(
        _plan(), tmp_path.resolve()
    )

    assert result.outcome == "COMPLETED"
    assert (
        "browser_navigate",
        {"url": "https://example.test/resources"},
    ) in _FakeMcp.calls


class _DestructiveButtonMcp(_FakeMcp):
    def call_tool(self, name: str, arguments: dict[str, object]) -> str:
        self.calls.append((name, arguments))
        if name == "browser_snapshot":
            return (
                "- Page URL: https://example.test/start\n"
                "- Page Title: Store\n"
                "- button '删除用户' [ref=e9]"
            )
        if name == "browser_network_requests":
            return "no requests"
        return "ok"


def test_executor_still_blocks_destructive_snapshot_target(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(web_exploration, "PlaywrightMcpClient", _DestructiveButtonMcp)
    decision = WebExplorationDecisionResult(
        1,
        "exploration-001",
        "message-001",
        1,
        55,
        {
            "action": "CLICK",
            "reason": "继续探索",
            "element": "button",
            "ref": "e9",
            "value": None,
            "url": None,
            "expected_observation": None,
        },
    )

    result = WebExplorationExecutor(decide=lambda _: decision, control=_control).execute(
        _plan(), tmp_path.resolve()
    )

    assert result.outcome == "FAILED"
    assert result.error_message == "Runner 拒绝执行可能产生破坏性副作用的探索动作"
    assert not any(name == "browser_click" for name, _ in _DestructiveButtonMcp.calls)


class _CredentialMcp(_FakeMcp):
    def call_tool(self, name: str, arguments: dict[str, object]) -> str:
        self.calls.append((name, arguments))
        typed = [
            str(item[1].get("text"))
            for item in self.calls
            if item[0] == "browser_type"
        ]
        if name == "browser_snapshot":
            values = " ".join(typed)
            return (
                "- Page URL: https://example.test/start\n"
                "- Page Title: Login\n"
                f"- textbox 'Username {values}' [ref=e1]\n"
                "- textbox 'Password' [ref=e2]\n"
                "- button 'Login' [ref=e3]"
            )
        if name == "browser_network_requests":
            return "no requests"
        if name == "browser_generate_locator":
            return f"getByRef({arguments.get('target')})"
        if name == "browser_type":
            return f"typed {arguments.get('text')}"
        return "ok"


def test_executor_injects_login_secrets_locally_and_redacts_all_results(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(web_exploration, "PlaywrightMcpClient", _CredentialMcp)
    secret_username = "managed-user@example.test"
    secret_password = "managed-password-value"
    plan = replace(
        _plan(),
        objective="验证登录",
        permissions=("PAGE_READ", "MANAGED_LOGIN"),
        secrets=(
            WebExecutionSecret("AI_TEST_USERNAME", secret_username),
            WebExecutionSecret("AI_TEST_PASSWORD", secret_password),
        ),
    )
    decisions = iter(
        [
            {
                "action": "TYPE", "reason": "输入测试账号", "element": "Username",
                "ref": "e1", "value": "{{secret.AI_TEST_USERNAME}}", "url": None,
                "expected_observation": "账号已输入",
            },
            {
                "action": "TYPE", "reason": "输入测试密码", "element": "Password",
                "ref": "e2", "value": "{{secret.AI_TEST_PASSWORD}}", "url": None,
                "expected_observation": "密码已输入",
            },
            {
                "action": "CLICK", "reason": "登录", "element": "Login", "ref": "e3",
                "value": None, "url": None, "expected_observation": "登录完成",
            },
        ]
    )

    def decide(observation: dict[str, object]) -> WebExplorationDecisionResult:
        try:
            decision = next(decisions)
        except StopIteration:
            decision = {
                "action": "FINISH", "reason": "登录目标已验证", "element": None,
                "ref": None, "value": None, "url": None, "expected_observation": None,
            }
        sequence = int(observation["sequence"])
        return WebExplorationDecisionResult(
            1, "exploration-001", "message-001", sequence, 40 + sequence, decision,
        )

    result = WebExplorationExecutor(decide=decide, control=_control).execute(
        plan, tmp_path.resolve()
    )

    assert result.outcome == "COMPLETED"
    typed_values = [
        arguments["text"]
        for name, arguments in _CredentialMcp.calls
        if name == "browser_type"
    ]
    assert typed_values == [secret_username, secret_password]
    serialized = json.dumps(
        {"observations": result.observations, "trace": result.action_trace},
        ensure_ascii=False,
    )
    assert secret_username not in serialized
    assert secret_password not in serialized
    assert serialized.count("[REDACTED]") >= 2


def test_executor_reports_exact_missing_permission_for_direct_api(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(web_exploration, "PlaywrightMcpClient", _FakeMcp)
    decision = WebExplorationDecisionResult(
        1,
        "exploration-001",
        "message-001",
        1,
        88,
        {
            "action": "NAVIGATE",
            "reason": "查询合成用户 buyer",
            "element": None,
            "ref": None,
            "value": None,
            "url": "https://example.test/api/users?q=buyer",
            "expected_observation": "返回匹配用户",
        },
    )

    result = WebExplorationExecutor(decide=lambda _: decision, control=_control).execute(
        _plan(), tmp_path.resolve()
    )

    assert result.outcome == "FAILED"
    assert result.error_type == "MCP_PERMISSION_DENIED"
    assert result.result_summary is not None
    assert result.result_summary["required_capability"] == "DIRECT_API_NAVIGATION"
    assert "buyer" in result.error_message


def test_executor_allows_buyer_api_when_explicitly_authorized(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(web_exploration, "PlaywrightMcpClient", _FakeMcp)
    decisions = iter(
        [
            {
                "action": "NAVIGATE",
                "reason": "诊断查询 buyer",
                "element": None,
                "ref": None,
                "value": None,
                "url": "https://example.test/api/users?q=buyer",
                "expected_observation": "响应可见",
            },
            {
                "action": "FINISH", "reason": "诊断完成", "element": None,
                "ref": None, "value": None, "url": None, "expected_observation": None,
            },
        ]
    )

    def decide(observation: dict[str, object]) -> WebExplorationDecisionResult:
        sequence = int(observation["sequence"])
        return WebExplorationDecisionResult(
            1, "exploration-001", "message-001", sequence, 90 + sequence, next(decisions)
        )

    result = WebExplorationExecutor(decide=decide, control=_control).execute(
        replace(_plan(), permissions=("PAGE_READ", "DIRECT_API_NAVIGATION")),
        tmp_path.resolve(),
    )

    assert result.outcome == "COMPLETED"
