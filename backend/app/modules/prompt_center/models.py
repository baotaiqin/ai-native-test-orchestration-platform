from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class OutputSchema(Base):
    __tablename__ = "output_schemas"
    __table_args__ = (
        UniqueConstraint("name", "version_no", name="uq_output_schemas_name_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    schema_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class PromptDefinition(Base):
    __tablename__ = "prompt_definitions"
    __table_args__ = (
        CheckConstraint(
            "scope IN ('SYSTEM','PROJECT')",
            name="prompt_definitions_scope_values",
        ),
        UniqueConstraint(
            "project_id",
            "base_prompt_id",
            name="uq_prompt_definitions_project_base",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(
        String(16), nullable=False, default="SYSTEM", server_default="SYSTEM"
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    base_prompt_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_definitions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    is_builtin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    current_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("prompt_id", "version_no", name="uq_prompt_versions_prompt_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prompt_id: Mapped[int] = mapped_column(
        ForeignKey("prompt_definitions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_template: Mapped[str] = mapped_column(Text, nullable=False)
    output_schema_id: Mapped[int | None] = mapped_column(
        ForeignKey("output_schemas.id", ondelete="SET NULL"), nullable=True
    )
    change_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class AiCallLog(Base):
    __tablename__ = "ai_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_config_id: Mapped[int] = mapped_column(
        ForeignKey("model_configurations.id", ondelete="RESTRICT"), nullable=False
    )
    actual_model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version_id: Mapped[int] = mapped_column(
        ForeignKey("prompt_versions.id", ondelete="RESTRICT"), nullable=False
    )
    output_schema_id: Mapped[int | None] = mapped_column(
        ForeignKey("output_schemas.id", ondelete="SET NULL"), nullable=True
    )
    input_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost: Mapped[Decimal] = mapped_column(
        Numeric(16, 8), nullable=False, default=0
    )
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    repair_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_response: Mapped[str] = mapped_column(Text, nullable=False)
    repair_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_result: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    validation_errors: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
