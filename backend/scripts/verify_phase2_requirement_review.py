"""用本机 Mock Provider 与真实 MySQL 验证需求评审人工治理并清理数据。"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import get_settings
from app.infrastructure.db.session import engine
from app.main import app

REVIEW = {
    "clarity_issues": ["锁定时长未说明"],
    "ambiguity": ["失败统计周期不明确"],
    "missing_rules": ["缺少解锁规则"],
    "exception_gaps": ["未定义依赖服务异常"],
    "testability": ["失败次数可做边界验证"],
    "acceptance_criteria_suggestions": ["失败 5 次锁定 30 分钟"],
    "overall_summary": "规则需要补充后再进入用例设计。",
}


class ReviewProvider(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        payload = {
            "id": "local-requirement-review",
            "choices": [{"message": {"content": json.dumps(REVIEW, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 600, "completion_tokens": 300},
        }
        content = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), ReviewProvider)
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
                json={"name": "Phase 2 Review 验证", "code": f"REVIEW_{marker}"},
            )
            project.raise_for_status()
            ids["project"] = project.json()["id"]
            model = client.post(
                "/api/v1/model-center", headers=headers,
                json={
                    "name": f"REVIEW_MODEL_{marker}",
                    "provider": "OPENAI_COMPATIBLE", "base_url": base_url,
                    "model_name": "review-model", "model_type": "TEXT",
                    "supports_structured_output": True,
                },
            )
            model.raise_for_status()
            ids["model"] = model.json()["id"]
            client.put(
                "/api/v1/model-center/bindings/project", headers=headers,
                json={
                    "project_id": ids["project"],
                    "task_type": "REQUIREMENT_REVIEW",
                    "primary_model_id": ids["model"], "max_fallback": 0,
                },
            ).raise_for_status()
            array_fields = (
                "clarity_issues", "ambiguity", "missing_rules", "exception_gaps",
                "testability", "acceptance_criteria_suggestions",
            )
            schema = client.post(
                "/api/v1/ai/output-schemas", headers=headers,
                json={
                    "name": f"REVIEW_SCHEMA_{marker}",
                    "schema_json": {
                        "type": "object", "required": [*array_fields, "overall_summary"],
                        "additionalProperties": False,
                        "properties": {
                            **{
                                key: {"type": "array", "items": {"type": "string"}}
                                for key in array_fields
                            },
                            "overall_summary": {"type": "string"},
                        },
                    },
                },
            )
            schema.raise_for_status()
            ids["schema"] = schema.json()["id"]
            prompt = client.post(
                "/api/v1/prompt-center", headers=headers,
                json={
                    "name": f"Review Prompt {marker}",
                    "code": f"REVIEW_PROMPT_{marker}",
                    "task_type": "REQUIREMENT_REVIEW",
                    "system_prompt": "输出评审 JSON",
                    "user_template": "评审 {{ requirement }}",
                    "output_schema_id": ids["schema"],
                },
            )
            prompt.raise_for_status()
            ids["prompt"] = prompt.json()["id"]
            requirement = client.post(
                "/api/v1/requirements", headers=headers,
                json={
                    "project_id": ids["project"], "title": "用户登录",
                    "markdown_content": "# 用户登录\n失败 5 次锁定。",
                },
            )
            requirement.raise_for_status()
            ids["requirement"] = requirement.json()["id"]
            original_version = requirement.json()["current_version_id"]
            generated = client.post(
                f"/api/v1/requirements/{ids['requirement']}/ai-reviews",
                headers=headers,
                json={"prompt_id": ids["prompt"], "additional_instructions": "关注锁定"},
            )
            generated.raise_for_status()
            ids["review"] = generated.json()["id"]
            edited_result = {**REVIEW, "overall_summary": "人工确认：需要补充锁定规则。"}
            client.patch(
                f"/api/v1/requirements/ai-reviews/{ids['review']}", headers=headers,
                json={"human_result": edited_result, "decision_note": "人工已核对"},
            ).raise_for_status()
            accepted = client.post(
                f"/api/v1/requirements/ai-reviews/{ids['review']}/decision",
                headers=headers, json={"action": "ACCEPT"},
            )
            accepted.raise_for_status()
            if accepted.json()["status"] != "ACCEPTED":
                raise RuntimeError("人工 Accept 未成功")
            current = client.get(
                f"/api/v1/requirements/{ids['requirement']}", headers=headers
            )
            current.raise_for_status()
            if current.json()["current_version_id"] != original_version:
                raise RuntimeError("AI 评审不应直接修改 Requirement")
            print("requirement_review_draft=ok")
            print("human_edit=ok")
            print("accept_and_freeze=ok")
            print("requirement_unchanged=ok")
    finally:
        server.shutdown()
        server.server_close()
        with engine.begin() as connection:
            if "review" in ids:
                connection.execute(
                    text("DELETE FROM requirement_reviews WHERE id=:id"), {"id": ids["review"]}
                )
            if "project" in ids:
                params = {"project_id": ids["project"]}
                connection.execute(
                    text("DELETE FROM ai_call_logs WHERE project_id=:project_id"), params
                )
                connection.execute(
                    text("DELETE FROM project_model_bindings WHERE project_id=:project_id"),
                    params,
                )
            if "requirement" in ids:
                params = {"requirement_id": ids["requirement"]}
                connection.execute(
                    text(
                        "UPDATE requirements SET current_version_id=NULL "
                        "WHERE id=:requirement_id"
                    ),
                    params,
                )
                connection.execute(
                    text("DELETE FROM requirement_versions WHERE requirement_id=:requirement_id"),
                    params,
                )
                connection.execute(
                    text("DELETE FROM requirements WHERE id=:requirement_id"), params
                )
            if "prompt" in ids:
                params = {"prompt_id": ids["prompt"]}
                connection.execute(
                    text(
                        "UPDATE prompt_definitions SET current_version_id=NULL "
                        "WHERE id=:prompt_id"
                    ),
                    params,
                )
                connection.execute(
                    text("DELETE FROM prompt_versions WHERE prompt_id=:prompt_id"), params
                )
                connection.execute(
                    text("DELETE FROM prompt_definitions WHERE id=:prompt_id"), params
                )
            if "schema" in ids:
                connection.execute(
                    text("DELETE FROM output_schemas WHERE id=:id"), {"id": ids["schema"]}
                )
            if "model" in ids:
                connection.execute(
                    text("DELETE FROM model_configurations WHERE id=:id"), {"id": ids["model"]}
                )
            if "project" in ids:
                params = {"project_id": ids["project"]}
                connection.execute(
                    text("DELETE FROM project_members WHERE project_id=:project_id"), params
                )
                connection.execute(text("DELETE FROM projects WHERE id=:project_id"), params)
        print("temporary_data=removed")


if __name__ == "__main__":
    main()
