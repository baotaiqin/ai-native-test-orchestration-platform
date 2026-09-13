"""Persist the provider catalog primary model category."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_0038"
down_revision: str | None = "20260909_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "model_configurations"
_COLUMN = "model_category"
_CHECK = "ck_model_configurations_model_category_values"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=32), nullable=True))
    op.create_check_constraint(
        _CHECK,
        _TABLE,
        "model_category IS NULL OR model_category IN "
        "('LLM','VISION','OMNI','AUDIO','EMBEDDING','IMAGE_GENERATION',"
        "'VIDEO_GENERATION','THREE_D','OTHER')",
    )


def downgrade() -> None:
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.drop_column(_TABLE, _COLUMN)
