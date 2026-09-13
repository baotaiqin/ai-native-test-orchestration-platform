from app.modules.performance.models import PerformanceRun
from app.modules.performance.schemas import (
    PerformanceAnalysisResult,
    PerformanceCompleteRequest,
    PerformanceProfileCreate,
    PerformanceSample,
    PerformanceSla,
)
from app.modules.performance.service import (
    _analysis_source_snapshot,
    _evaluate_sla,
    _passes_performance_run,
    _performance_analysis_errors,
    _summarize,
    _validate_analysis_prompt,
    compare_runs,
)
from app.modules.prompt_center.models import OutputSchema, PromptDefinition, PromptVersion


def test_profile_requires_iterations_not_less_than_concurrency() -> None:
    try:
        PerformanceProfileCreate(
            project_id=1,
            name="Smoke performance",
            case_id=1,
            concurrency=10,
            iterations=5,
        )
    except ValueError as exc:
        assert "iterations" in str(exc)
    else:
        raise AssertionError("invalid performance profile was accepted")


def test_fixed_rps_profile_derives_bounded_iterations() -> None:
    profile = PerformanceProfileCreate(
        project_id=1,
        name="Fixed RPS",
        case_id=1,
        concurrency=5,
        load_mode="FIXED_RPS",
        target_rps=12.5,
        duration_seconds=4,
    )

    assert profile.iterations == 50

    try:
        PerformanceProfileCreate(
            project_id=1,
            name="Too many requests",
            case_id=1,
            concurrency=5,
            load_mode="FIXED_RPS",
            target_rps=10_000,
            duration_seconds=2,
        )
    except ValueError as exc:
        assert "10000" in str(exc)
    else:
        raise AssertionError("unbounded FIXED_RPS profile was accepted")


def test_metrics_and_sla_are_deterministic() -> None:
    payload = PerformanceCompleteRequest(
        message_id="msg_1",
        case_run_id=1,
        wall_duration_ms=1_000,
        samples=[
            PerformanceSample(duration_ms=10, status_code=200, bytes_received=100),
            PerformanceSample(duration_ms=20, status_code=201, bytes_received=120),
            PerformanceSample(duration_ms=30, status_code=500, bytes_received=80),
            PerformanceSample(duration_ms=40, error_type="TARGET_TIMEOUT"),
        ],
    )

    metrics, errors = _summarize(payload)
    results = _evaluate_sla(
        metrics,
        PerformanceSla(max_error_rate=0.25, max_p95_ms=35, min_rps=3).model_dump(exclude_none=True),
    )

    assert metrics.request_count == 4
    assert metrics.success_count == 2
    assert metrics.error_rate == 0.5
    assert metrics.rps == 4
    assert metrics.p50_ms == 20
    assert metrics.p95_ms == 40
    assert metrics.received_bytes == 300
    assert errors == {"HTTP_500": 1, "TARGET_TIMEOUT": 1}
    assert [item.passed for item in results] == [False, False, True]


def test_configured_error_budget_controls_final_outcome() -> None:
    payload = PerformanceCompleteRequest(
        message_id="msg_budget",
        case_run_id=1,
        wall_duration_ms=1_000,
        samples=[
            *[PerformanceSample(duration_ms=10, status_code=200) for _ in range(99)],
            PerformanceSample(duration_ms=10, status_code=500),
        ],
    )
    metrics, _ = _summarize(payload)
    budget = {"max_error_rate": 0.01}
    budget_results = _evaluate_sla(metrics, budget)

    assert _passes_performance_run(metrics, budget_results, budget) is True
    assert _passes_performance_run(metrics, [], {}) is False


def test_step_load_derives_peak_concurrency_and_validates_total_duration() -> None:
    profile = PerformanceProfileCreate(
        project_id=1,
        name="Step load",
        case_id=1,
        load_mode="STEP_LOAD",
        iterations=200,
        step_stages=[
            {"concurrency": 2, "duration_seconds": 10},
            {"concurrency": 8, "duration_seconds": 20},
        ],
    )

    assert profile.concurrency == 8
    assert profile.step_stages is not None
    assert sum(item.duration_seconds for item in profile.step_stages) == 30


