"""用本机 Mock Provider 与真实 MySQL 验证 AI Case 人工审核并清理数据。"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import get_settings
from app.infrastructure.db.session import engine
from app.main import app

CASES = {
    "cases": [
        {
            "title": "登录成功", "case_type": "API", "priority": "P0",
            "preconditions": ["账号有效"],
            "steps": [
                {"order": 1, "action": "提交正确凭据", "expected": "返回令牌"}
            ],
            "test_data": {"username": "demo"}, "expected_result": "登录成功",
            "tags": ["smoke"], "confidence": 0.95,
        },
        {
            "title": "密码错误", "case_type": "API", "priority": "P1",
            "preconditions": ["账号有效"],
            "steps": [
                {"order": 1, "action": "提交错误密码", "expected": "认证失败"}
            ],
            "test_data": {"password": "wrong"}, "expected_result": "拒绝登录",
            "tags": ["negative"], "confidence": 0.9,
        },
    ]
}


class CaseProvider(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        payload = {
            "id": "local-ai-cases",
            "choices": [{"message": {"content": json.dumps(CASES, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 800, "completion_tokens": 600},
        }
        content = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args) -> None:
        return


def case_schema() -> dict:
    properties = {
        "title": {"type": "string"}, "case_type": {"type": "string"},
        "priority": {"type": "string"},
        "preconditions": {"type": "array", "items": {"type": "string"}},
        "steps": {
            "type": "array",
            "items": {
                "type": "object", "required": ["order", "action", "expected"],
                "properties": {
                    "order": {"type": "integer"}, "action": {"type": "string"},
                    "expected": {"type": "string"},
                },
            },
        },
        "test_data": {"type": "object"}, "expected_result": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    }
    return {
        "type": "object", "required": ["cases"],
        "properties": {
            "cases": {
                "type": "array",
                "items": {
                    "type": "object", "required": list(properties),
                    "properties": properties,
                },
            }
        },
    }


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), CaseProvider)
    Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    marker = uuid4().hex[:8].upper()
    settings = get_settings()
    ids: dict[str, int] = {}
    try:
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
            project = client.post(
                "/api/v1/projects", headers=headers,
                json={"name": "Phase 2 AI Case 验证", "code": f"AICASE_{marker}"},
            )
            project.raise_for_status()
            ids["project"] = project.json()["id"]
            model = client.post(
                "/api/v1/model-center", headers=headers,
                json={
                    "name": f"AICASE_MODEL_{marker}", "provider": "OPENAI_COMPATIBLE",
                    "base_url": base_url, "model_name": "case-model",
                    "model_type": "TEXT", "supports_structured_output": True,
                },
            )
            model.raise_for_status()
            ids["model"] = model.json()["id"]
            client.put(
                "/api/v1/model-center/bindings/project", headers=headers,
                json={
                    "project_id": ids["project"], "task_type": "API_CASE_GENERATE",
                    "primary_model_id": ids["model"], "max_fallback": 0,
                },
            ).raise_for_status()
            schema = client.post(
                "/api/v1/ai/output-schemas", headers=headers,
                json={"name": f"AICASE_SCHEMA_{marker}", "schema_json": case_schema()},
            )
            schema.raise_for_status()
            ids["schema"] = schema.json()["id"]
            prompt = client.post(
                "/api/v1/prompt-center", headers=headers,
                json={
                    "name": f"AI Case Prompt {marker}",
                    "code": f"AICASE_PROMPT_{marker}",
                    "task_type": "API_CASE_GENERATE", "system_prompt": "输出用例 JSON",
                    "user_template": "根据需求生成用例：{{ requirement }}",
                    "output_schema_id": ids["schema"],
                },
            )
            prompt.raise_for_status()
            ids["prompt"] = prompt.json()["id"]
            requirement = client.post(
                "/api/v1/requirements", headers=headers,
                json={
                    "project_id": ids["project"], "title": "用户登录",
                    "markdown_content": "# 用户登录\n支持账号密码登录。",
                },
            )
            requirement.raise_for_status()
            ids["requirement"] = requirement.json()["id"]
            generation = client.post(
                f"/api/v1/test-cases/requirements/{ids['requirement']}/generations",
                headers=headers, json={"prompt_id": ids["prompt"]},
            )
            generation.raise_for_status()
            ids["generation"] = generation.json()["id"]
            suggestions = generation.json()["suggestions"]
            edited = {**CASES["cases"][0], "title": "登录成功（人工确认）"}
            client.patch(
                f"/api/v1/test-cases/suggestions/{suggestions[0]['id']}",
                headers=headers, json={"human_result": edited},
            ).raise_for_status()
            accepted = client.post(
                f"/api/v1/test-cases/suggestions/{suggestions[0]['id']}/decision",
                headers=headers, json={"action": "ACCEPT"},
            )
            accepted.raise_for_status()
            ids["case"] = accepted.json()["test_case_id"]
            rejected = client.post(
                f"/api/v1/test-cases/suggestions/{suggestions[1]['id']}/decision",
                headers=headers, json={"action": "REJECT"},
            )
            rejected.raise_for_status()
            formal = client.get(f"/api/v1/test-cases/{ids['case']}", headers=headers)
            links = client.get(
                f"/api/v1/test-cases/requirements/{ids['requirement']}/links",
                headers=headers,
            )
            formal.raise_for_status()
            links.raise_for_status()
            if formal.json()["current_version"]["version_no"] != 1:
                raise RuntimeError("正式 Test Case Version 未生成")
            if len(links.json()) != 1 or rejected.json()["test_case_id"] is not None:
                raise RuntimeError("Accept/Reject 资产固化规则不正确")
            print("ai_case_suggestions=2")
            print("human_single_edit=ok")
            print("accept_to_test_case_v1=ok")
            print("reject_without_asset=ok")
            print("requirement_case_link=ok")
    finally:
        server.shutdown()
        server.server_close()
        with engine.begin() as connection:
            if "project" in ids:
                params = {"project_id": ids["project"]}
                project_cleanup_statements = [
                    """DELETE FROM requirement_case_links
                    WHERE requirement_id IN (
                        SELECT id FROM requirements WHERE project_id=:project_id
                    )""",
                    """UPDATE test_cases SET current_version_id=NULL
                    WHERE project_id=:project_id""",
                    """DELETE FROM test_case_versions
                    WHERE case_id IN (
                        SELECT id FROM test_cases WHERE project_id=:project_id
                    )""",
                    """UPDATE ai_case_suggestions SET test_case_id=NULL
                    WHERE generation_id IN (
                        SELECT id FROM ai_case_generations WHERE project_id=:project_id
                    )""",
                    "DELETE FROM test_cases WHERE project_id=:project_id",
                    """DELETE FROM ai_case_suggestions
                    WHERE generation_id IN (
                        SELECT id FROM ai_case_generations WHERE project_id=:project_id
                    )""",
                    "DELETE FROM ai_case_generations WHERE project_id=:project_id",
                    "DELETE FROM ai_call_logs WHERE project_id=:project_id",
                    "DELETE FROM project_model_bindings WHERE project_id=:project_id",
                ]
                for statement in project_cleanup_statements:
                    connection.execute(text(statement), params)
            if "requirement" in ids:
                params = {"id": ids["requirement"]}
                connection.execute(
                    text("UPDATE requirements SET current_version_id=NULL WHERE id=:id"),
                    params,
                )
                connection.execute(
                    text("DELETE FROM requirement_versions WHERE requirement_id=:id"),
                    params,
                )
                connection.execute(text("DELETE FROM requirements WHERE id=:id"), params)
            if "prompt" in ids:
                params = {"id": ids["prompt"]}
                connection.execute(
                    text("UPDATE prompt_definitions SET current_version_id=NULL WHERE id=:id"),
                    params,
                )
                connection.execute(text("DELETE FROM prompt_versions WHERE prompt_id=:id"), params)
                connection.execute(text("DELETE FROM prompt_definitions WHERE id=:id"), params)
            if "schema" in ids:
                connection.execute(
                    text("DELETE FROM output_schemas WHERE id=:id"),
                    {"id": ids["schema"]},
                )
            if "model" in ids:
                connection.execute(
                    text("DELETE FROM model_configurations WHERE id=:id"),
                    {"id": ids["model"]},
                )
            if "project" in ids:
                params = {"id": ids["project"]}
                connection.execute(text("DELETE FROM project_members WHERE project_id=:id"), params)
                connection.execute(text("DELETE FROM projects WHERE id=:id"), params)
        print("temporary_data=removed")


if __name__ == "__main__":
    main()
