"""允许正式 Run 使用 WEB_CASE 类型。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_0030"
down_revision: str | None = "20260901_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "runs"
_CONSTRAINT = "runs_run_type_values"
_WITH_WEB_CASE = "run_type IN ('API_CASE','SCENARIO','WEB_CASE')"
_WITHOUT_WEB_CASE = "run_type IN ('API_CASE','SCENARIO')"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _WITH_WEB_CASE)


def _ensure_no_web_case_rows() -> None:
    runs = sa.table("runs", sa.column("run_type", sa.String(length=32)))
    statement = (
        sa.select(sa.literal(1))
        .select_from(runs)
        .where(runs.c.run_type == "WEB_CASE")
        .limit(1)
    )
    if op.get_bind().execute(statement).first() is not None:
        raise RuntimeError(
            "不能 downgrade：runs 中存在 WEB_CASE 记录，请先由运维迁移或清理相关数据"
        )


def downgrade() -> None:
    _ensure_no_web_case_rows()
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _WITHOUT_WEB_CASE)
