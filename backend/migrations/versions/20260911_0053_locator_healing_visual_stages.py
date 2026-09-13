"""Add explicit visual and final-agent locator healing stages."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_0053"
down_revision: str | None = "20260911_0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "locator_healing_proposals"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "analysis_stage",
            sa.String(length=24),
            nullable=False,
            server_default="LLM_DOM",
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column("screenshot_artifact_id", sa.String(length=128), nullable=True),
    )
    op.create_check_constraint(
        op.f(f"ck_{_TABLE}_{_TABLE}_analysis_stage_values"),
        _TABLE,
        "analysis_stage IN ('LLM_DOM','VISION_SCREENSHOT','AGENT_LOCATE')",
    )
    op.create_foreign_key(
        op.f(f"fk_{_TABLE}_screenshot_artifact_id_artifacts"),
        _TABLE,
        "artifacts",
        ["screenshot_artifact_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        f"ix_{_TABLE}_screenshot_artifact_id",
        _TABLE,
        ["screenshot_artifact_id"],
    )


def downgrade() -> None:
    op.drop_index(f"ix_{_TABLE}_screenshot_artifact_id", table_name=_TABLE)
    op.drop_constraint(
        op.f(f"fk_{_TABLE}_screenshot_artifact_id_artifacts"),
        _TABLE,
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f(f"ck_{_TABLE}_{_TABLE}_analysis_stage_values"),
        _TABLE,
        type_="check",
    )
    op.drop_column(_TABLE, "screenshot_artifact_id")
    op.drop_column(_TABLE, "analysis_stage")