def test_profile_target_and_stream_engine_are_strict() -> None:
    scenario = PerformanceProfileCreate(
        project_id=1,
        name="Scenario load",
        target_type="SCENARIO",
        scenario_id=3,
        concurrency=2,
        iterations=4,
    )
    assert scenario.case_id is None
    assert scenario.scenario_id == 3

    try:
        PerformanceProfileCreate(
            project_id=1,
            name="Invalid stream scenario",
            target_type="SCENARIO",
            scenario_id=3,
            engine="PYTHON_STREAM",
            concurrency=2,
            iterations=4,
        )
    except ValueError as exc:
        assert "PYTHON_STREAM" in str(exc)
    else:
        raise AssertionError("streaming Scenario profile was accepted")


def test_stream_metrics_and_ttft_sla_are_deterministic() -> None:
    payload = PerformanceCompleteRequest(
        message_id="msg_stream",
        case_run_id=1,
        wall_duration_ms=2_000,
        samples=[
            PerformanceSample(
                duration_ms=300,
                status_code=200,
                bytes_received=120,
                bytes_sent=40,
                started_offset_ms=0,
                ttft_ms=50,
                stream_duration_ms=250,
                input_tokens=10,
                output_tokens=20,
                chunk_count=4,
                max_chunk_gap_ms=80,
                stream_completed=True,
            ),
            PerformanceSample(
                duration_ms=500,
                error_type="STREAM_INTERRUPTED",
                bytes_received=60,
                bytes_sent=40,
                started_offset_ms=100,
                ttft_ms=100,
                stream_duration_ms=400,
                chunk_count=2,
                max_chunk_gap_ms=150,
                stream_completed=False,
            ),
        ],
    )

    metrics, errors = _summarize(payload)
    results = _evaluate_sla(metrics, {"max_ttft_p95_ms": 90})

    assert metrics.ttft_p95_ms == 100
    assert metrics.output_tokens == 20
    assert metrics.chunk_count == 6
    assert metrics.interrupted_streams == 1
    assert metrics.sent_bytes == 80
    assert metrics.trends[0]["requests"] == 2
    assert errors == {"STREAM_INTERRUPTED": 1}
    assert results[0].passed is False


