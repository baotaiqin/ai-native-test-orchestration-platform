"""清理内置 Demo 项目的 AI 用例历史。

默认只输出待清理数量；只有显式传入 ``--execute AI_DEMO`` 才会提交删除。
清理范围限定为 AI_DEMO 项目的 AI 用例生成任务、建议、相关 AI 调用记录，
以及该项目中 source=AI 且尚未被运行引用的正式 API 用例。
"""

from __future__ import annotations

import argparse

from sqlalchemy import delete, func, select, update

from app.infrastructure.db.session import SessionLocal
from app.modules.projects.models import Project
from app.modules.prompt_center.models import AiCallLog
from app.modules.runs.models import CaseRun, TestRun
from app.modules.test_cases.models import (
    AiCaseDesignTask,
    AiCaseGeneration,
    AiCaseGenerationTask,
    RequirementCaseLink,
    TestCase,
    TestCaseVersion,
)

DEMO_PROJECT_CODE = "AI_DEMO"


def _positive_count(session, statement) -> int:
    return int(session.scalar(statement) or 0)


def clear_demo_case_history(*, execute: bool) -> dict[str, int | bool]:
    with SessionLocal() as session:
        project = session.scalar(select(Project).where(Project.code == DEMO_PROJECT_CODE))
        if project is None:
            return {"executed": False, "project_found": False}

        generation_ids = list(
            session.scalars(
                select(AiCaseGeneration.id).where(AiCaseGeneration.project_id == project.id)
            )
        )
        ai_call_ids = list(
            session.scalars(
                select(AiCallLog.id).where(
                    AiCallLog.project_id == project.id,
                    AiCallLog.task_type.in_(["API_TEST_DESIGN", "API_CASE_GENERATE"]),
                )
            )
        )
        case_ids = list(
            session.scalars(
                select(TestCase.id).where(
                    TestCase.project_id == project.id,
                    TestCase.source == "AI",
                )
            )
        )

        run_references = 0
        if case_ids:
            run_references += _positive_count(
                session, select(func.count(TestRun.id)).where(TestRun.case_id.in_(case_ids))
            )
            run_references += _positive_count(
                session, select(func.count(CaseRun.id)).where(CaseRun.case_id.in_(case_ids))
            )

        summary: dict[str, int | bool] = {
            "executed": execute,
            "project_found": True,
            "project_id": project.id,
            "generation_tasks": _positive_count(
                session,
                select(func.count(AiCaseGenerationTask.id)).where(
                    AiCaseGenerationTask.project_id == project.id
                ),
            ),
            "design_tasks": _positive_count(
                session,
                select(func.count(AiCaseDesignTask.id)).where(
                    AiCaseDesignTask.project_id == project.id
                ),
            ),
            "generations": len(generation_ids),
            "formal_ai_cases": len(case_ids),
            "run_references": run_references,
            "ai_call_logs": len(ai_call_ids),
        }
        if not execute:
            return summary
        if run_references:
            raise RuntimeError(
                "Demo AI 用例已被运行记录引用，本安全清理脚本不会级联删除运行证据。"
            )

        session.execute(
            delete(AiCaseDesignTask).where(AiCaseDesignTask.project_id == project.id)
        )
        session.execute(
            delete(AiCaseGenerationTask).where(
                AiCaseGenerationTask.project_id == project.id
            )
        )
        if generation_ids:
            session.execute(
                delete(AiCaseGeneration).where(AiCaseGeneration.id.in_(generation_ids))
            )
        if case_ids:
            session.execute(
                delete(RequirementCaseLink).where(
                    RequirementCaseLink.case_id.in_(case_ids)
                )
            )
            session.execute(
                update(TestCase)
                .where(TestCase.id.in_(case_ids))
                .values(current_version_id=None)
            )
            session.execute(
                delete(TestCaseVersion).where(TestCaseVersion.case_id.in_(case_ids))
            )
            session.execute(delete(TestCase).where(TestCase.id.in_(case_ids)))
        if ai_call_ids:
            session.execute(delete(AiCallLog).where(AiCallLog.id.in_(ai_call_ids)))
        session.commit()
        return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="清理内置 Demo 的 AI 用例历史")
    parser.add_argument(
        "--execute",
        metavar="PROJECT_CODE",
        help="执行删除时必须显式传入 AI_DEMO",
    )
    args = parser.parse_args()
    if args.execute is not None and args.execute != DEMO_PROJECT_CODE:
        parser.error("--execute 只接受 AI_DEMO")
    print(clear_demo_case_history(execute=args.execute == DEMO_PROJECT_CODE))


if __name__ == "__main__":
    main()
