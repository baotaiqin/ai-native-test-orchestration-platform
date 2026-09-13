from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now_naive
from app.infrastructure.db.base import Base


class WebHealingProposal(Base):
    """A bounded, reviewable locator repair proposal.

    The source is pinned to the failed Run's immutable Web Case Version.  The
    draft key is cleared on every terminal review so rejected proposals can be
    regenerated without losing the audit trail.
    """

    __tablename__ = "locator_healing_proposals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','ACCEPTED','REJECTED')",
            name="locator_healing_proposals_status_values",
        ),
        CheckConstraint(
            "analysis_stage IN ('LLM_DOM','VISION_SCREENSHOT','AGENT_LOCATE')",
            name="locator_healing_proposals_analysis_stage_values",
        ),
        CheckConstraint(
            "source_snapshot_size >= 0 AND source_snapshot_size <= 1000000",
            name="locator_healing_proposals_snapshot_size_bounds",
        ),
        CheckConstraint(
            "length(source_snapshot_sha256) = 64",
            name="locator_healing_proposals_snapshot_sha256_length",
        ),
        CheckConstraint(
            "draft_key IS NULL OR draft_key = 'DRAFT'",
            name="locator_healing_proposals_draft_key_values",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="locator_healing_proposals_confidence_bounds",
        ),
        CheckConstraint(
            "evidence_candidate_index >= 0 AND evidence_candidate_index < 40",
            name="locator_healing_proposals_candidate_index_bounds",
        ),
        UniqueConstraint("ai_call_id", name="uq_locator_healing_proposals_ai_call"),
        UniqueConstraint(
            "run_id",
            "case_run_id",
            "node_id",
            "draft_key",
            name="uq_locator_healing_proposals_run_case_node_draft",
        ),
        Index(
            "ix_locator_healing_proposals_run_case_node",
            "run_id",
            "case_run_id",
            "node_id",
            "created_at",
        ),
        Index(
            "ix_locator_healing_proposals_project_created",
            "project_id",
            "created_at",
        ),
        Index(
            "ix_locator_healing_proposals_status_created",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    case_run_id: Mapped[int] = mapped_column(
        ForeignKey("case_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    web_case_id: Mapped[int] = mapped_column(
        ForeignKey("web_cases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    web_case_version_id: Mapped[int] = mapped_column(
        ForeignKey("web_case_versions.id", ondelete="RESTRICT"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ai_call_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_call_logs.id", ondelete="RESTRICT"), nullable=True
    )
    analysis_stage: Mapped[str] = mapped_column(
        String(24), nullable=False, default="LLM_DOM", server_default="LLM_DOM"
    )
    screenshot_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    draft_key: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_size: Mapped[int] = mapped_column(Integer, nullable=False)
    additional_instructions: Mapped[str | None] = mapped_column(String(5000), nullable=True)
    structured_result: Mapped[dict] = mapped_column(JSON, nullable=False)
    old_locator: Mapped[dict] = mapped_column(JSON, nullable=False)
    proposed_locator: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    human_locator: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    evidence_candidate_index: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_element_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("web_element_versions.id", ondelete="RESTRICT"), nullable=True
    )
    created_web_case_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("web_case_versions.id", ondelete="RESTRICT"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# The descriptive alias is useful to callers while the table-oriented name
# remains stable for migrations and database inspection.
LocatorHealingProposal = WebHealingProposal


class WebHealingValidation(Base):
    """An immutable temporary candidate validation backed by a normal Web Run."""

    __tablename__ = "locator_healing_validations"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_locator_healing_validations_run"),
        Index(
            "ix_locator_healing_validations_proposal_created",
            "proposal_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("locator_healing_proposals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    locator: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
