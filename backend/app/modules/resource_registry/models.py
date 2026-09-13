from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class ResourceRegistryEntry(Base):
    __tablename__ = "resource_registry"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "run_id",
            "registration_sequence",
            name="uq_resource_registry_project_run_sequence",
        ),
        CheckConstraint(
            "status IN ('PENDING','CLEANING','CLEANED','FAILED','SKIPPED')",
            name="resource_registry_status_values",
        ),
        CheckConstraint(
            "cleanup_type IN ('API','SQL')",
            name="resource_registry_cleanup_type_values",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="resource_registry_attempt_count_nonnegative",
        ),
        CheckConstraint(
            "registration_sequence > 0",
            name="resource_registry_registration_sequence_positive",
        ),
        Index(
            "ix_resource_registry_project_run_sequence",
            "project_id",
            "run_id",
            "registration_sequence",
        ),
        Index("ix_resource_registry_project_status", "project_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(512), nullable=False)
    cleanup_type: Mapped[str] = mapped_column(String(16), nullable=False)
    cleanup_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", index=True)
    registration_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="RUNTIME")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    cleaned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
