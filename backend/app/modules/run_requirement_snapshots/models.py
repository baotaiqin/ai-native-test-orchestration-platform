from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import utc_now_naive
from app.infrastructure.db.base import Base


class RunRequirementCapture(Base):
    """One immutable capture header for one CaseRun."""

    __tablename__ = "run_requirement_captures"
    __table_args__ = (
        UniqueConstraint(
            "case_run_id", name="uq_run_req_captures_case_run"
        ),
        CheckConstraint(
            "capture_status IN ('CAPTURED','CAPTURED_EMPTY','UNSUPPORTED_TARGET')",
            name="capture_status_values",
        ),
        CheckConstraint(
            "target_type IN ('API_CASE','WEB_CASE','SCENARIO')",
            name="target_type_values",
        ),
        CheckConstraint(
            "captured_at_time_basis = 'UTC'",
            name="captured_time_basis_utc",
        ),
        CheckConstraint(
            "consistency_basis = 'CREATE_RUN_TRANSACTION'",
            name="consistency_basis_value",
        ),
        CheckConstraint(
            "target_asset_id > 0 AND target_version_id > 0",
            name="target_ids_positive",
        ),
        CheckConstraint(
            "item_count >= 0 AND item_count <= 1000",
            name="item_count_bounds",
        ),
        CheckConstraint(
            "(capture_status = 'CAPTURED' AND item_count > 0) OR "
            "(capture_status IN ('CAPTURED_EMPTY','UNSUPPORTED_TARGET') "
            "AND item_count = 0)",
            name="status_count_consistent",
        ),
        CheckConstraint(
            "(target_type = 'SCENARIO' AND capture_status = 'UNSUPPORTED_TARGET') OR "
            "(target_type IN ('API_CASE','WEB_CASE') "
            "AND capture_status IN ('CAPTURED','CAPTURED_EMPTY'))",
            name="target_status_consistent",
        ),
        Index("ix_run_req_captures_target", "target_type", "target_asset_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_run_id: Mapped[int] = mapped_column(
        ForeignKey(
            "case_runs.id",
            name="fk_run_req_captures_case_run",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    capture_status: Mapped[str] = mapped_column(String(32), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive
    )
    captured_at_time_basis: Mapped[str] = mapped_column(
        String(16), nullable=False, default="UTC", server_default="UTC"
    )
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_asset_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    item_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    consistency_basis: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="CREATE_RUN_TRANSACTION",
        server_default="CREATE_RUN_TRANSACTION",
    )

    sources: Mapped[list["RunRequirementSource"]] = relationship(
        back_populates="capture",
        cascade="all, delete-orphan",
        order_by="RunRequirementSource.sequence_no",
    )


class RunRequirementSource(Base):
    """Denormalized immutable public-safe requirement association evidence."""

    __tablename__ = "run_requirement_sources"
    __table_args__ = (
        UniqueConstraint(
            "capture_id", "sequence_no", name="uq_run_req_sources_capture_seq"
        ),
        UniqueConstraint(
            "capture_id", "original_link_id", name="uq_run_req_sources_capture_link"
        ),
        CheckConstraint(
            "sequence_no > 0 AND sequence_no <= 1000",
            name="sequence_bounds",
        ),
        CheckConstraint(
            "target_type IN ('API_CASE','WEB_CASE')",
            name="target_type_values",
        ),
        CheckConstraint(
            "(target_type = 'API_CASE' AND link_asset_type = 'TEST_CASE') OR "
            "(target_type = 'WEB_CASE' AND link_asset_type = 'WEB_CASE')",
            name="asset_type_consistent",
        ),
        CheckConstraint(
            "target_asset_id > 0 AND target_version_id > 0 "
            "AND original_link_id > 0",
            name="ids_positive",
        ),
        CheckConstraint(
            "supersedes_link_id IS NULL OR supersedes_link_id > 0",
            name="supersedes_positive",
        ),
        CheckConstraint(
            "requirement_id > 0",
            name="requirement_id_positive",
        ),
        CheckConstraint(
            "requirement_version_binding IN "
            "('EXACT_REQUIREMENT_VERSION','REQUIREMENT_VERSION_UNKNOWN')",
            name="requirement_binding_values",
        ),
        CheckConstraint(
            "(requirement_version_binding = 'EXACT_REQUIREMENT_VERSION' "
            "AND requirement_version_id IS NOT NULL "
            "AND requirement_version_no IS NOT NULL "
            "AND requirement_content_hash IS NOT NULL "
            "AND requirement_source_type IS NOT NULL) OR "
            "(requirement_version_binding = 'REQUIREMENT_VERSION_UNKNOWN' "
            "AND requirement_version_id IS NULL "
            "AND requirement_version_no IS NULL "
            "AND requirement_content_hash IS NULL "
            "AND requirement_source_type IS NULL)",
            name="requirement_binding_shape",
        ),
        CheckConstraint(
            "asset_version_binding IN "
            "('EXACT_EXECUTION_VERSION','ASSET_VERSION_UNKNOWN')",
            name="asset_binding_values",
        ),
        CheckConstraint(
            "(asset_version_binding = 'EXACT_EXECUTION_VERSION' "
            "AND link_asset_version_id IS NOT NULL "
            "AND link_asset_version_id = target_version_id) OR "
            "(asset_version_binding = 'ASSET_VERSION_UNKNOWN' "
            "AND link_asset_version_id IS NULL)",
            name="asset_binding_shape",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="confidence_bounds",
        ),
        CheckConstraint(
            "link_created_at_time_basis IN ('UTC','LEGACY_UNKNOWN')",
            name="link_time_basis_values",
        ),
        CheckConstraint(
            "captured_at_time_basis = 'UTC'",
            name="captured_time_basis_utc",
        ),
        Index("ix_run_req_sources_requirement", "requirement_id"),
        Index("ix_run_req_sources_capture_order", "capture_id", "sequence_no", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    capture_id: Mapped[int] = mapped_column(
        ForeignKey(
            "run_requirement_captures.id",
            name="fk_run_req_sources_capture",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    original_link_id: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_link_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    requirement_id: Mapped[int] = mapped_column(Integer, nullable=False)
    requirement_code: Mapped[str] = mapped_column(String(64), nullable=False)
    requirement_title: Mapped[str] = mapped_column(String(255), nullable=False)
    requirement_type: Mapped[str] = mapped_column(String(32), nullable=False)
    requirement_status: Mapped[str] = mapped_column(String(32), nullable=False)
    requirement_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requirement_version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requirement_content_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    requirement_source_type: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    requirement_version_binding: Mapped[str] = mapped_column(String(40), nullable=False)

    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    link_asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_asset_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    link_asset_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    asset_version_binding: Mapped[str] = mapped_column(String(40), nullable=False)

    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    link_created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    link_created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    link_created_at_time_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    captured_at_time_basis: Mapped[str] = mapped_column(
        String(16), nullable=False, default="UTC", server_default="UTC"
    )

    capture: Mapped[RunRequirementCapture] = relationship(back_populates="sources")
