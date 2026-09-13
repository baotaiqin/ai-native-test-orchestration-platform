"""将 Run 模块审计时间收口为 UTC-naive DATETIME。

本迁移的历史数据边界是：0017～0020 创建的五张 Run 表中，created_at 与
updated_at 的旧值来自 MySQL CURRENT_TIMESTAMP，且执行时区为 Asia/Shanghai
(UTC+08:00)。upgrade 将这些字段减去 8 小时；生命周期字段由应用写入 UTC
naive，因此明确不转换。downgrade 反向加回 8 小时并恢复数据库默认值，供
回滚到旧版应用时使用。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_0021"
down_revision: str | None = "20260825_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE_OFFSET_HOURS = 8
RUN_AUDIT_TABLES = (
    "runs",
    "case_runs",
    "step_runs",
    "run_dispatch_outbox",
    "run_api_execution_results",
)
AUDIT_TIMESTAMP_COLUMNS = ("created_at", "updated_at")
LIFECYCLE_TIMESTAMP_COLUMNS = (
    "started_at",
    "ended_at",
    "published_at",
    "claimed_at",
    "completed_at",
)


def _set_server_default(
    table_name: str,
    column_name: str,
    default: object,
    *,
    existing_default: object | None,
) -> None:
    op.alter_column(
        table_name,
        column_name,
        existing_type=sa.DateTime(),
        existing_nullable=False,
        existing_server_default=existing_default,
        server_default=default,
    )


def _convert_audit_timestamps(table_name: str, *, hours: int) -> None:
    table = f"`{table_name}`"
    interval = f"INTERVAL {abs(hours)} HOUR"
    operation = "DATE_SUB" if hours >= 0 else "DATE_ADD"
    op.execute(
        sa.text(
            f"UPDATE {table} "
            f"SET `created_at` = {operation}(`created_at`, {interval}), "
            f"`updated_at` = {operation}(`updated_at`, {interval})"
        )
    )


def upgrade() -> None:
    for table_name in RUN_AUDIT_TABLES:
        for column_name in AUDIT_TIMESTAMP_COLUMNS:
            _set_server_default(
                table_name,
                column_name,
                None,
                existing_default=sa.text("CURRENT_TIMESTAMP"),
            )
    for table_name in RUN_AUDIT_TABLES:
        _convert_audit_timestamps(table_name, hours=SOURCE_OFFSET_HOURS)


def downgrade() -> None:
    for table_name in RUN_AUDIT_TABLES:
        _convert_audit_timestamps(table_name, hours=-SOURCE_OFFSET_HOURS)
    for table_name in RUN_AUDIT_TABLES:
        for column_name in AUDIT_TIMESTAMP_COLUMNS:
            _set_server_default(
                table_name,
                column_name,
                sa.text("CURRENT_TIMESTAMP"),
                existing_default=None,
            )
