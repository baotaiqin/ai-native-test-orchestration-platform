from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class ApiDefinitionImport(Base):
    __tablename__ = "api_definition_imports"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "source_filename", "version_no",
            name="uq_api_imports_project_file_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    spec_version: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    imported_by: Mapped[str] = mapped_column(String(64), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class ApiDefinition(Base):
    __tablename__ = "api_definitions"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "method", "path", name="uq_api_definitions_project_method_path"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    import_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_definition_imports.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    operation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    parameters: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    request_schema: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response_schema: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    auth_info: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="SWAGGER")
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contract_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ApiScenarioPlan(Base):
    """One immutable requirement/OpenAPI scope analyzed into scenario candidates."""

    __tablename__ = "api_scenario_plans"
    __table_args__ = (
        CheckConstraint(
            "generation_status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",
            name="api_scenario_plans_generation_status_values",
        ),
        CheckConstraint(
            "active_key IS NULL OR active_key = 'ACTIVE'",
            name="api_scenario_plans_active_key_values",
        ),
        CheckConstraint(
            "source_snapshot_size >= 0 AND source_snapshot_size <= 1000000",
            name="api_scenario_plans_snapshot_size_bounds",
        ),
        CheckConstraint(
            "length(source_snapshot_sha256) = 64",
            name="api_scenario_plans_snapshot_sha256_length",
        ),
        UniqueConstraint("ai_call_id", name="uq_api_scenario_plans_ai_call"),
        UniqueConstraint("import_id", "active_key", name="uq_api_scenario_plans_active"),
        Index("ix_api_scenario_plans_import_created", "import_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    import_id: Mapped[int] = mapped_column(
        ForeignKey("api_definition_imports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    prompt_id: Mapped[int] = mapped_column(
        ForeignKey("prompt_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    ai_call_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_call_logs.id", ondelete="RESTRICT"), nullable=True
    )
    generation_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="QUEUED", server_default="QUEUED"
    )
    active_key: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_size: Mapped[int] = mapped_column(Integer, nullable=False)
    additional_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)


class ApiScenarioPlanItem(Base):
    """A selectable scenario outline produced by a scenario plan."""

    __tablename__ = "api_scenario_plan_items"
    __table_args__ = (
        UniqueConstraint("plan_id", "candidate_key", name="uq_api_plan_items_key"),
        Index("ix_api_plan_items_plan_id", "plan_id"),
        Index("ix_api_plan_items_plan_order", "plan_id", "order_index"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("api_scenario_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_key: Mapped[str] = mapped_column(String(64), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    priority: Mapped[str] = mapped_column(String(8), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    requirement_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    api_definition_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    api_flow: Mapped[list] = mapped_column(JSON, nullable=False)
    preconditions: Mapped[list] = mapped_column(JSON, nullable=False)
    expected_outcomes: Mapped[list] = mapped_column(JSON, nullable=False)
    cleanup_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class ApiDesignSuggestion(Base):
    """Reviewable AI output pinned to one immutable OpenAPI import."""

    __tablename__ = "api_design_suggestions"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('CASE_SET','SCENARIO')",
            name="api_design_suggestions_kind_values",
        ),
        CheckConstraint(
            "status IN ('DRAFT','ACCEPTED','REJECTED')",
            name="api_design_suggestions_status_values",
        ),
        CheckConstraint(
            "generation_status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",
            name="api_design_suggestions_generation_status_values",
        ),
        CheckConstraint(
            "draft_key IS NULL OR draft_key = 'DRAFT'",
            name="api_design_suggestions_draft_key_values",
        ),
        CheckConstraint(
            "source_snapshot_size >= 0 AND source_snapshot_size <= 1000000",
            name="api_design_suggestions_snapshot_size_bounds",
        ),
        CheckConstraint(
            "length(source_snapshot_sha256) = 64",
            name="api_design_suggestions_snapshot_sha256_length",
        ),
        UniqueConstraint("ai_call_id", name="uq_api_design_suggestions_ai_call"),
        UniqueConstraint(
            "import_id",
            "kind",
            "draft_key",
            name="uq_api_design_suggestions_import_kind_draft",
        ),
        Index(
            "ix_api_design_suggestions_import_created",
            "import_id",
            "created_at",
        ),
        Index(
            "ix_api_design_suggestions_project_status",
            "project_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    import_id: Mapped[int] = mapped_column(
        ForeignKey("api_definition_imports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    prompt_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_definitions.id", ondelete="RESTRICT"), nullable=True
    )
    plan_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_scenario_plan_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    ai_call_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_call_logs.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    generation_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="SUCCEEDED", server_default="SUCCEEDED"
    )
    draft_key: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_size: Mapped[int] = mapped_column(Integer, nullable=False)
    additional_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    human_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_test_case_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_scenario_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
