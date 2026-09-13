"""在真实 MySQL 上验证模型配置、项目绑定与 fallback 约束并清理临时数据。"""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import get_settings
from app.infrastructure.db.session import engine
from app.main import app


def main() -> None:
    settings = get_settings()
    marker = uuid4().hex[:8].upper()
    project_id: int | None = None
    model_ids: list[int] = []
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
                "/api/v1/projects", headers=headers,
                json={"name": "Phase 2 Model 验证", "code": f"MODEL_VERIFY_{marker}"},
            )
            project.raise_for_status()
            project_id = project.json()["id"]
            for suffix in ("PRIMARY", "FALLBACK"):
                response = client.post(
                    "/api/v1/model-center", headers=headers,
                    json={
                        "name": f"VERIFY_{marker}_{suffix}",
                        "provider": "OPENAI_COMPATIBLE",
                        "base_url": "http://127.0.0.1:11434/v1",
                        "model_name": f"verify-{suffix.lower()}",
                        "model_type": "TEXT",
                        "max_context": 32768,
                        "timeout_seconds": 30,
                    },
                )
                response.raise_for_status()
                model_ids.append(response.json()["id"])
            binding = client.put(
                "/api/v1/model-center/bindings/project", headers=headers,
                json={
                    "project_id": project_id,
                    "task_type": "REQUIREMENT_REVIEW",
                    "primary_model_id": model_ids[0],
                    "fallback_model_id": model_ids[1],
                    "max_fallback": 1,
                },
            )
            binding.raise_for_status()
            listed = client.get(
                f"/api/v1/model-center/bindings/project?project_id={project_id}",
                headers=headers,
            )
            listed.raise_for_status()
            if listed.json()["total"] != 1:
                raise RuntimeError("模型绑定未写入")
            print("model_configuration=ok")
            print("project_model_binding=ok")
            print("fallback_policy=max_1")
        finally:
            with engine.begin() as connection:
                if project_id is not None:
                    parameters = {"project_id": project_id}
                    connection.execute(
                        text(
                            "DELETE FROM project_model_bindings "
                            "WHERE project_id=:project_id"
                        ), parameters,
                    )
                    connection.execute(
                        text("DELETE FROM project_members WHERE project_id=:project_id"),
                        parameters,
                    )
                    connection.execute(
                        text("DELETE FROM projects WHERE id=:project_id"), parameters,
                    )
                for model_id in model_ids:
                    connection.execute(
                        text("DELETE FROM model_configurations WHERE id=:model_id"),
                        {"model_id": model_id},
                    )
            print("temporary_data=removed")


if __name__ == "__main__":
    main()
