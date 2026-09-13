"""Split platform model access channels from model metadata.

The pre-0036 model columns remain as deprecated compatibility columns. Upgrade
creates one deterministic channel per existing model and copies the old
encrypted credential state. Downgrade copies the channel values back before
removing the new relation and channel table.
"""

import ipaddress
from collections.abc import Sequence
from urllib.parse import urlsplit

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_0036"
down_revision: str | None = "20260908_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MODEL_TABLE = "model_configurations"
_CONNECTION_TABLE = "model_provider_connections"
_MODEL_CONNECTION_FK = "fk_model_configurations_connection_id_model_provider_connections"
_MODEL_CONNECTION_INDEX = "ix_model_configurations_connection_id"


def _legacy_access_type(provider: str | None, base_url: str | None) -> str:
    provider_name = (provider or "").strip().upper()
    if provider_name in {"OPENROUTER", "AGGREGATOR"}:
        return "AGGREGATOR"
    try:
        hostname = urlsplit(base_url or "").hostname or ""
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if hostname.lower() in {"localhost", "localhost.localdomain"} or (
        address is not None and (address.is_private or address.is_loopback or address.is_link_local)
    ):
        return "SELF_HOSTED"
    return "DIRECT"


def _legacy_model_vendor(provider: str | None, model_name: str) -> str:
    model_value = (model_name or "").strip().upper()
    if "DEEPSEEK" in model_value:
        return "DEEPSEEK"
    if any(token in model_value for token in ("QWEN", "DASHSCOPE", "BAILIAN", "ALIBABA")):
        return "ALIBABA_QWEN"
    if "OPENAI" in model_value or "GPT-" in model_value or model_value.startswith("GPT"):
        return "OPENAI"

    provider_value = (provider or "").strip().upper()
    if provider_value == "OPENAI_COMPATIBLE":
        return "OTHER"
    if "DEEPSEEK" in provider_value:
        return "DEEPSEEK"
    if any(
        token in provider_value for token in ("QWEN", "DASHSCOPE", "BAILIAN", "ALIBABA")
    ):
        return "ALIBABA_QWEN"
    if "OPENAI" in provider_value:
        return "OPENAI"
    return "OTHER"


def _legacy_rows(bind) -> list[dict[str, object]]:
    statement = sa.text(
        "SELECT model.id, model.name, model.provider, model.base_url, "
        "model.model_name, model.created_by, model.api_key_encrypted_value, "
        "model.api_key_fingerprint, model.api_key_rotated_at, "
        "secret.encrypted_value AS legacy_encrypted_value, "
        "secret.fingerprint AS legacy_fingerprint, "
        "secret.rotated_at AS legacy_rotated_at "
        "FROM model_configurations AS model "
        "LEFT JOIN secrets AS secret ON secret.id = model.api_key_secret_id"
    )
    return list(bind.execute(statement).mappings())


def _backfill_connections() -> None:
    bind = op.get_bind()
    rows = _legacy_rows(bind)
    metadata = sa.MetaData()
    connection_table = sa.Table(
        _CONNECTION_TABLE,
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=128)),
        sa.Column("access_type", sa.String(length=16)),
        sa.Column("provider", sa.String(length=64)),
        sa.Column("protocol_type", sa.String(length=32)),
        sa.Column("base_url", sa.String(length=500)),
        sa.Column("api_key_encrypted_value", sa.Text()),
        sa.Column("api_key_fingerprint", sa.String(length=64)),
        sa.Column("api_key_rotated_at", sa.DateTime()),
        sa.Column("enabled", sa.Boolean()),
        sa.Column("created_by", sa.String(length=64)),
    )
    model_table = sa.Table(
        _MODEL_TABLE,
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("connection_id", sa.Integer()),
        sa.Column("model_vendor", sa.String(length=64)),
    )
    for row in rows:
        encrypted = row["api_key_encrypted_value"] or row["legacy_encrypted_value"]
        fingerprint = row["api_key_fingerprint"] or row["legacy_fingerprint"]
        rotated_at = row["api_key_rotated_at"] or row["legacy_rotated_at"]
        provider = str(row["provider"] or "CUSTOM")
        base_url = str(row["base_url"] or "")
        result = bind.execute(
            sa.insert(connection_table).values(
                name=f"legacy-model-{row['id']}",
                access_type=_legacy_access_type(row["provider"], row["base_url"]),
                provider=provider,
                protocol_type="OPENAI_COMPATIBLE",
                base_url=base_url,
                api_key_encrypted_value=encrypted,
                api_key_fingerprint=fingerprint,
                api_key_rotated_at=rotated_at,
                enabled=True,
                created_by=str(row["created_by"]),
            )
        )
        try:
            connection_id = int(result.inserted_primary_key[0])
        except (IndexError, TypeError, ValueError) as exc:
            raise RuntimeError(
                "model provider connection backfill 未返回有效主键"
            ) from exc
        if connection_id <= 0:
            raise RuntimeError("model provider connection backfill 返回了无效主键")
        bind.execute(
            sa.update(model_table)
            .where(model_table.c.id == row["id"])
            .values(
                connection_id=connection_id,
                model_vendor=_legacy_model_vendor(
                    row["provider"], str(row["model_name"])
                ),
            )
        )


