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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base


class ModelProviderConnection(Base):
    __tablename__ = "model_provider_connections"
    __table_args__ = (
        UniqueConstraint("name", name="uq_model_provider_connections_name"),
        CheckConstraint(
            "access_type IN ('DIRECT','AGGREGATOR','SELF_HOSTED')",
            name="access_type_values",
        ),
        CheckConstraint(
            "protocol_type = 'OPENAI_COMPATIBLE'",
            name="protocol_type_values",
        ),
        CheckConstraint(
            "api_key_fingerprint IS NULL OR length(api_key_fingerprint) = 64",
            name="api_key_fingerprint_length",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    access_type: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    protocol_type: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    api_key_encrypted_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    api_key_rotated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key_encrypted_value)

    @property
    def api_key_masked(self) -> str | None:
        return "••••••••" if self.has_api_key else None


class ModelConfiguration(Base):
    __tablename__ = "model_configurations"
    __table_args__ = (
        CheckConstraint(
            "api_key_fingerprint IS NULL OR length(api_key_fingerprint) = 64",
            name="model_configurations_api_key_fingerprint_length",
        ),
        CheckConstraint(
            "max_input_tokens IS NULL OR max_input_tokens >= 0",
            name="max_input_tokens_nonnegative",
        ),
        CheckConstraint(
            "max_output_tokens IS NULL OR max_output_tokens >= 0",
            name="max_output_tokens_nonnegative",
        ),
        CheckConstraint(
            "max_reasoning_tokens IS NULL OR max_reasoning_tokens >= 0",
            name="max_reasoning_tokens_nonnegative",
        ),
        CheckConstraint(
            "reasoning_max_input_tokens IS NULL OR reasoning_max_input_tokens >= 0",
            name="reasoning_max_input_tokens_nonnegative",
        ),
        CheckConstraint(
            "reasoning_max_output_tokens IS NULL OR reasoning_max_output_tokens >= 0",
            name="reasoning_max_output_tokens_nonnegative",
        ),
        CheckConstraint(
            "model_category IS NULL OR model_category IN "
            "('LLM','VISION','OMNI','AUDIO','EMBEDDING','IMAGE_GENERATION',"
            "'VIDEO_GENERATION','THREE_D','OTHER')",
            name="model_category_values",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    # Deprecated 0035/0036 compatibility columns; runtime uses connection instead.
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Deprecated 0035/0036 compatibility relation; runtime no longer reads or writes it.
    api_key_secret_id: Mapped[int | None] = mapped_column(
        ForeignKey("secrets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Deprecated 0035/0036 compatibility credential columns; runtime uses connection.
    api_key_encrypted_value: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    api_key_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    api_key_rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("model_provider_connections.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    model_vendor: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_type: Mapped[str] = mapped_column(String(32), nullable=False)
    model_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    metadata_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    supports_reasoning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_max_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_modalities: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    output_modalities: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    capabilities: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    features: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    pricing_tiers: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    supports_tool_call: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supports_structured_output: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    max_context: Mapped[int | None] = mapped_column(Integer, nullable=True, default=128000)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    input_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=0
    )
    output_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=0
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    connection: Mapped[ModelProviderConnection] = relationship(
        ModelProviderConnection, lazy="joined"
    )


class ProjectModelBinding(Base):
    __tablename__ = "project_model_bindings"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "task_type", name="uq_project_model_bindings_project_task"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    primary_model_id: Mapped[int] = mapped_column(
        ForeignKey("model_configurations.id", ondelete="RESTRICT"), nullable=False
    )
    fallback_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_configurations.id", ondelete="SET NULL"), nullable=True
    )
    max_fallback: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
