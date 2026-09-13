from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.infrastructure.db.base import Base
from app.modules.projects.business_codes import (
    BusinessCodeNamespace,
    next_project_business_code,
)
from app.modules.projects.models import Project, ProjectBusinessCounter
from app.modules.requirements.models import Requirement


def _project(project_id: int, code: str) -> Project:
    return Project(
        id=project_id,
        name=code,
        code=code,
        status="ACTIVE",
        owner_id="admin",
    )


def _requirement(project_id: int, code: str, title: str) -> Requirement:
    return Requirement(
        project_id=project_id,
        code=code,
        title=title,
        type="FEATURE",
        order_index=0,
        status="ACTIVE",
        created_by="admin",
    )


def test_business_codes_are_project_scoped_monotonic_and_seed_legacy_rows() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[
            Project.__table__,
            ProjectBusinessCounter.__table__,
            Requirement.__table__,
        ],
    )
    with Session(engine) as session:
        session.add_all(
            [_project(101, "PROJECT_A"), _project(202, "PROJECT_B"), _project(303, "LEGACY")]
        )
        session.add(_requirement(303, "REQ-0042", "历史需求"))
        session.commit()

        first_a = next_project_business_code(
            session,
            project_id=101,
            namespace=BusinessCodeNamespace.REQUIREMENT,
            model=Requirement,
        )
        session.add(_requirement(101, first_a, "A-1"))
        second_a = next_project_business_code(
            session,
            project_id=101,
            namespace=BusinessCodeNamespace.REQUIREMENT,
            model=Requirement,
        )
        session.add(_requirement(101, second_a, "A-2"))
        first_b = next_project_business_code(
            session,
            project_id=202,
            namespace=BusinessCodeNamespace.REQUIREMENT,
            model=Requirement,
        )
        legacy_next = next_project_business_code(
            session,
            project_id=303,
            namespace=BusinessCodeNamespace.REQUIREMENT,
            model=Requirement,
        )
        session.commit()

        assert (first_a, second_a, first_b, legacy_next) == (
            "REQ-0001",
            "REQ-0002",
            "REQ-0001",
            "REQ-0043",
        )

        session.delete(
            session.query(Requirement)
            .filter_by(project_id=101, code="REQ-0002")
            .one()
        )
        session.flush()
        after_delete = next_project_business_code(
            session,
            project_id=101,
            namespace=BusinessCodeNamespace.REQUIREMENT,
            model=Requirement,
        )
        assert after_delete == "REQ-0003"
