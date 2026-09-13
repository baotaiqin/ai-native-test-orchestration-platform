"""在真实 MySQL 上验证 Prompt 版本、渲染与回滚并清理临时数据。"""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import get_settings
from app.infrastructure.db.session import engine
from app.main import app


def main() -> None:
    settings = get_settings()
    marker = uuid4().hex[:8].upper()
    prompt_id: int | None = None
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
            created = client.post(
                "/api/v1/prompt-center", headers=headers,
                json={
                    "name": f"Prompt 验证 {marker}",
                    "code": f"PROMPT_VERIFY_{marker}",
                    "task_type": "REQUIREMENT_REVIEW",
                    "system_prompt": "你是 {{ role }}。",
                    "user_template": "评审需求：{{ requirement }}，项目：{{ project }}",
                },
            )
            created.raise_for_status()
            prompt_id = created.json()["id"]
            version_one_id = created.json()["current_version_id"]
            rendered = client.post(
                f"/api/v1/prompt-center/{prompt_id}/render", headers=headers,
                json={"variables": {"role": "测试专家", "requirement": "支付"}},
            )
            rendered.raise_for_status()
            if rendered.json()["missing_variables"] != ["project"]:
                raise RuntimeError("模板缺失变量识别不正确")
            version_two = client.post(
                f"/api/v1/prompt-center/{prompt_id}/versions", headers=headers,
                json={
                    "system_prompt": "你是高级测试专家。",
                    "user_template": "评审：{{ requirement }}",
                    "change_note": "验证第二版",
                },
            )
            version_two.raise_for_status()
            if version_two.json()["current_version"]["version_no"] != 2:
                raise RuntimeError("Prompt 版本未递增")
            rollback = client.post(
                f"/api/v1/prompt-center/{prompt_id}/versions/"
                f"{version_one_id}/rollback",
                headers=headers,
            )
            rollback.raise_for_status()
            if rollback.json()["current_version"]["version_no"] != 1:
                raise RuntimeError("Prompt 回滚失败")
            print("prompt_version=V2")
            print("prompt_variable_render=ok")
            print("prompt_missing_variable=ok")
            print("prompt_rollback=V1")
        finally:
            if prompt_id is not None:
                with engine.begin() as connection:
                    parameters = {"prompt_id": prompt_id}
                    connection.execute(
                        text(
                            "UPDATE prompt_definitions SET current_version_id=NULL "
                            "WHERE id=:prompt_id"
                        ), parameters,
                    )
                    connection.execute(
                        text("DELETE FROM prompt_versions WHERE prompt_id=:prompt_id"),
                        parameters,
                    )
                    connection.execute(
                        text("DELETE FROM prompt_definitions WHERE id=:prompt_id"),
                        parameters,
                    )
                print("temporary_data=removed")


if __name__ == "__main__":
    main()
