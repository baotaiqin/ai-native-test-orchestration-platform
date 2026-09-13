"""在真实 MySQL 验证 Schema、单次 Repair 与 AI 调用审计并清理数据。"""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import get_settings
from app.infrastructure.db.session import engine
from app.main import app


def main() -> None:
    settings = get_settings()
    marker = uuid4().hex[:8].upper()
    ids: dict[str, int] = {}
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
                json={"name": "Phase 2 Structured 验证", "code": f"STRUCT_{marker}"},
            )
            project.raise_for_status()
            ids["project"] = project.json()["id"]
            model = client.post(
                "/api/v1/model-center", headers=headers,
                json={
                    "name": f"STRUCT_MODEL_{marker}",
                    "provider": "OPENAI_COMPATIBLE",
                    "base_url": "http://127.0.0.1:11434/v1",
                    "model_name": "verify-json",
                    "model_type": "TEXT",
                    "input_price": "2",
                    "output_price": "8",
                },
            )
            model.raise_for_status()
            ids["model"] = model.json()["id"]
            schema = client.post(
                "/api/v1/ai/output-schemas", headers=headers,
                json={
                    "name": f"STRUCT_SCHEMA_{marker}",
                    "schema_json": {
                        "type": "object",
                        "required": ["summary", "risks"],
                        "additionalProperties": False,
                        "properties": {
                            "summary": {"type": "string"},
                            "risks": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                },
            )
            schema.raise_for_status()
            ids["schema"] = schema.json()["id"]
            prompt = client.post(
                "/api/v1/prompt-center", headers=headers,
                json={
                    "name": f"Structured Prompt {marker}",
                    "code": f"STRUCT_PROMPT_{marker}",
                    "task_type": "REQUIREMENT_REVIEW",
                    "system_prompt": "输出 JSON",
                    "user_template": "评审 {{ requirement }}",
                    "output_schema_id": ids["schema"],
                },
            )
            prompt.raise_for_status()
            ids["prompt"] = prompt.json()["id"]
            ids["prompt_version"] = prompt.json()["current_version_id"]
            payload = {
                "project_id": ids["project"],
                "task_type": "REQUIREMENT_REVIEW",
                "model_config_id": ids["model"],
                "prompt_version_id": ids["prompt_version"],
                "output_schema_id": ids["schema"],
                "raw_output": (
                    "```json\n"
                    '{"summary":"已修复","risks":["边界"],}\n'
                    "```"
                ),
                "input_token": 1000,
                "output_token": 500,
                "latency_ms": 1200,
            }
            validated = client.post(
                "/api/v1/ai/structured-output/validate", headers=headers, json=payload
            )
            validated.raise_for_status()
            if not validated.json()["success"] or not validated.json()["repair_used"]:
                raise RuntimeError("结构化输出单次 Repair 未成功")
            logs = client.get(
                f"/api/v1/ai/calls?project_id={ids['project']}", headers=headers
            )
            logs.raise_for_status()
            item = logs.json()["items"][0]
            if item["total_token"] != 1500 or float(item["estimated_cost"]) != 0.006:
                raise RuntimeError("Token 或费用审计不正确")
            print("output_schema=ok")
            print("structured_parse_and_validate=ok")
            print("single_repair=ok")
            print("ai_call_log_token_cost_latency=ok")
        finally:
            with engine.begin() as connection:
                if "project" in ids:
                    connection.execute(
                        text("DELETE FROM ai_call_logs WHERE project_id=:project_id"),
                        {"project_id": ids["project"]},
                    )
                if "prompt" in ids:
                    connection.execute(
                        text(
                            "UPDATE prompt_definitions SET current_version_id=NULL "
                            "WHERE id=:prompt_id"
                        ), {"prompt_id": ids["prompt"]},
                    )
                    connection.execute(
                        text("DELETE FROM prompt_versions WHERE prompt_id=:prompt_id"),
                        {"prompt_id": ids["prompt"]},
                    )
                    connection.execute(
                        text("DELETE FROM prompt_definitions WHERE id=:prompt_id"),
                        {"prompt_id": ids["prompt"]},
                    )
                if "schema" in ids:
                    connection.execute(
                        text("DELETE FROM output_schemas WHERE id=:schema_id"),
                        {"schema_id": ids["schema"]},
                    )
                if "model" in ids:
                    connection.execute(
                        text("DELETE FROM model_configurations WHERE id=:model_id"),
                        {"model_id": ids["model"]},
                    )
                if "project" in ids:
                    parameters = {"project_id": ids["project"]}
                    connection.execute(
                        text("DELETE FROM project_members WHERE project_id=:project_id"),
                        parameters,
                    )
                    connection.execute(
                        text("DELETE FROM projects WHERE id=:project_id"), parameters,
                    )
            print("temporary_data=removed")


if __name__ == "__main__":
    main()
