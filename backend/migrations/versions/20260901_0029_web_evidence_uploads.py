"""扩展 Web Evidence 类型、大小边界和同类型多文件幂等键。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0029"
down_revision: str | None = "20260831_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_TYPES = "artifact_type IN ('RESPONSE','ASSERTION_RESULT')"
_WEB_TYPES = (
    "artifact_type IN ('RESPONSE','ASSERTION_RESULT','SCREENSHOT',"
    "'PLAYWRIGHT_TRACE','CONSOLE_ERROR','NETWORK_ERROR','WEB_SUMMARY')"
)
_OLD_SIZE = "size >= 0 AND size <= 2000000"
_WEB_SIZE = "size >= 0 AND size <= 20000000"
_WEB_ARTIFACT_TYPES = (
    "SCREENSHOT",
    "PLAYWRIGHT_TRACE",
    "CONSOLE_ERROR",
    "NETWORK_ERROR",
    "WEB_SUMMARY",
)


def _replace_checks(*, type_expression: str, size_expression: str) -> None:
    op.drop_constraint("artifacts_type_values", "artifacts", type_="check")
    op.create_check_constraint(
        "artifacts_type_values", "artifacts", type_expression
    )
    op.drop_constraint("artifacts_size_bounds", "artifacts", type_="check")
    op.create_check_constraint(
        "artifacts_size_bounds", "artifacts", size_expression
    )


def upgrade() -> None:
    _replace_checks(type_expression=_WEB_TYPES, size_expression=_WEB_SIZE)
    op.drop_constraint(
        "uq_artifacts_run_case_type", "artifacts", type_="unique"
    )
    op.create_unique_constraint(
        "uq_artifacts_run_case_type",
        "artifacts",
        ["run_id", "case_run_id", "artifact_type", "file_name"],
    )


def _ensure_no_web_evidence_rows() -> None:
    artifacts = sa.table(
        "artifacts", sa.column("artifact_type", sa.String(length=32))
    )
    statement = (
        sa.select(sa.literal(1))
        .select_from(artifacts)
        .where(artifacts.c.artifact_type.in_(_WEB_ARTIFACT_TYPES))
        .limit(1)
    )
    if op.get_bind().execute(statement).first() is not None:
        raise RuntimeError(
            "不能 downgrade：artifacts 中存在 Web Evidence；请先备份并由运维显式清理 MinIO/DB 数据"
        )


def downgrade() -> None:
    _ensure_no_web_evidence_rows()
    op.drop_constraint(
        "uq_artifacts_run_case_type", "artifacts", type_="unique"
    )
    op.create_unique_constraint(
        "uq_artifacts_run_case_type",
        "artifacts",
        ["run_id", "case_run_id", "artifact_type"],
    )
    _replace_checks(type_expression=_OLD_TYPES, size_expression=_OLD_SIZE)
