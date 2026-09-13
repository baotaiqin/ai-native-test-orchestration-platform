from fastapi.testclient import TestClient

from app.core.metrics import RUNS_CREATED


def test_metrics_exports_process_http_and_business_metrics(client: TestClient) -> None:
    RUNS_CREATED.labels(run_type="API_CASE", trigger_type="MANUAL").inc()
    assert client.get("/api/v1/system/health").status_code == 200

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "python_info" in response.text
    assert "ai_test_process_cpu_seconds" in response.text
    assert "ai_test_process_resident_memory_bytes" in response.text
    assert "ai_test_system_cpu_usage_ratio" in response.text
    assert "ai_test_system_memory_usage_ratio" in response.text
    assert 'ai_test_system_load{window="1m"}' in response.text
    assert "ai_test_http_requests_total" in response.text
    assert 'route="/api/v1/system/health"' in response.text
    assert "ai_test_http_request_duration_seconds" in response.text
    assert "ai_test_http_requests_in_progress" in response.text
    assert "ai_test_runs_created_total" in response.text


def test_metrics_uses_route_templates_and_never_raw_unmatched_paths(client: TestClient) -> None:
    sensitive_path = "/not-a-route/do-not-export-this-value"
    assert client.get(sensitive_path).status_code == 404

    body = client.get("/metrics").text

    assert sensitive_path not in body
    assert 'route="unmatched"' in body
