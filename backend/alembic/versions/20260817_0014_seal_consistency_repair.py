"""Seal consistency repair lease, concurrency, and deletion semantics.

Revision ID: 20260817_0014
Revises: 20260817_0013
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260817_0014"
down_revision: str | None = "20260817_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "consistency_repair_operations",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_consistency_repair_operations_active",
        "consistency_repair_operations",
        ["knowledge_base_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.drop_constraint(
        "consistency_repair_items_finding_id_fkey",
        "consistency_repair_items",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_consistency_repair_items_finding_id",
        "consistency_repair_items",
        "consistency_audit_findings",
        ["finding_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_consistency_repair_items_finding_id",
        "consistency_repair_items",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "consistency_repair_items_finding_id_fkey",
        "consistency_repair_items",
        "consistency_audit_findings",
        ["finding_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_index(
        "uq_consistency_repair_operations_active",
        table_name="consistency_repair_operations",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.drop_column("consistency_repair_operations", "heartbeat_at")
