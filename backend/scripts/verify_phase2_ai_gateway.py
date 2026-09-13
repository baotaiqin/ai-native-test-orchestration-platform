"""用本机 Mock Provider 与真实 MySQL 验证 Gateway fallback/Repair 并清理数据。"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import get_settings
from app.infrastructure.db.session import engine
from app.main import app


class MockProviderHandler(BaseHTTPRequestHandler):
    mode = "fallback"
    repair_calls = 0

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        model = body["model"]
        if self.mode == "fallback" and model == "verify-primary":
            self._json(503, {"error": "temporary unavailable"})
            return
        if self.mode == "repair" and model == "verify-primary":
            type(self).repair_calls += 1
            content = (
                '{"summary":"缺少 risks"}'
                if self.repair_calls == 1
                else '{"summary":"已修复","risks":["并发"]}'
            )
        else:
            content = '{"summary":"Fallback 成功","risks":["超时"]}'
        self._json(
            200,
            {
                "id": f"local-{self.mode}-{model}",
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    def _json(self, status_code: int, payload: dict) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockProviderHandler)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    settings = get_settings()
    marker = uuid4().hex[:8].upper()
    ids: dict[str, int] = {}
    model_ids: list[int] = []
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
                json={"name": "Phase 2 Gateway 验证", "code": f"GATEWAY_{marker}"},
            )
            project.raise_for_status()
            ids["project"] = project.json()["id"]
            for label, model_name in (
                ("PRIMARY", "verify-primary"), ("FALLBACK", "verify-fallback")
            ):
                model = client.post(
                    "/api/v1/model-center", headers=headers,
                    json={
                        "name": f"GATEWAY_{marker}_{label}",
                        "provider": "OPENAI_COMPATIBLE", "base_url": base_url,
                        "model_name": model_name, "model_type": "TEXT",
                        "supports_structured_output": True,
                    },
                )
                model.raise_for_status()
                model_ids.append(model.json()["id"])
            binding = client.put(
                "/api/v1/model-center/bindings/project", headers=headers,
                json={
                    "project_id": ids["project"],
                    "task_type": "REQUIREMENT_REVIEW",
                    "primary_model_id": model_ids[0],
                    "fallback_model_id": model_ids[1],
                    "max_fallback": 1,
                },
            )
            binding.raise_for_status()
            schema = client.post(
                "/api/v1/ai/output-schemas", headers=headers,
                json={
                    "name": f"GATEWAY_SCHEMA_{marker}",
                    "schema_json": {
                        "type": "object", "required": ["summary", "risks"],
                        "properties": {
                            "summary": {"type": "string"},
                            "risks": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
            )
            schema.raise_for_status()
            ids["schema"] = schema.json()["id"]
            prompt = client.post(
                "/api/v1/prompt-center", headers=headers,
                json={
                    "name": f"Gateway Prompt {marker}",
                    "code": f"GATEWAY_PROMPT_{marker}",
                    "task_type": "REQUIREMENT_REVIEW",
                    "system_prompt": "只输出 JSON",
                    "user_template": "评审 {{ requirement }}",
                    "output_schema_id": ids["schema"],
                },
            )
            prompt.raise_for_status()
            ids["prompt"] = prompt.json()["id"]
            payload = {
                "project_id": ids["project"],
                "task_type": "REQUIREMENT_REVIEW",
                "prompt_id": ids["prompt"],
                "variables": {"requirement": "支付需求"},
            }
            fallback = client.post(
                "/api/v1/ai/generate", headers=headers, json=payload
            )
            fallback.raise_for_status()
            if not fallback.json()["fallback_used"]:
                raise RuntimeError("主模型 503 后未使用 fallback")
            MockProviderHandler.mode = "repair"
            repair = client.post(
                "/api/v1/ai/generate", headers=headers, json=payload
            )
            repair.raise_for_status()
            if repair.json()["fallback_used"] or not repair.json()["repair_used"]:
                raise RuntimeError("结构化错误未按同模型 Repair 策略执行")
            logs = client.get(
                f"/api/v1/ai/calls?project_id={ids['project']}", headers=headers
            )
            logs.raise_for_status()
            if logs.json()["total"] != 2:
                raise RuntimeError("Gateway 调用日志数量不正确")
            print("openai_compatible_http=ok")
            print("retryable_fallback_once=ok")
            print("quality_error_same_model_repair=ok")
            print("real_ai_call_audit=ok")
    finally:
        server.shutdown()
        server.server_close()
        with engine.begin() as connection:
            if "project" in ids:
                parameters = {"project_id": ids["project"]}
                connection.execute(
                    text("DELETE FROM ai_call_logs WHERE project_id=:project_id"), parameters
                )
                connection.execute(
                    text(
                        "DELETE FROM project_model_bindings WHERE project_id=:project_id"
                    ), parameters,
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
            for model_id in model_ids:
                connection.execute(
                    text("DELETE FROM model_configurations WHERE id=:model_id"),
                    {"model_id": model_id},
                )
            if "project" in ids:
                parameters = {"project_id": ids["project"]}
                connection.execute(
                    text("DELETE FROM project_members WHERE project_id=:project_id"), parameters
                )
                connection.execute(
                    text("DELETE FROM projects WHERE id=:project_id"), parameters
                )
        print("temporary_data=removed")


if __name__ == "__main__":
    main()
