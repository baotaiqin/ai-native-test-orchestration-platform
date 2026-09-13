from typing import Any

import pymysql
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from test_requirement_links import LinkClient
from test_requirement_links import _as as as_user
from test_requirement_links import requirement_link_client as _requirement_link_client

from app.core.exceptions import ResourceConflictError
from app.modules.auth.schemas import CurrentUser
from app.modules.requirement_links.schemas import RequirementAssetType, RequirementLinkCreate
from app.modules.requirement_links.service import (
    create_requirement_link,
    remove_requirement_link,
)
from app.modules.test_cases.models import RequirementCaseLink

requirement_link_client = _requirement_link_client


def _identity() -> CurrentUser:
    return CurrentUser(
        id="owner",
        username="owner",
        display_name="owner",
        roles=[],
    )


def _payload(relation_type: str) -> RequirementLinkCreate:
    return RequirementLinkCreate(
        asset_type=RequirementAssetType.TEST_CASE,
        asset_id=1,
        requirement_version_id=101,
        asset_version_id=1001,
        relation_type=relation_type,
    )


def _lock_error(statement: str, parameters: Any, code: int) -> OperationalError:
    return OperationalError(
        statement,
        parameters,
        pymysql.err.OperationalError(code, "synthetic lock conflict"),
    )


@pytest.mark.parametrize("operation", ["create", "remove"])
def test_association_writes_lock_requirement_before_current_link_reads(
    requirement_link_client: LinkClient,
    operation: str,
) -> None:
    factory = requirement_link_client.session_factory
    with factory() as session:
        original = None
        if operation == "remove":
            original = create_requirement_link(
                session,
                _identity(),
                1,
                _payload("LOCK_ORDER_REMOVE"),
            )

        before_commit = True
        locked_reads: list[tuple[str, bool]] = []

        def observe_orm_execute(execute_state: Any) -> None:
            if not before_commit or not execute_state.is_select:
                return
            tables = {
                getattr(table, "name", "")
                for table in execute_state.statement.get_final_froms()
            }
            table_name = next(
                (
                    name
                    for name in ("requirements", "requirement_case_links")
                    if name in tables
                ),
                None,
            )
            if table_name is not None:
                locked_reads.append(
                    (
                        table_name,
                        execute_state.statement._for_update_arg is not None,
                    )
                )

        def mark_committed(_session: Session) -> None:
            nonlocal before_commit
            before_commit = False

        event.listen(session, "do_orm_execute", observe_orm_execute)
        event.listen(session, "after_commit", mark_committed)
        try:
            if operation == "create":
                create_requirement_link(
                    session,
                    _identity(),
                    1,
                    _payload("LOCK_ORDER_CREATE"),
                )
            else:
                assert original is not None
                remove_requirement_link(session, _identity(), 1, original.id)
        finally:
            event.remove(session, "after_commit", mark_committed)
            event.remove(session, "do_orm_execute", observe_orm_execute)

    assert locked_reads[0] == ("requirements", True)
    link_reads = [locked for table, locked in locked_reads if table == "requirement_case_links"]
    assert len(link_reads) == (2 if operation == "create" else 1)
    assert all(link_reads)


@pytest.mark.parametrize("error_code", [1205, 1213])
@pytest.mark.parametrize("operation", ["create", "remove"])
def test_mysql_lock_conflict_during_flush_rolls_back_and_session_recovers(
    requirement_link_client: LinkClient,
    error_code: int,
    operation: str,
) -> None:
    factory = requirement_link_client.session_factory
    engine = factory.kw["bind"]
    relation_type = f"FLUSH_{operation.upper()}_{error_code}"

    with factory() as session:
        original = None
        if operation == "remove":
            original = create_requirement_link(
                session,
                _identity(),
                1,
                _payload(relation_type),
            )
        marker = (
            "insert into requirement_case_links"
            if operation == "create"
            else "update requirement_case_links"
        )
        faults: list[int] = []

        def fail_flush(
            _connection: Any,
            _cursor: Any,
            statement: str,
            parameters: Any,
            _context: Any,
            _many: bool,
        ) -> None:
            if not faults and marker in statement.lower():
                faults.append(error_code)
                raise _lock_error(statement, parameters, error_code)

        event.listen(engine, "before_cursor_execute", fail_flush)
        try:
            with pytest.raises(ResourceConflictError, match="数据库锁冲突"):
                if operation == "create":
                    create_requirement_link(
                        session,
                        _identity(),
                        1,
                        _payload(relation_type),
                    )
                else:
                    assert original is not None
                    remove_requirement_link(session, _identity(), 1, original.id)
        finally:
            event.remove(engine, "before_cursor_execute", fail_flush)
        assert faults == [error_code]
        assert session.scalar(select(1)) == 1

    with factory() as session:
        rows = list(
            session.scalars(
                select(RequirementCaseLink).where(
                    RequirementCaseLink.relation_type == relation_type
                )
            ).all()
        )
        if operation == "create":
            assert rows == []
        else:
            assert len(rows) == 1
            assert rows[0].status == "ACTIVE"
            assert rows[0].active_slot == 1


