import re
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.modules.projects.models import ProjectBusinessCounter


class BusinessCodeNamespace(StrEnum):
    REQUIREMENT = "REQUIREMENT"
    TEST_CASE = "TEST_CASE"
    SCENARIO = "SCENARIO"
    WEB_CASE = "WEB_CASE"


_CODE_FORMATS: dict[BusinessCodeNamespace, tuple[str, int]] = {
    BusinessCodeNamespace.REQUIREMENT: ("REQ", 4),
    BusinessCodeNamespace.TEST_CASE: ("TC", 5),
    BusinessCodeNamespace.SCENARIO: ("SC", 5),
    BusinessCodeNamespace.WEB_CASE: ("WC", 5),
}


def _seed_value(session: Session, project_id: int, model: Any, prefix: str) -> int:
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    codes = session.scalars(select(model.code).where(model.project_id == project_id)).all()
    existing_numbers = [
        int(match.group(1))
        for code in codes
        if (match := pattern.fullmatch(str(code))) is not None
    ]
    return max(existing_numbers, default=0) + 1


def _ensure_counter_row(
    session: Session,
    *,
    project_id: int,
    namespace: BusinessCodeNamespace,
    seed: int,
) -> None:
    values = {
        "project_id": project_id,
        "namespace": namespace.value,
        "next_value": seed,
    }
    dialect = session.get_bind().dialect.name
    if dialect == "mysql":
        statement = mysql_insert(ProjectBusinessCounter).values(**values)
        statement = statement.on_duplicate_key_update(
            next_value=ProjectBusinessCounter.next_value
        )
    elif dialect == "postgresql":
        statement = postgresql_insert(ProjectBusinessCounter).values(**values)
        statement = statement.on_conflict_do_nothing(
            index_elements=["project_id", "namespace"]
        )
    elif dialect == "sqlite":
        statement = sqlite_insert(ProjectBusinessCounter).values(**values)
        statement = statement.on_conflict_do_nothing(
            index_elements=["project_id", "namespace"]
        )
    else:
        counter = session.get(ProjectBusinessCounter, (project_id, namespace.value))
        if counter is None:
            session.add(ProjectBusinessCounter(**values))
            session.flush()
        return
    session.execute(statement)


def next_project_business_code(
    session: Session,
    *,
    project_id: int,
    namespace: BusinessCodeNamespace,
    model: Any,
) -> str:
    """Allocate a monotonic business code within one project and transaction."""

    prefix, width = _CODE_FORMATS[namespace]
    counter = session.scalar(
        select(ProjectBusinessCounter)
        .where(
            ProjectBusinessCounter.project_id == project_id,
            ProjectBusinessCounter.namespace == namespace.value,
        )
        .with_for_update()
    )
    if counter is None:
        _ensure_counter_row(
            session,
            project_id=project_id,
            namespace=namespace,
            seed=_seed_value(session, project_id, model, prefix),
        )
        counter = session.scalar(
            select(ProjectBusinessCounter)
            .where(
                ProjectBusinessCounter.project_id == project_id,
                ProjectBusinessCounter.namespace == namespace.value,
            )
            .with_for_update()
        )
    if counter is None:  # pragma: no cover - defensive guard for unsupported dialects
        raise RuntimeError("项目业务编号计数器初始化失败")
    value = counter.next_value
    counter.next_value = value + 1
    session.flush()
    return f"{prefix}-{value:0{width}d}"
