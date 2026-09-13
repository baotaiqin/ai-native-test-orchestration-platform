"""在真实 MySQL 上验证 OpenAPI 预览、Diff 与导入主链并清理临时数据。"""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import get_settings
from app.infrastructure.db.session import engine
from app.main import app

SPEC = """
openapi: 3.0.3
info: {title: Verification API, version: 1.0.0}
components:
  schemas:
    Result:
      type: object
      properties:
        id: {type: integer}
paths:
  /verification/items/{itemId}:
    get:
      summary: 查询验证项目
      parameters:
        - name: itemId
          in: path
          required: true
          schema: {type: integer}
      responses:
        '200':
          description: success
          content:
            application/json:
              schema: {$ref: '#/components/schemas/Result'}
"""


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
                json={"name": "Phase 2 Swagger 验证", "code": f"API_VERIFY_{marker}"},
            )
            project.raise_for_status()
            project_id = project.json()["id"]
            payload = {
                "project_id": project_id,
                "filename": "verification.yaml",
                "content": SPEC,
            }
            preview = client.post(
                "/api/v1/api-definitions/import/preview", headers=headers, json=payload
            )
            preview.raise_for_status()
            if preview.json()["added_count"] != 1:
                raise RuntimeError("首次预览未识别为新增")

            imported = client.post(
                "/api/v1/api-definitions/import", headers=headers, json=payload
            )
            imported.raise_for_status()
            if imported.json()["created_count"] != 1:
                raise RuntimeError("API Definition 未成功创建")

            unchanged = client.post(
                "/api/v1/api-definitions/import/preview", headers=headers, json=payload
            )
            unchanged.raise_for_status()
            if unchanged.json()["unchanged_count"] != 1:
                raise RuntimeError("重复导入未识别为无变化")

            changed_payload = {
                **payload,
                "content": SPEC.replace("查询验证项目", "查询验证项目详情"),
            }
            changed = client.post(
                "/api/v1/api-definitions/import/preview",
                headers=headers,
                json=changed_payload,
            )
            changed.raise_for_status()
            if changed.json()["changed_count"] != 1:
                raise RuntimeError("契约变化未识别")
            second = client.post(
                "/api/v1/api-definitions/import", headers=headers, json=changed_payload
            )
            second.raise_for_status()
            if second.json()["version_no"] != 2:
                raise RuntimeError("导入版本未递增")

            definitions = client.get(
                f"/api/v1/api-definitions?project_id={project_id}", headers=headers
            )
            definitions.raise_for_status()
            if definitions.json()["total"] != 1:
                raise RuntimeError("接口资产列表数量不正确")
            print("openapi_yaml_parse=ok")
            print("api_definition_import=V2")
            print("api_contract_diff=ok")
            print("api_definition_list=ok")
        finally:
            if project_id is not None:
                with engine.begin() as connection:
                    parameters = {"project_id": project_id}
                    connection.execute(
                        text("DELETE FROM api_definitions WHERE project_id=:project_id"),
                        parameters,
                    )
                    connection.execute(
                        text(
                            "DELETE FROM api_definition_imports "
                            "WHERE project_id=:project_id"
                        ),
                        parameters,
                    )
                    connection.execute(
                        text("DELETE FROM project_members WHERE project_id=:project_id"),
                        parameters,
                    )
                    connection.execute(
                        text("DELETE FROM projects WHERE id=:project_id"), parameters
                    )
                print("temporary_data=removed")


if __name__ == "__main__":
    main()
