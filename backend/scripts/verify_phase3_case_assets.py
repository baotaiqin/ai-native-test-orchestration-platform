"""在真实 MySQL 上验证人工 Test Case、不可变版本与需求关联并清理临时数据。"""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.infrastructure.db.session import engine
from app.main import app


def _content(title: str, expected_result: str) -> dict:
    return {
        "title": title,
        "case_type": "API",
        "priority": "P0",
        "preconditions": ["用户账号有效"],
        "steps": [
            {"order": 1, "action": "提交账号密码", "expected": expected_result}
        ],
        "test_data": {"username": "demo"},
        "expected_result": expected_result,
        "tags": ["smoke", "login"],
        "confidence": 1,
        "request": {
            "method": "POST",
            "url": "{{base_url}}/api/v1/auth/login",
            "query_params": [
                {"name": "locale", "value": "zh-CN", "enabled": True}
            ],
            "headers": [
                {"name": "X-Request-ID", "value": "{{run_id}}", "enabled": True}
            ],
            "cookies": [],
            "body": {
                "type": "JSON",
                "content": {
                    "username": "demo",
                    "password": "{{secret.login_password}}",
                },
            },
            "auth": {"type": "NONE"},
            "timeout_ms": 10000,
            "follow_redirects": False,
        },
        "pre_actions": [
            {"name": "request_owner", "value": "{{seed_owner}}", "enabled": True}
        ],
        "extractors": [
            {
                "name": "access_token",
                "source": "JSONPATH",
                "expression": "$.data.token",
                "required": True,
                "enabled": True,
            },
            {
                "name": "trace_id",
                "source": "HEADER",
                "expression": "X-Trace-ID",
                "required": True,
                "enabled": True,
            },
        ],
        "post_actions": [
            {
                "name": "result_key",
                "value": "{{request_owner}}-{{response.status_code}}",
                "enabled": True,
            }
        ],
    }


