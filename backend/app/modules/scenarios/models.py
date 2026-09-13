from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class Scenario(Base):
    __tablename__ = "scenarios"
    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_scenarios_project_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    current_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ScenarioVersion(Base):
    __tablename__ = "scenario_versions"
    __table_args__ = (
        UniqueConstraint(
            "scenario_id", "version_no", name="uq_scenario_versions_scenario_version"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scenario_id: Mapped[int] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    dsl: Mapped[dict] = mapped_column(JSON, nullable=False)
    change_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class ScenarioPreviewProfile(Base):
    """Saved offline Preview fixtures for one immutable Scenario version."""

    __tablename__ = "scenario_preview_profiles"
    __table_args__ = (
        CheckConstraint(
            "cleanup_outcome IN ('SUCCESS','FAILURE','CANCELLED','TIMEOUT')",
            name="scenario_preview_profiles_cleanup_outcome",
        ),
        UniqueConstraint(
            "scenario_version_id", name="uq_scenario_preview_profiles_version"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scenario_version_id: Mapped[int] = mapped_column(
        ForeignKey("scenario_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    responses_by_node: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    cleanup_outcome: Mapped[str] = mapped_column(
        String(16), nullable=False, default="SUCCESS"
    )
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
