from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0076"
down_revision: str | None = "20260913_0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_exploration_evidence",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "exploration_id",
            sa.String(128),
            sa.ForeignKey("web_explorations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("mime", sa.String(32), nullable=False, server_default="image/png"),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("minio_bucket", sa.String(63), nullable=False),
        sa.Column("minio_key", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "sequence >= 1 AND sequence <= 50",
            name="ck_web_exploration_evidence_sequence",
        ),
        sa.CheckConstraint(
            "kind IN ('CHECKPOINT','FINAL','FAILURE')",
            name="ck_web_exploration_evidence_kind",
        ),
        sa.CheckConstraint(
            "size > 0 AND size <= 5000000",
            name="ck_web_exploration_evidence_size",
        ),
        sa.CheckConstraint(
            "length(sha256) = 64",
            name="ck_web_exploration_evidence_sha256_length",
        ),
        sa.UniqueConstraint(
            "exploration_id", "file_name", name="uq_web_exploration_evidence_file"
        ),
        sa.UniqueConstraint(
            "minio_bucket", "minio_key", name="uq_web_exploration_evidence_object"
        ),
    )
    op.create_index(
        "ix_web_exploration_evidence_project_id",
        "web_exploration_evidence",
        ["project_id"],
    )
    op.create_index(
        "ix_web_exploration_evidence_exploration_id",
        "web_exploration_evidence",
        ["exploration_id"],
    )
    op.create_index(
        "ix_web_exploration_evidence_sequence",
        "web_exploration_evidence",
        ["exploration_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_web_exploration_evidence_sequence",
        table_name="web_exploration_evidence",
    )
    op.drop_index(
        "ix_web_exploration_evidence_exploration_id",
        table_name="web_exploration_evidence",
    )
    op.drop_index(
        "ix_web_exploration_evidence_project_id",
        table_name="web_exploration_evidence",
    )
    op.drop_table("web_exploration_evidence")
