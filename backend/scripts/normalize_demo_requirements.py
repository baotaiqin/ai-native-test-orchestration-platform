"""清理内置 Demo 的旧需求树，并重排当前演示需求的业务编号。

默认仅预览。只有显式传入 ``--execute AI_DEMO`` 才会提交变更。
脚本只删除旧版 ``builtin-ai-demo-requirement.md`` 导入的需求；若存在评审、
AI 生成、正式用例关联或运行快照引用，则拒绝执行。
"""

from __future__ import annotations

import argparse

from sqlalchemy import delete, func, select, update

from app.infrastructure.db.session import SessionLocal
from app.modules.projects.business_codes import BusinessCodeNamespace
from app.modules.projects.models import Project, ProjectBusinessCounter
from app.modules.requirement_reviews.models import RequirementReview
from app.modules.requirements.models import Requirement, RequirementVersion
from app.modules.run_requirement_snapshots.models import RunRequirementSource
from app.modules.test_cases.models import (
    AiCaseGeneration,
    AiCaseGenerationTask,
    RequirementCaseLink,
)

DEMO_PROJECT_CODE = "AI_DEMO"
LEGACY_FILENAME = "builtin-ai-demo-requirement.md"
CURRENT_FILENAME = "builtin-ai-demo-requirement-v2.md"


def _count(session, model, requirement_ids: list[int]) -> int:
    if not requirement_ids:
        return 0
    return int(
        session.scalar(
            select(func.count()).select_from(model).where(model.requirement_id.in_(requirement_ids))
        )
        or 0
    )


def _preorder(requirements: list[Requirement]) -> list[Requirement]:
    by_parent: dict[int | None, list[Requirement]] = {}
    ids = {item.id for item in requirements}
    for item in requirements:
        parent_id = item.parent_id if item.parent_id in ids else None
        by_parent.setdefault(parent_id, []).append(item)
    for children in by_parent.values():
        children.sort(key=lambda item: (item.order_index, item.id))

    ordered: list[Requirement] = []

    def visit(item: Requirement) -> None:
        ordered.append(item)
        for child in by_parent.get(item.id, []):
            visit(child)

    for root in by_parent.get(None, []):
        visit(root)
    return ordered


def normalize_demo_requirements(*, execute: bool) -> dict[str, object]:
    with SessionLocal() as session:
        project = session.scalar(select(Project).where(Project.code == DEMO_PROJECT_CODE))
        if project is None:
            return {"executed": False, "project_found": False}

        legacy = list(
            session.scalars(
                select(Requirement)
                .join(RequirementVersion, RequirementVersion.requirement_id == Requirement.id)
                .where(
                    Requirement.project_id == project.id,
                    RequirementVersion.source_filename == LEGACY_FILENAME,
                )
                .distinct()
            )
        )
        current = list(
            session.scalars(
                select(Requirement)
                .join(RequirementVersion, RequirementVersion.requirement_id == Requirement.id)
                .where(
                    Requirement.project_id == project.id,
                    RequirementVersion.source_filename == CURRENT_FILENAME,
                )
                .distinct()
            )
        )
        legacy_ids = [item.id for item in legacy]
        references = {
            "requirement_reviews": _count(session, RequirementReview, legacy_ids),
            "generation_tasks": _count(session, AiCaseGenerationTask, legacy_ids),
            "generations": _count(session, AiCaseGeneration, legacy_ids),
            "case_links": _count(session, RequirementCaseLink, legacy_ids),
            "run_snapshots": _count(session, RunRequirementSource, legacy_ids),
        }
        ordered = _preorder(current)
        summary: dict[str, object] = {
            "executed": execute,
            "project_found": True,
            "project_id": project.id,
            "legacy_requirements": len(legacy_ids),
            "current_requirements": len(ordered),
            "references": references,
            "planned_range": (
                f"REQ-0001..REQ-{len(ordered):04d}" if ordered else None
            ),
        }
        if not execute:
            return summary
        if any(references.values()):
            raise RuntimeError("旧需求仍被评审、用例或运行快照引用，已拒绝删除。")
        if not ordered:
            raise RuntimeError("未找到当前版内置 Demo 需求，已拒绝执行。")

        if legacy_ids:
            session.execute(
                update(Requirement)
                .where(Requirement.id.in_(legacy_ids))
                .values(current_version_id=None)
            )
            session.execute(
                delete(RequirementVersion).where(
                    RequirementVersion.requirement_id.in_(legacy_ids)
                )
            )
            session.execute(delete(Requirement).where(Requirement.id.in_(legacy_ids)))
            session.flush()

        for item in ordered:
            item.code = f"TEMP-REQ-{item.id}"
        session.flush()
        for index, item in enumerate(ordered, start=1):
            item.code = f"REQ-{index:04d}"
        session.flush()

        counter = session.get(
            ProjectBusinessCounter,
            (project.id, BusinessCodeNamespace.REQUIREMENT.value),
        )
        if counter is None:
            counter = ProjectBusinessCounter(
                project_id=project.id,
                namespace=BusinessCodeNamespace.REQUIREMENT.value,
                next_value=len(ordered) + 1,
            )
            session.add(counter)
        else:
            counter.next_value = len(ordered) + 1
        session.commit()
        return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="规范内置 Demo 需求业务编号")
    parser.add_argument(
        "--execute",
        metavar="PROJECT_CODE",
        help="执行变更时必须显式传入 AI_DEMO",
    )
    args = parser.parse_args()
    if args.execute is not None and args.execute != DEMO_PROJECT_CODE:
        parser.error("--execute 只接受 AI_DEMO")
    print(normalize_demo_requirements(execute=args.execute == DEMO_PROJECT_CODE))


if __name__ == "__main__":
    main()
