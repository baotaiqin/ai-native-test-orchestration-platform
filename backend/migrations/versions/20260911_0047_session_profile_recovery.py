"""Add fixed login recovery bindings to Session Profiles."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_0047"
down_revision: str | None = "20260911_0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "session_profiles",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "session_profiles",
        sa.Column(
            "refresh_ttl_seconds", sa.Integer(), nullable=False, server_default="86400"
        ),
    )
    op.add_column(
        "session_profiles", sa.Column("login_web_case_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "session_profiles",
        sa.Column("login_web_case_version_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "session_profiles", sa.Column("expiry_condition", sa.JSON(), nullable=True)
    )
    op.add_column(
        "session_profiles", sa.Column("success_condition", sa.JSON(), nullable=True)
    )
    op.create_index(
        "ix_session_profiles_login_web_case_id",
        "session_profiles",
        ["login_web_case_id"],
    )
    op.create_index(
        "ix_session_profiles_login_web_case_version_id",
        "session_profiles",
        ["login_web_case_version_id"],
    )
    op.create_foreign_key(
        "fk_session_profiles_login_web_case_id_web_cases",
        "session_profiles",
        "web_cases",
        ["login_web_case_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_session_profiles_login_web_case_version_id_web_case_versions",
        "session_profiles",
        "web_case_versions",
        ["login_web_case_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_session_profiles_login_web_case_version_id_web_case_versions",
        "session_profiles",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_session_profiles_login_web_case_id_web_cases",
        "session_profiles",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_session_profiles_login_web_case_version_id", table_name="session_profiles"
    )
    op.drop_index("ix_session_profiles_login_web_case_id", table_name="session_profiles")
    op.drop_column("session_profiles", "success_condition")
    op.drop_column("session_profiles", "expiry_condition")
    op.drop_column("session_profiles", "login_web_case_version_id")
    op.drop_column("session_profiles", "login_web_case_id")
    op.drop_column("session_profiles", "refresh_ttl_seconds")
    op.drop_column("session_profiles", "revision")
