"""退货寄回流程、商品变更历史与导出文件的执行版本隔离

Revision ID: 0003_workflows
Revises: 0002_operations
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_workflows"
down_revision: str | None = "0002_operations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 商品历史与失效命令同事务保存；导出文件按执行版本命名，旧 worker 不覆盖新结果。
    op.create_table(
        "catalog_changes",
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], name=op.f("fk_catalog_changes_product_id_products")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_changes")),
        sa.UniqueConstraint("product_id", "version", name=op.f("uq_catalog_changes_product_id")),
    )
    op.add_column("export_jobs", sa.Column("file_key", sa.String(length=100), nullable=True))
    op.execute("UPDATE export_jobs SET file_key = id::text || '.csv' WHERE status = 'done'")
    op.add_column("return_requests", sa.Column("carrier", sa.String(length=80), nullable=True))
    op.add_column(
        "return_requests", sa.Column("tracking_number", sa.String(length=100), nullable=True)
    )


def downgrade() -> None:
    # 含业务数据的环境优先前向修复，不直接回退删除历史。
    op.drop_column("return_requests", "tracking_number")
    op.drop_column("return_requests", "carrier")
    op.drop_column("export_jobs", "file_key")
    op.drop_table("catalog_changes")
