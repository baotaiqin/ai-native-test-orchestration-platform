from decimal import Decimal

import psutil
from prometheus_client import Counter, Gauge, Histogram

_PROCESS = psutil.Process()

PROCESS_CPU_SECONDS = Gauge(
    "ai_test_process_cpu_seconds",
    "Total user and system CPU time consumed by the backend process.",
)
PROCESS_RESIDENT_MEMORY = Gauge(
    "ai_test_process_resident_memory_bytes",
    "Resident memory used by the backend process.",
)
SYSTEM_CPU_USAGE = Gauge(
    "ai_test_system_cpu_usage_ratio",
    "System CPU utilization ratio sampled at scrape time.",
)
SYSTEM_MEMORY_USAGE = Gauge(
    "ai_test_system_memory_usage_ratio",
    "System memory utilization ratio sampled at scrape time.",
)
SYSTEM_LOAD = Gauge(
    "ai_test_system_load",
    "System load average by time window.",
    ("window",),
)

PROCESS_CPU_SECONDS.set_function(lambda: sum(_PROCESS.cpu_times()[:2]))
PROCESS_RESIDENT_MEMORY.set_function(lambda: _PROCESS.memory_info().rss)
SYSTEM_CPU_USAGE.set_function(lambda: psutil.cpu_percent(interval=None) / 100)
SYSTEM_MEMORY_USAGE.set_function(lambda: psutil.virtual_memory().percent / 100)
for _window, _index in (("1m", 0), ("5m", 1), ("15m", 2)):
    SYSTEM_LOAD.labels(window=_window).set_function(lambda index=_index: psutil.getloadavg()[index])

HTTP_REQUESTS = Counter(
    "ai_test_http_requests_total",
    "Completed backend HTTP requests.",
    ("method", "route", "status_code"),
)
HTTP_REQUEST_DURATION = Histogram(
    "ai_test_http_request_duration_seconds",
    "Backend HTTP request latency in seconds.",
    ("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 180),
)
HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "ai_test_http_requests_in_progress",
    "Backend HTTP requests currently being processed.",
    ("method",),
)
RUNS_CREATED = Counter(
    "ai_test_runs_created_total",
    "Runs committed by the platform.",
    ("run_type", "trigger_type"),
)
PERFORMANCE_RUNS_COMPLETED = Counter(
    "ai_test_performance_runs_completed_total",
    "Performance runs committed with deterministic metrics and SLA verdict.",
    ("engine", "status", "sla_verdict"),
)
AI_CALLS = Counter(
    "ai_test_ai_calls_total",
    "AI provider calls recorded by task type and outcome.",
    ("task_type", "status", "fallback_used", "repair_used"),
)
AI_TOKENS = Counter(
    "ai_test_ai_tokens_total",
    "AI tokens recorded by task type and direction.",
    ("task_type", "direction"),
)
AI_ESTIMATED_COST = Counter(
    "ai_test_ai_estimated_cost_total",
    "Estimated AI cost recorded in the model configuration currency unit.",
    ("task_type",),
)
WEBHOOK_DELIVERIES = Counter(
    "ai_test_webhook_deliveries_total",
    "Webhook delivery attempts by safe terminal attempt outcome.",
    ("status",),
)


def observe_ai_call(
    *,
    task_type: str,
    success: bool,
    fallback_used: bool,
    repair_used: bool,
    input_token: int,
    output_token: int,
    estimated_cost: Decimal,
) -> None:
    AI_CALLS.labels(
        task_type=task_type,
        status="success" if success else "failure",
        fallback_used=str(fallback_used).lower(),
        repair_used=str(repair_used).lower(),
    ).inc()
    AI_TOKENS.labels(task_type=task_type, direction="input").inc(max(0, input_token))
    AI_TOKENS.labels(task_type=task_type, direction="output").inc(max(0, output_token))
    AI_ESTIMATED_COST.labels(task_type=task_type).inc(float(max(Decimal(0), estimated_cost)))
