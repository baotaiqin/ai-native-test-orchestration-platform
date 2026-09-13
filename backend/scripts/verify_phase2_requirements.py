"""在真实 MySQL 上验证 Requirement Markdown 与版本主链并清理临时数据。"""

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
                json={"name": "Phase 2 Requirement 验证", "code": f"REQ_VERIFY_{marker}"},
            )
            project.raise_for_status()
            project_id = project.json()["id"]
            markdown = """# 结算服务

支持订单结算。

## 提交结算

用户提交结算请求。

### 验收标准

- 返回结算编号
- 重复请求保持幂等
"""
            payload = {
                "project_id": project_id,
                "filename": "settlement.md",
                "content": markdown,
            }
            preview = client.post("/api/v1/requirements/import/preview", json=payload)
            preview.raise_for_status()
            if preview.json()["total"] != 3:
                raise RuntimeError("Markdown 标题树解析数量不正确")

            imported = client.post(
                "/api/v1/requirements/import", headers=headers, json=payload
            )
            imported.raise_for_status()
            root = imported.json()["root_items"][0]
            if root["children"][0]["children"][0]["title"] != "验收标准":
                raise RuntimeError("Requirement Tree 层级不正确")

            requirement_id = root["id"]
            new_version = client.post(
                f"/api/v1/requirements/{requirement_id}/versions",
                headers=headers,
                json={
                    "markdown_content": "# 结算服务\n\n支持订单结算与退款。",
                    "change_summary": "增加退款范围",
                },
            )
            new_version.raise_for_status()
            diff = client.get(
                f"/api/v1/requirements/{requirement_id}/diff?from_version=1&to_version=2",
                headers=headers,
            )
            diff.raise_for_status()
            if diff.json()["additions"] < 1 or "退款" not in diff.json()["unified_diff"]:
                raise RuntimeError("版本 Diff 结果不正确")

            print("requirement_markdown_preview=ok")
            print("requirement_tree=ok")
            print("requirement_version=V2")
            print("requirement_diff=ok")
        finally:
            if project_id is not None:
                with engine.begin() as connection:
                    parameters = {"project_id": project_id}
                    connection.execute(
                        text(
                            "UPDATE requirements SET current_version_id=NULL "
                            "WHERE project_id=:project_id"
                        ),
                        parameters,
                    )
                    connection.execute(
                        text(
                            "DELETE FROM requirement_versions WHERE requirement_id IN "
                            "(SELECT id FROM requirements WHERE project_id=:project_id)"
                        ),
                        parameters,
                    )
                    connection.execute(
                        text("DELETE FROM requirements WHERE project_id=:project_id"), parameters
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
