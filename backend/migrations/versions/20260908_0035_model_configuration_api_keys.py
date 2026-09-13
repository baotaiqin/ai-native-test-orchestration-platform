"""Add platform-level encrypted API keys to model configurations.

The legacy ``api_key_secret_id`` column is intentionally retained for rollback
and data compatibility. Runtime code no longer reads it after this migration.
Existing legacy ciphertext is copied to the new columns during upgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_0035"
down_revision: str | None = "20260908_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "model_configurations"
_FINGERPRINT_CHECK = "ck_model_configurations_api_key_fingerprint_length"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("api_key_encrypted_value", sa.Text(), nullable=True),
    )
    op.add_column(
        _TABLE,
        sa.Column("api_key_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        _TABLE,
        sa.Column("api_key_rotated_at", sa.DateTime(), nullable=True),
    )
    op.create_check_constraint(
        _FINGERPRINT_CHECK,
        _TABLE,
        "api_key_fingerprint IS NULL OR length(api_key_fingerprint) = 64",
    )
    op.execute(
        sa.text(
            "UPDATE model_configurations AS model "
            "JOIN secrets AS secret "
            "ON secret.id = model.api_key_secret_id "
            "SET model.api_key_encrypted_value = secret.encrypted_value, "
            "model.api_key_fingerprint = secret.fingerprint, "
            "model.api_key_rotated_at = secret.rotated_at "
            "WHERE model.api_key_secret_id IS NOT NULL "
            "AND model.api_key_encrypted_value IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_constraint(_FINGERPRINT_CHECK, _TABLE, type_="check")
    op.drop_column(_TABLE, "api_key_rotated_at")
    op.drop_column(_TABLE, "api_key_fingerprint")
    op.drop_column(_TABLE, "api_key_encrypted_value")
