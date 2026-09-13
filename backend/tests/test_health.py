from fastapi.testclient import TestClient

from app.modules.system.schemas import DatabaseHealth


def test_root_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["X-Request-ID"]


def test_versioned_health(client: TestClient) -> None:
    response = client.get("/api/v1/system/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "backend",
        "version": "0.1.0",
        "environment": "development",
    }


def test_readiness_reports_database_connection(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.modules.system.router.get_database_health",
        lambda: DatabaseHealth(status="ok", database="ai_test_platform"),
    )
    response = client.get("/api/v1/system/readiness")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": {
            "status": "ok",
            "database": "ai_test_platform",
            "dialect": "mysql",
        },
    }


def test_readiness_returns_503_when_database_is_unavailable(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.modules.system.router.get_database_health",
        lambda: DatabaseHealth(status="unavailable"),
    )
    response = client.get("/api/v1/system/readiness")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
