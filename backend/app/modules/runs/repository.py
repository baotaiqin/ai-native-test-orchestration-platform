from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.modules.runs.models import (
    CaseRun,
    RunDispatchOutbox,
    RunWebExecutionResult,
    StepRun,
    TestRun,
)


def get_run(session: Session, run_id: str, *, for_update: bool = False) -> TestRun | None:
    statement = select(TestRun).where(TestRun.id == run_id)
    if for_update:
        statement = statement.with_for_update()
    return session.scalar(statement)


def get_run_with_children(session: Session, run_id: str) -> TestRun | None:
    return session.scalar(
        select(TestRun)
        .options(selectinload(TestRun.case_runs).selectinload(CaseRun.step_runs))
        .where(TestRun.id == run_id)
    )


def list_web_execution_results(session: Session, run_id: str) -> list[RunWebExecutionResult]:
    return list(
        session.scalars(
            select(RunWebExecutionResult)
            .where(RunWebExecutionResult.run_id == run_id)
            .order_by(RunWebExecutionResult.case_run_id.asc(), RunWebExecutionResult.id.asc())
        ).all()
    )


def list_runs(
    session: Session, project_id: int, *, offset: int, limit: int
) -> tuple[list[TestRun], int]:
    items = list(
        session.scalars(
            select(TestRun)
            .where(TestRun.project_id == project_id)
            .order_by(TestRun.created_at.desc(), TestRun.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )
    total = session.scalar(
        select(func.count(TestRun.id)).where(
            TestRun.project_id == project_id
        )
    )
    return items, int(total or 0)


def list_case_runs(session: Session, run_id: str) -> list[CaseRun]:
    return list(
        session.scalars(
            select(CaseRun)
            .where(CaseRun.run_id == run_id)
            .order_by(CaseRun.sequence_no.asc(), CaseRun.id.asc())
        ).all()
    )


def list_step_runs(session: Session, case_run_id: int) -> list[StepRun]:
    return list(
        session.scalars(
            select(StepRun)
            .where(StepRun.case_run_id == case_run_id)
            .order_by(StepRun.sequence_no.asc(), StepRun.id.asc())
        ).all()
    )


def get_dispatch_outbox_for_run(
    session: Session, run_id: str, *, for_update: bool = False
) -> RunDispatchOutbox | None:
    statement = select(RunDispatchOutbox).where(RunDispatchOutbox.run_id == run_id)
    if for_update:
        statement = statement.with_for_update()
    return session.scalar(statement)


def get_dispatch_outbox_by_message(
    session: Session, message_id: str, *, for_update: bool = False
) -> RunDispatchOutbox | None:
    statement = select(RunDispatchOutbox).where(
        RunDispatchOutbox.message_id == message_id
    )
    if for_update:
        statement = statement.with_for_update()
    return session.scalar(statement)
