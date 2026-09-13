from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class Requirement(Base):
    __tablename__ = "requirements"
    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_requirements_project_id_code"),
        CheckConstraint(
            "verification_type IN "
            "('AUTO','API','WEB','PERFORMANCE','PLATFORM','MANUAL')",
            name="verification_type_values",
        ),
        CheckConstraint(
            "automation_readiness IN "
            "('READY','NEEDS_CLARIFICATION','MANUAL_ONLY')",
            name="automation_readiness_values",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), nullable=True, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False, default="FEATURE")
    verification_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="AUTO"
    )
    automation_readiness: Mapped[str] = mapped_column(
        String(32), nullable=False, default="READY"
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    current_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class RequirementVersion(Base):
    __tablename__ = "requirement_versions"
    __table_args__ = (
        UniqueConstraint(
            "requirement_id", "version_no", name="uq_requirement_versions_requirement_id_version_no"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    markdown_content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    change_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class RequirementDocumentVersion(Base):
    """A published snapshot of the complete requirement tree for one project.

    RequirementVersion remains the immutable technical revision used by existing
    links and execution snapshots. This project-level version is the user-facing
    V1/V2 history of the complete requirement document.
    """

    __tablename__ = "requirement_document_versions"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "version_no",
            name="uq_requirement_document_versions_project_id_version_no",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    change_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_review_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirement_reviews.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