def test_run_comparison_preserves_baseline_order_and_computes_deltas(monkeypatch) -> None:
    baseline_metrics, _ = _summarize(
        PerformanceCompleteRequest(
            message_id="msg_base",
            case_run_id=1,
            wall_duration_ms=1_000,
            samples=[PerformanceSample(duration_ms=100, status_code=200)],
        )
    )
    candidate_metrics, _ = _summarize(
        PerformanceCompleteRequest(
            message_id="msg_candidate",
            case_run_id=2,
            wall_duration_ms=1_000,
            samples=[
                PerformanceSample(duration_ms=80, status_code=200),
                PerformanceSample(duration_ms=120, status_code=200),
            ],
        )
    )

    class Row:
        def __init__(self, run_id: str, run_code: str, metrics: dict) -> None:
            self.performance_run = type(
                "PerformanceRunRow", (), {"run_id": run_id, "metrics": metrics}
            )()
            self.run = type("RunRow", (), {"run_code": run_code, "status": "SUCCESS"})()

    rows = [
        Row("run-candidate", "PERF-002", candidate_metrics.model_dump(mode="json")),
        Row("run-base", "PERF-001", baseline_metrics.model_dump(mode="json")),
    ]

    class Result:
        def all(self):
            return [(row.performance_run, row.run) for row in rows]

    class Session:
        def execute(self, _statement):
            return Result()

    monkeypatch.setattr("app.modules.performance.service.get_project", lambda *_args: object())
    response = compare_runs(
        Session(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        1,
        ["run-base", "run-candidate"],
    )

    assert response.baseline_run_id == "run-base"
    assert [item.run_id for item in response.items] == ["run-base", "run-candidate"]
    assert response.items[0].delta_from_baseline["rps"] == 0
    assert response.items[1].delta_from_baseline["rps"] == 1
    assert response.items[1].delta_from_baseline["average_ms"] == 0


def _analysis_run() -> PerformanceRun:
    metrics, errors = _summarize(
        PerformanceCompleteRequest(
            message_id="msg_analysis",
            case_run_id=1,
            wall_duration_ms=1_000,
            samples=[
                PerformanceSample(duration_ms=100, status_code=200),
                PerformanceSample(duration_ms=300, status_code=500),
            ],
        )
    )
    return PerformanceRun(
        run_id="run-analysis",
        profile_id=1,
        project_id=1,
        config_snapshot={
            "target_type": "API_CASE",
            "concurrency": 2,
            "iterations": 2,
            "load_mode": "FIXED_ITERATIONS",
            "engine": "PYTHON_HTTP",
            "unexpected": "must-not-leak",
        },
        status="FAILED",
        metrics=metrics.model_dump(mode="json"),
        sla_results=[
            {
                "metric": "error_rate",
                "operator": "LTE",
                "threshold": 0.1,
                "actual": 0.5,
                "passed": False,
            }
        ],
        error_distribution={**errors, "unsafe error text": 2},
    )


def test_analysis_snapshot_is_bounded_grounded_and_sanitized() -> None:
    snapshot, digest, size, metric_values, verdict = _analysis_source_snapshot(_analysis_run())

    assert verdict == "MISSES_SLA"
    assert snapshot["sla_verdict"] == "MISSES_SLA"
    assert snapshot["config"].get("unexpected") is None
    assert "trends" not in snapshot["metrics"]
    assert snapshot["error_distribution"] == {"HTTP_500": 1, "UNCLASSIFIED": 2}
    assert metric_values["p95_ms"] == 300
    assert metric_values["error.UNCLASSIFIED"] == 2
    assert len(digest) == 64
    assert 0 < size <= 65_536


def test_analysis_validator_cannot_override_sla_or_invent_metrics() -> None:
    _snapshot, _digest, _size, metric_values, verdict = _analysis_source_snapshot(_analysis_run())
    result = {
        "verdict": "MEETS_SLA",
        "summary": "平台指标显示错误率超过阈值。",
        "findings": [
            {
                "category": "ERRORS",
                "severity": "CRITICAL",
                "metric": "invented_metric",
                "observed": 0.01,
                "observation": "该值来自模型猜测。",
                "recommendation": "先复核服务端错误分布。",
            }
        ],
        "bottleneck_hypotheses": [],
        "recommendations": ["检查失败请求并完成复测。"],
        "confidence": 0.8,
        "needs_human_review": True,
    }

    errors = _performance_analysis_errors(
        result, metric_values=metric_values, expected_verdict=verdict
    )

    assert any("verdict" in item for item in errors)
    assert any("只能引用" in item for item in errors)


def test_analysis_text_rejects_urls_and_credentials() -> None:
    payload = {
        "verdict": "NO_SLA",
        "summary": "请访问 https://internal.example 排查。",
        "findings": [
            {
                "category": "LATENCY",
                "severity": "INFO",
                "metric": "p95_ms",
                "observed": 100,
                "observation": "P95 延迟稳定。",
                "recommendation": "继续观察。",
            }
        ],
        "recommendations": ["继续观察。"],
        "confidence": 0.5,
        "needs_human_review": True,
    }

    try:
        PerformanceAnalysisResult.model_validate(payload)
    except ValueError as exc:
        assert "敏感凭据" in str(exc)
    else:
        raise AssertionError("unsafe analysis text was accepted")


def test_analysis_uses_project_effective_prompt_for_audit(monkeypatch) -> None:
    base = PromptDefinition(
        id=1,
        name="System performance",
        code="SYSTEM_PERFORMANCE_TEST",
        task_type="PERFORMANCE_ANALYSIS",
        enabled=True,
        current_version_id=11,
        created_by="dev-admin",
    )
    override = PromptDefinition(
        id=2,
        name="Project performance",
        code="PROJECT_PERFORMANCE_TEST",
        task_type="PERFORMANCE_ANALYSIS",
        scope="PROJECT",
        project_id=7,
        base_prompt_id=1,
        enabled=True,
        current_version_id=22,
        created_by="dev-admin",
    )
    version = PromptVersion(
        id=22,
        prompt_id=2,
        version_no=1,
        system_prompt="Analyze safely",
        user_template="{{ source_snapshot }}",
        output_schema_id=33,
        created_by="dev-admin",
    )
    schema = OutputSchema(
        id=33,
        name="PerformanceAnalysisTest",
        version_no=1,
        schema_json={"type": "object"},
        enabled=True,
        created_by="dev-admin",
    )

    class Session:
        def get(self, model, object_id):
            return {
                (PromptDefinition, 1): base,
                (PromptVersion, 22): version,
                (OutputSchema, 33): schema,
            }.get((model, object_id))

    monkeypatch.setattr(
        "app.modules.performance.service.resolve_effective_prompt",
        lambda _session, project_id, prompt: (
            override if project_id == 7 and prompt is base else prompt
        ),
    )

    assert _validate_analysis_prompt(Session(), 1, 7) == (22, 33)  # type: ignore[arg-type]