def upgrade() -> None:
    op.create_table(
        _CONNECTION_TABLE,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("access_type", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("protocol_type", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("api_key_encrypted_value", sa.Text(), nullable=True),
        sa.Column("api_key_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("api_key_rotated_at", sa.DateTime(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "access_type IN ('DIRECT','AGGREGATOR','SELF_HOSTED')",
            name=op.f("ck_model_provider_connections_access_type_values"),
        ),
        sa.CheckConstraint(
            "protocol_type = 'OPENAI_COMPATIBLE'",
            name=op.f("ck_model_provider_connections_protocol_type_values"),
        ),
        sa.CheckConstraint(
            "api_key_fingerprint IS NULL OR length(api_key_fingerprint) = 64",
            name=op.f("ck_model_provider_connections_api_key_fingerprint_length"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_provider_connections")),
        sa.UniqueConstraint("name", name="uq_model_provider_connections_name"),
    )
    op.add_column(
        _MODEL_TABLE,
        sa.Column("connection_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        _MODEL_TABLE,
        sa.Column("model_vendor", sa.String(length=64), nullable=False, server_default="OTHER"),
    )
    op.alter_column(
        _MODEL_TABLE, "provider", existing_type=sa.String(length=64), nullable=True
    )
    op.alter_column(
        _MODEL_TABLE, "base_url", existing_type=sa.String(length=500), nullable=True
    )
    _backfill_connections()
    op.alter_column(
        _MODEL_TABLE, "connection_id", existing_type=sa.Integer(), nullable=False
    )
    op.alter_column(
        _MODEL_TABLE, "model_vendor", existing_type=sa.String(length=64), server_default=None
    )
    op.create_foreign_key(
        _MODEL_CONNECTION_FK,
        _MODEL_TABLE,
        _CONNECTION_TABLE,
        ["connection_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(_MODEL_CONNECTION_INDEX, _MODEL_TABLE, ["connection_id"])


def _restore_legacy_model_values() -> None:
    bind = op.get_bind()
    model_table = sa.table(
        _MODEL_TABLE,
        sa.column("id", sa.Integer()),
        sa.column("connection_id", sa.Integer()),
        sa.column("provider", sa.String(length=64)),
        sa.column("base_url", sa.String(length=500)),
        sa.column("api_key_encrypted_value", sa.Text()),
        sa.column("api_key_fingerprint", sa.String(length=64)),
        sa.column("api_key_rotated_at", sa.DateTime()),
    )
    connection_table = sa.table(
        _CONNECTION_TABLE,
        sa.column("id", sa.Integer()),
        sa.column("provider", sa.String(length=64)),
        sa.column("base_url", sa.String(length=500)),
        sa.column("api_key_encrypted_value", sa.Text()),
        sa.column("api_key_fingerprint", sa.String(length=64)),
        sa.column("api_key_rotated_at", sa.DateTime()),
    )
    rows = bind.execute(
        sa.select(
            model_table.c.id,
            connection_table.c.provider,
            connection_table.c.base_url,
            connection_table.c.api_key_encrypted_value,
            connection_table.c.api_key_fingerprint,
            connection_table.c.api_key_rotated_at,
        ).select_from(
            model_table.join(
                connection_table,
                connection_table.c.id == model_table.c.connection_id,
            )
        )
    ).mappings()
    for row in rows:
        bind.execute(
            sa.update(model_table)
            .where(model_table.c.id == row["id"])
            .values(
                provider=row["provider"],
                base_url=row["base_url"],
                api_key_encrypted_value=row["api_key_encrypted_value"],
                api_key_fingerprint=row["api_key_fingerprint"],
                api_key_rotated_at=row["api_key_rotated_at"],
            )
        )


def downgrade() -> None:
    _restore_legacy_model_values()
    op.drop_constraint(_MODEL_CONNECTION_FK, _MODEL_TABLE, type_="foreignkey")
    op.drop_index(_MODEL_CONNECTION_INDEX, table_name=_MODEL_TABLE)
    op.drop_column(_MODEL_TABLE, "model_vendor")
    op.drop_column(_MODEL_TABLE, "connection_id")
    op.alter_column(
        _MODEL_TABLE, "provider", existing_type=sa.String(length=64), nullable=False
    )
    op.alter_column(
        _MODEL_TABLE, "base_url", existing_type=sa.String(length=500), nullable=False
    )
    op.drop_table(_CONNECTION_TABLE)
