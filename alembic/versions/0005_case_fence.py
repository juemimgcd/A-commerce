"""对账差异增加并发版本和人工复核水位。

Revision ID: 0005_case_fence
Revises: 0004_recovery
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_case_fence"
down_revision: str | None = "0004_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 旧差异从版本 1 开始；仅迁移时使用数据库默认值，之后由应用显式维护版本。
    op.add_column(
        "reconciliation_cases",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.alter_column("reconciliation_cases", "revision", server_default=None)
    op.add_column(
        "reconciliation_cases",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 历史数据未单独保存复核时刻。对有人工备注的记录，保守使用最后核对时间，
    # 让在此之前创建的旧轮次不能覆盖已有人工结论。
    op.execute(
        "UPDATE reconciliation_cases SET reviewed_at = checked_at WHERE resolution_note IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("reconciliation_cases", "reviewed_at")
    op.drop_column("reconciliation_cases", "revision")
