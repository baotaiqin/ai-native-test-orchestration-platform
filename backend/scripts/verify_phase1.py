"""在真实本机 MySQL 上验证 Phase 1 主链，并清理本次创建的数据。"""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.infrastructure.db.session import engine
from app.main import app


def main() -> None:
    settings = get_settings()
    database_url = make_url(settings.database_url)
    if not database_url.password:
        raise RuntimeError("应用数据库 URL 未配置密码，无法执行数据库连接验证")

    project_id: int | None = None
    marker = uuid4().hex[:8].upper()
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={
                "username": settings.dev_admin_username,
                "password": settings.dev_admin_password,
            },
        )
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        try:
            project = client.post(
                "/api/v1/projects",
                headers=headers,
                json={"name": "Phase 1 自动验证", "code": f"VERIFY_{marker}"},
            )
            project.raise_for_status()
            project_id = project.json()["id"]

            environment = client.post(
                "/api/v1/environments",
                headers=headers,
                json={
                    "project_id": project_id,
                    "name": "本机开发环境",
                    "code": "LOCAL",
                    "base_url": "http://127.0.0.1:8000",
                },
            )
            environment.raise_for_status()
            environment_id = environment.json()["id"]

            variable = client.put(
                f"/api/v1/environments/{environment_id}/variables/retry_count",
                headers=headers,
                json={"value": "2", "value_type": "NUMBER", "enabled": True},
            )
            variable.raise_for_status()

            secret = client.post(
                "/api/v1/secrets",
                headers=headers,
                json={
                    "project_id": project_id,
                    "environment_id": environment_id,
                    "name": "MYSQL_PASSWORD",
                    "secret_type": "DB_PASSWORD",
                    "value": database_url.password,
                },
            )
            secret.raise_for_status()
            if database_url.password in secret.text or "encrypted_value" in secret.text:
                raise RuntimeError("Secret API 响应泄露敏感值")

            connection = client.post(
                "/api/v1/database-connections",
                headers=headers,
                json={
                    "project_id": project_id,
                    "environment_id": environment_id,
                    "name": "平台本机 MySQL",
                    "host": database_url.host or "127.0.0.1",
                    "port": database_url.port or 3306,
                    "database_name": database_url.database,
                    "username": database_url.username,
                    "password_secret_id": secret.json()["id"],
                    "ssl_enabled": False,
                },
            )
            connection.raise_for_status()
            connection_test = client.post(
                f"/api/v1/database-connections/{connection.json()['id']}/test",
                headers=headers,
            )
            connection_test.raise_for_status()
            result = connection_test.json()
            if result["status"] != "ok":
                raise RuntimeError(result["message"])

            print("phase1_api=ok")
            print(f"database={result['database']}")
            print(f"latency_ms={result['latency_ms']}")
            print("secret_response=masked")
        finally:
            if project_id is not None:
                with engine.begin() as connection:
                    parameters = {"project_id": project_id}
                    connection.execute(
                        text("DELETE FROM database_connections WHERE project_id=:project_id"),
                        parameters,
                    )
                    connection.execute(
                        text("DELETE FROM secrets WHERE project_id=:project_id"), parameters
                    )
                    connection.execute(
                        text(
                            "DELETE FROM environment_variables WHERE environment_id IN "
                            "(SELECT id FROM environments WHERE project_id=:project_id)"
                        ),
                        parameters,
                    )
                    connection.execute(
                        text("DELETE FROM environments WHERE project_id=:project_id"), parameters
                    )
                    connection.execute(
                        text("DELETE FROM project_members WHERE project_id=:project_id"), parameters
                    )
                    connection.execute(
                        text("DELETE FROM projects WHERE id=:project_id"), parameters
                    )
                print("temporary_data=removed")


if __name__ == "__main__":
    main()
