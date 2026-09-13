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
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class TestCase(Base):
    __tablename__ = "test_cases"
    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_test_cases_project_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    api_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_definitions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    case_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    current_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TestCaseVersion(Base):
    __tablename__ = "test_case_versions"
    __table_args__ = (
        UniqueConstraint("case_id", "version_no", name="uq_test_case_versions_case_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    change_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class AiCaseDesignTask(Base):
    __tablename__ = "ai_case_design_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",
            name="status_values",
        ),
        CheckConstraint(
            "source IS NULL OR source IN ('AI','RULE_FALLBACK')",
            name="source_values",
        ),
        Index("ix_ai_case_design_tasks_requirement_created", "requirement_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requirement_version_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_versions.id", ondelete="RESTRICT"), nullable=False
    )
    prompt_id: Mapped[int] = mapped_column(
        ForeignKey("prompt_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    ai_call_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_call_logs.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED")
    source: Mapped[str | None] = mapped_column(String(24), nullable=True)
    api_contract_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    included_api_definition_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AiCaseGeneration(Base):
    __tablename__ = "ai_case_generations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requirement_version_id: Mapped[int] = mapped_column(
        ForeignKey("requirement_versions.id", ondelete="RESTRICT"), nullable=False
    )
    ai_call_id: Mapped[int] = mapped_column(
        ForeignKey("ai_call_logs.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    additional_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_api_definition_ids: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    coverage_plan: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    raw_response: Mapped[str] = mapped_column(Text, nullable=False)
    structured_result: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class AiCaseGenerationTask(Base):
    __tablename__ = "ai_case_generation_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",
            name="status_values",
        ),
        Index("ix_ai_case_generation_tasks_requirement_created", "requirement_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", name="fk_ai_case_gen_tasks_project", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey(
            "requirements.id", name="fk_ai_case_gen_tasks_requirement", ondelete="CASCADE"
        ),
        nullable=False,
        index=True,
    )
    requirement_version_id: Mapped[int] = mapped_column(
        ForeignKey(
            "requirement_versions.id",
            name="fk_ai_case_gen_tasks_req_version",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    prompt_id: Mapped[int] = mapped_column(
        ForeignKey(
            "prompt_definitions.id", name="fk_ai_case_gen_tasks_prompt", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    generation_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "ai_case_generations.id",
            name="fk_ai_case_gen_tasks_generation",
            ondelete="SET NULL",
        ),
        nullable=True,
        unique=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED")
    additional_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_api_definition_ids: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    coverage_plan: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AiCaseSuggestion(Base):
    __tablename__ = "ai_case_suggestions"
    __table_args__ = (
        UniqueConstraint(
            "generation_id", "sequence_no", name="uq_ai_case_suggestions_generation_sequence"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    generation_id: Mapped[int] = mapped_column(
        ForeignKey("ai_case_generations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    structured_result: Mapped[dict] = mapped_column(JSON, nullable=False)
    human_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    linked_requirement_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    test_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_cases.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RequirementCaseLink(Base):
    __tablename__ = "requirement_case_links"
    __table_args__ = (
        CheckConstraint(
            "asset_type IN ('TEST_CASE','WEB_CASE')",
            name="asset_type_values",
        ),
        CheckConstraint(
            "(asset_type = 'TEST_CASE' AND case_id IS NOT NULL "
            "AND web_case_id IS NULL AND web_case_version_id IS NULL "
            "AND case_type IS NOT NULL) OR "
            "(asset_type = 'WEB_CASE' AND case_id IS NULL "
            "AND case_version_id IS NULL AND web_case_id IS NOT NULL "
            "AND case_type IS NULL)",
            name="target_shape",
        ),
        CheckConstraint(
            "(status = 'ACTIVE' AND active_slot IS NOT NULL AND active_slot = 1 "
            "AND removed_by IS NULL AND removed_at IS NULL) OR "
            "(status = 'REMOVED' AND active_slot IS NULL "
            "AND removed_by IS NOT NULL AND removed_at IS NOT NULL)",
            name="status_active_slot",
        ),
        CheckConstraint(
            "created_at_time_basis IN ('UTC','LEGACY_UNKNOWN')",
            name="created_time_basis",
        ),
        UniqueConstraint(
            "requirement_id",
            "case_id",
            "relation_type",
            "active_slot",
            name="uq_requirement_case_links_active_test_case",
        ),
        UniqueConstraint(
            "requirement_id",
            "web_case_id",
            "relation_type",
            "active_slot",
            name="uq_requirement_case_links_active_web_case",
        ),
        Index("ix_requirement_case_links_requirement_status_id", "requirement_id", "status", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requirement_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirement_versions.id", ondelete="SET NULL"), nullable=True
    )
    asset_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="TEST_CASE", server_default="TEST_CASE"
    )
    case_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    case_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=True, index=True
    )
    case_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_case_versions.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    web_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("web_cases.id", ondelete="CASCADE"), nullable=True, index=True
    )
    web_case_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("web_case_versions.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ACTIVE", server_default="ACTIVE"
    )
    active_slot: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=1, server_default="1"
    )
    supersedes_link_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "requirement_case_links.id",
            name="fk_req_case_links_supersedes",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at_time_basis: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="LEGACY_UNKNOWN",
        server_default="LEGACY_UNKNOWN",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    removed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
