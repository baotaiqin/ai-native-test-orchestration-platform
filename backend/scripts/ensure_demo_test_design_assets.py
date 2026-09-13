"""为已存在的内置 Demo 补齐 AI 测试设计 Prompt 与模型绑定。

默认只检查；只有显式传入 ``--execute AI_DEMO`` 才提交数据库变更。
脚本不会调用模型、读取 API Key 或创建任何 AI 结果。
"""

from __future__ import annotations

import argparse

from sqlalchemy import select

from app.infrastructure.db.session import SessionLocal
from app.modules.auth.schemas import CurrentUser
from app.modules.demo_bootstrap.service import (
    MODEL_NAME,
    PROJECT_CODE,
    _ensure_prompts,
)
from app.modules.model_center.models import ModelConfiguration, ProjectModelBinding
from app.modules.model_center.schemas import AiTaskType, ProjectModelBindingUpsert
from app.modules.model_center.service import upsert_binding
from app.modules.projects.models import Project
from app.modules.prompt_center.models import PromptDefinition

PROMPT_CODE = "DEMO_API_TEST_DESIGN"


def ensure_assets(*, execute: bool) -> dict[str, object]:
    with SessionLocal() as session:
        project = session.scalar(select(Project).where(Project.code == PROJECT_CODE))
        model = session.scalar(
            select(ModelConfiguration).where(ModelConfiguration.name == MODEL_NAME)
        )
        prompt = session.scalar(
            select(PromptDefinition).where(PromptDefinition.code == PROMPT_CODE)
        )
        binding = None
        if project is not None:
            binding = session.scalar(
                select(ProjectModelBinding).where(
                    ProjectModelBinding.project_id == project.id,
                    ProjectModelBinding.task_type == AiTaskType.API_TEST_DESIGN.value,
                )
            )
        before = {
            "executed": execute,
            "project_found": project is not None,
            "model_found": model is not None,
            "prompt_found": prompt is not None,
            "binding_found": binding is not None,
        }
        if not execute:
            return before
        if project is None or model is None:
            raise RuntimeError("AI_DEMO 项目或内置 Demo 主模型不存在，请先完成首次初始化")
        user = CurrentUser(
            id=project.owner_id,
            username="demo-asset-maintainer",
            display_name="Demo 资产维护",
            roles=["ADMIN"],
        )
        prompts = _ensure_prompts(session, user)
        upsert_binding(
            session,
            user,
            ProjectModelBindingUpsert(
                project_id=project.id,
                task_type=AiTaskType.API_TEST_DESIGN,
                primary_model_id=model.id,
                max_fallback=0,
            ),
        )
        return {
            **before,
            "prompt_id": prompts[PROMPT_CODE].id,
            "binding_created_or_updated": True,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="补齐内置 Demo 的 AI 测试设计配置")
    parser.add_argument("--execute", metavar="PROJECT_CODE")
    args = parser.parse_args()
    if args.execute is not None and args.execute != PROJECT_CODE:
        parser.error("--execute 只接受 AI_DEMO")
    print(ensure_assets(execute=args.execute == PROJECT_CODE))


if __name__ == "__main__":
    main()
