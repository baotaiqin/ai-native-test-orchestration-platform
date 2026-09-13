from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0078"
down_revision: str | None = "20260913_0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "requirements",
        sa.Column("verification_type", sa.String(32), nullable=False, server_default="AUTO"),
    )
    op.add_column(
        "requirements",
        sa.Column(
            "automation_readiness", sa.String(32), nullable=False, server_default="READY"
        ),
    )
    op.create_check_constraint(
        "verification_type_values",
        "requirements",
        "verification_type IN ('AUTO','API','WEB','PERFORMANCE','PLATFORM','MANUAL')",
    )
    op.create_check_constraint(
        "automation_readiness_values",
        "requirements",
        "automation_readiness IN ('READY','NEEDS_CLARIFICATION','MANUAL_ONLY')",
    )

    # The built-in requirement is intentionally cross-capability.  Persist its
    # routing so selecting the root does not feed Web/performance/platform-only
    # acceptance text into API test design.
    demo_types = {
        "REQ-0015": "PERFORMANCE",
        "REQ-0016": "PLATFORM",
        "REQ-0019": "WEB",
        "REQ-0020": "PLATFORM",
    }
    for code, verification_type in demo_types.items():
        op.execute(
            "UPDATE requirements "
            f"SET verification_type = '{verification_type}' "
            f"WHERE code = '{code}' AND project_id IN "
            "(SELECT id FROM projects WHERE code = 'AI_DEMO')"
        )
    op.execute(
        "UPDATE requirements SET automation_readiness = 'MANUAL_ONLY' "
        "WHERE code = 'REQ-0020' AND project_id IN "
        "(SELECT id FROM projects WHERE code = 'AI_DEMO')"
    )


def downgrade() -> None:
    op.drop_constraint("automation_readiness_values", "requirements", type_="check")
    op.drop_constraint("verification_type_values", "requirements", type_="check")
    op.drop_column("requirements", "automation_readiness")
    op.drop_column("requirements", "verification_type")