def main() -> None:
    settings = get_settings()
    marker = uuid4().hex[:8].upper()
    project_id: int | None = None
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
                json={"name": "Phase 3 Case 验证", "code": f"CASE_VERIFY_{marker}"},
            )
            project.raise_for_status()
            project_id = project.json()["id"]
            database_url = make_url(settings.database_url)
            environment = client.post(
                "/api/v1/environments",
                headers=headers,
                json={
                    "project_id": project_id,
                    "name": "Phase 3 SQL 环境",
                    "code": f"sql_{marker.lower()}",
                    "base_url": "https://api.example.test",
                },
            )
            environment.raise_for_status()
            environment_id = environment.json()["id"]
            database_secret = client.post(
                "/api/v1/secrets",
                headers=headers,
                json={
                    "project_id": project_id,
                    "environment_id": environment_id,
                    "name": f"sql_password_{marker.lower()}",
                    "secret_type": "DB_PASSWORD",
                    "value": database_url.password or "unused",
                },
            )
            database_secret.raise_for_status()
            database_connection = client.post(
                "/api/v1/database-connections",
                headers=headers,
                json={
                    "project_id": project_id,
                    "environment_id": environment_id,
                    "name": "Phase 3 验证连接",
                    "host": database_url.host or "localhost",
                    "port": database_url.port or 3306,
                    "database_name": database_url.database or "ai_test_platform",
                    "username": database_url.username or "root",
                    "password_secret_id": database_secret.json()["id"],
                },
            )
            database_connection.raise_for_status()
            database_connection_id = database_connection.json()["id"]
            requirement = client.post(
                "/api/v1/requirements",
                headers=headers,
                json={
                    "project_id": project_id,
                    "title": "用户登录",
                    "markdown_content": "# 用户登录\n支持账号密码登录。",
                },
            )
            requirement.raise_for_status()
            requirement_id = requirement.json()["id"]
            created = client.post(
                "/api/v1/test-cases",
                headers=headers,
                json={
                    "project_id": project_id,
                    "requirement_id": requirement_id,
                    "content": _content("登录成功", "返回访问令牌"),
                    "change_note": "人工创建",
                },
            )
            created.raise_for_status()
            case_id = created.json()["id"]
            if created.json()["current_version"]["version_no"] != 1:
                raise RuntimeError("Test Case V1 未正确创建")
            request = created.json()["current_version"]["content"]["request"]
            if request["method"] != "POST" or request["body"]["type"] != "JSON":
                raise RuntimeError("API Request Template 未正确保存")
            runtime = client.post(
                "/api/v1/test-cases/runtime/preview",
                headers=headers,
                json={
                    "request": request,
                    "context": {
                        "base_url": "https://api.example.test",
                        "run_id": "run-001",
                        "secret.login_password": "masked-password",
                        "seed_owner": "tester",
                    },
                    "pre_actions": created.json()["current_version"]["content"][
                        "pre_actions"
                    ],
                    "response": {
                        "status_code": 200,
                        "json_body": {"data": {"token": "token-001"}},
                        "headers": {"x-trace-id": "trace-001"},
                        "cookies": {},
                    },
                    "extractors": created.json()["current_version"]["content"][
                        "extractors"
                    ],
                    "post_actions": created.json()["current_version"]["content"][
                        "post_actions"
                    ],
                },
            )
            runtime.raise_for_status()
            if runtime.json()["context"]["result_key"] != "tester-200":
                raise RuntimeError("Runtime Context 动作或变量替换不正确")
            if runtime.json()["extracted"]["trace_id"] != "trace-001":
                raise RuntimeError("Header 提取不正确")
            version = client.post(
                f"/api/v1/test-cases/{case_id}/versions",
                headers=headers,
                json={
                    "content": _content("登录成功 V2", "返回访问令牌和刷新令牌"),
                    "change_note": "补充刷新令牌断言",
                },
            )
            version.raise_for_status()
            versions = client.get(
                f"/api/v1/test-cases/{case_id}/versions", headers=headers
            )
            links = client.get(
                f"/api/v1/test-cases/requirements/{requirement_id}/links",
                headers=headers,
            )
            versions.raise_for_status()
            links.raise_for_status()
            if [item["version_no"] for item in versions.json()] != [2, 1]:
                raise RuntimeError("Test Case 不可变版本顺序不正确")
            if len(links.json()) != 1 or links.json()[0]["source"] != "MANUAL":
                raise RuntimeError("RequirementCaseLink 未正确创建")
            archived = client.post(
                f"/api/v1/test-cases/{case_id}/archive", headers=headers
            )
            archived.raise_for_status()
            if archived.json()["status"] != "ARCHIVED":
                raise RuntimeError("Test Case 归档失败")
            scenario_dsl = {
                "version": "1.0",
                "settings": {
                    "initial_variables": ["base_url"],
                    "cleanup_policy": "ALWAYS",
                },
                "nodes": [
                    {"id": "start", "type": "START", "name": "开始"},
                    {
                        "id": "request",
                        "type": "HTTP",
                        "name": "登录请求",
                        "config": {"url": "{{base_url}}/login", "method": "POST"},
                    },
                    {
                        "id": "extract",
                        "type": "EXTRACT",
                        "name": "提取令牌",
                        "config": {
                            "name": "token",
                            "source": "JSONPATH",
                            "expression": "$.data.token",
                        },
                    },
                    {"id": "end", "type": "END", "name": "结束"},
                ],
            }
            validation = client.post(
                "/api/v1/scenarios/validate",
                headers=headers,
                json={
                    "project_id": project_id,
                    "name": "登录业务场景",
                    "dsl": scenario_dsl,
                },
            )
            validation.raise_for_status()
            if not validation.json()["valid"]:
                raise RuntimeError("合法 Scenario DSL 未通过校验")
            scenario = client.post(
                "/api/v1/scenarios",
                headers=headers,
                json={
                    "project_id": project_id,
                    "name": "登录业务场景",
                    "dsl": scenario_dsl,
                },
            )
            scenario.raise_for_status()
            scenario_id = scenario.json()["id"]
            v2_dsl = {
                **scenario_dsl,
                "nodes": [
                    *scenario_dsl["nodes"][:-1],
                    {
                        "id": "wait",
                        "type": "WAIT",
                        "name": "等待同步",
                        "config": {"duration_ms": 100},
                    },
                    scenario_dsl["nodes"][-1],
                ],
            }
            scenario_v2 = client.post(
                f"/api/v1/scenarios/{scenario_id}/versions",
                headers=headers,
                json={"dsl": v2_dsl, "change_note": "增加等待节点"},
            )
            scenario_v2.raise_for_status()
            if scenario_v2.json()["version_no"] != 2:
                raise RuntimeError("Scenario V2 未正确创建")
            execution_dsl = {
                "version": "1.0",
                "settings": {
                    "initial_variables": ["status", "items"],
                    "max_loop_iterations": 10,
                },
                "nodes": [
                    {"id": "start", "type": "START", "name": "开始"},
                    {
                        "id": "condition",
                        "type": "IF",
                        "name": "判断状态",
                        "config": {"condition": "{{status}} == \"READY\""},
                    },
                    {
                        "id": "ready",
                        "type": "SET_VARIABLE",
                        "name": "记录分支",
                        "parent_id": "condition",
                        "config": {"name": "branch", "value": "ready"},
                    },
                    {
                        "id": "loop",
                        "type": "LOOP",
                        "name": "遍历项目",
                        "config": {"items": "{{items}}"},
                    },
                    {
                        "id": "remember",
                        "type": "SET_VARIABLE",
                        "name": "记录项目",
                        "parent_id": "loop",
                        "config": {"name": "last_item", "value": "{{loop_item}}"},
                    },
                    {
                        "id": "wait",
                        "type": "WAIT",
                        "name": "等待同步",
                        "config": {"duration_ms": 50},
                    },
                    {"id": "end", "type": "END", "name": "结束"},
                ],
            }
            execution = client.post(
                "/api/v1/scenarios/execute-preview",
                headers=headers,
                json={
                    "dsl": execution_dsl,
                    "context": {"status": "READY", "items": ["a", "b", "c"]},
                },
            )
            execution.raise_for_status()
            if execution.json()["context"].get("last_item") != "c":
                raise RuntimeError("LOOP Runtime Context 结果不正确")
            remember_traces = [
                item
                for item in execution.json()["traces"]
                if item["node_id"] == "remember"
            ]
            if len(remember_traces) != 3:
                raise RuntimeError("LOOP 子节点执行次数不正确")
            data_node_dsl = {
                "version": "1.0",
                "settings": {"initial_variables": ["project_row_id"]},
                "nodes": [
                    {"id": "start", "type": "START", "name": "开始"},
                    {
                        "id": "query",
                        "type": "SQL_QUERY",
                        "name": "查询验证项目",
                        "config": {
                            "connection_id": database_connection_id,
                            "sql": "SELECT id, name FROM projects WHERE id=%(project_id)s",
                            "params": {"project_id": "{{project_row_id}}"},
                            "result_variable": "project_rows",
                            "max_rows": 10,
                        },
                    },
                    {
                        "id": "execute",
                        "type": "SQL_EXECUTE",
                        "name": "预览更新",
                        "config": {
                            "connection_id": database_connection_id,
                            "sql": "UPDATE projects SET name=name WHERE id=%(project_id)s",
                            "params": {"project_id": "{{project_row_id}}"},
                            "result_variable": "updated_rows",
                        },
                    },
                    {
                        "id": "script",
                        "type": "PYTHON_SCRIPT",
                        "name": "整理查询结果",
                        "config": {
                            "script": (
                                'context["project_count"] = len(context["project_rows"])\n'
                                'if context["project_count"] == 1:\n'
                                '    context["data_status"] = "ready"'
                            )
                        },
                    },
                    {"id": "end", "type": "END", "name": "结束"},
                ],
            }
            data_execution = client.post(
                "/api/v1/scenarios/execute-preview",
                headers=headers,
                json={
                    "project_id": project_id,
                    "dsl": data_node_dsl,
                    "context": {"project_row_id": project_id},
                },
            )
            data_execution.raise_for_status()
            if data_execution.json()["context"].get("data_status") != "ready":
                raise RuntimeError("SQL/Python 数据节点结果不正确")
            print("manual_test_case=V1")
            print("api_request_template=ok")
            print("runtime_context_and_extractors=ok")
            print("immutable_case_version=V2")
            print("requirement_case_link=ok")
            print("case_archive=ok")
            print("scenario_dsl_validation=ok")
            print("scenario_immutable_version=V2")
            print("if_loop_wait_execution_preview=ok")
            print("mysql_sql_query_execute_preview=ok")
            print("restricted_python_script=ok")
        finally:
            if project_id is not None:
                with engine.begin() as connection:
                    params = {"project_id": project_id}
                    statements = [
                        """UPDATE scenarios SET current_version_id=NULL
                        WHERE project_id=:project_id""",
                        """DELETE FROM scenario_versions
                        WHERE scenario_id IN (
                            SELECT id FROM scenarios WHERE project_id=:project_id
                        )""",
                        "DELETE FROM scenarios WHERE project_id=:project_id",
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
                        "DELETE FROM test_cases WHERE project_id=:project_id",
                        """UPDATE requirements SET current_version_id=NULL
                        WHERE project_id=:project_id""",
                        """DELETE FROM requirement_versions
                        WHERE requirement_id IN (
                            SELECT id FROM requirements WHERE project_id=:project_id
                        )""",
                        "DELETE FROM requirements WHERE project_id=:project_id",
                        "DELETE FROM database_connections WHERE project_id=:project_id",
                        "DELETE FROM secrets WHERE project_id=:project_id",
                        "DELETE FROM environments WHERE project_id=:project_id",
                        "DELETE FROM project_members WHERE project_id=:project_id",
                        "DELETE FROM projects WHERE id=:project_id",
                    ]
                    for statement in statements:
                        connection.execute(text(statement), params)
                print("temporary_data=removed")


if __name__ == "__main__":
    main()