@pytest.mark.parametrize("error_code", [1205, 1213])
def test_mysql_lock_conflict_from_commit_rolls_back_pending_write(
    requirement_link_client: LinkClient,
    monkeypatch: pytest.MonkeyPatch,
    error_code: int,
) -> None:
    factory = requirement_link_client.session_factory
    relation_type = f"COMMIT_{error_code}"

    with factory() as session:
        original_commit = session.commit

        def fail_commit() -> None:
            raise _lock_error("COMMIT", (), error_code)

        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(ResourceConflictError, match="数据库锁冲突"):
            create_requirement_link(
                session,
                _identity(),
                1,
                _payload(relation_type),
            )
        monkeypatch.setattr(session, "commit", original_commit)
        assert session.scalar(select(1)) == 1

    with factory() as session:
        assert session.scalar(
            select(func.count())
            .select_from(RequirementCaseLink)
            .where(RequirementCaseLink.relation_type == relation_type)
        ) == 0


def test_unrelated_operational_error_is_rolled_back_but_not_mapped(
    requirement_link_client: LinkClient,
) -> None:
    factory = requirement_link_client.session_factory
    engine = factory.kw["bind"]
    faults: list[int] = []

    def fail_current_read(
        _connection: Any,
        _cursor: Any,
        statement: str,
        parameters: Any,
        _context: Any,
        _many: bool,
    ) -> None:
        if not faults and "from requirement_case_links" in statement.lower():
            faults.append(2006)
            raise _lock_error(statement, parameters, 2006)

    with factory() as session:
        event.listen(engine, "before_cursor_execute", fail_current_read)
        try:
            with pytest.raises(OperationalError) as captured:
                create_requirement_link(
                    session,
                    _identity(),
                    1,
                    _payload("NON_LOCK_OPERATIONAL"),
                )
        finally:
            event.remove(engine, "before_cursor_execute", fail_current_read)
        assert captured.value.orig.args[0] == 2006
        assert session.scalar(select(1)) == 1


def test_post_commit_response_read_error_does_not_report_a_rolled_back_write(
    requirement_link_client: LinkClient,
) -> None:
    factory = requirement_link_client.session_factory
    engine = factory.kw["bind"]
    relation_type = "POST_COMMIT_READ"
    committed = False
    faults: list[int] = []

    with factory() as session:
        def mark_committed(_session: Session) -> None:
            nonlocal committed
            committed = True

        def fail_response_read(
            _connection: Any,
            _cursor: Any,
            statement: str,
            parameters: Any,
            _context: Any,
            _many: bool,
        ) -> None:
            if (
                committed
                and not faults
                and "from requirement_case_links" in statement.lower()
            ):
                faults.append(1205)
                raise _lock_error(statement, parameters, 1205)

        event.listen(session, "after_commit", mark_committed)
        event.listen(engine, "before_cursor_execute", fail_response_read)
        try:
            with pytest.raises(OperationalError):
                create_requirement_link(
                    session,
                    _identity(),
                    1,
                    _payload(relation_type),
                )
        finally:
            event.remove(engine, "before_cursor_execute", fail_response_read)
            event.remove(session, "after_commit", mark_committed)
    assert faults == [1205]

    with factory() as session:
        assert session.scalar(
            select(func.count())
            .select_from(RequirementCaseLink)
            .where(RequirementCaseLink.relation_type == relation_type)
        ) == 1


def test_cached_active_identity_cannot_overwrite_independent_http_removal(
    requirement_link_client: LinkClient,
) -> None:
    factory = requirement_link_client.session_factory
    with factory() as seed_session:
        created = create_requirement_link(
            seed_session,
            _identity(),
            1,
            _payload("IDENTITY_REFRESH"),
        )

    with factory() as stale_session:
        cached = stale_session.get(RequirementCaseLink, created.id)
        assert cached is not None and cached.status == "ACTIVE"
        client = as_user(requirement_link_client, _identity())
        removed = client.delete(f"/api/v1/requirements/1/links/{created.id}")
        assert removed.status_code == 200, removed.text
        original_audit = removed.json()
        assert cached.status == "ACTIVE"

        with pytest.raises(ResourceConflictError, match="已经移除"):
            remove_requirement_link(stale_session, _identity(), 1, created.id)
        stale_session.rollback()

        fetched = client.get(
            "/api/v1/requirements/1/links",
            params={"include_removed": True, "page_size": 100},
        )
        assert fetched.status_code == 200
        actual = next(
            item for item in fetched.json()["items"] if item["id"] == created.id
        )
        assert actual == original_audit
