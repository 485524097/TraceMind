"""Add minimal audit snapshots and selected consistency repair operations.

Revision ID: 20260817_0013
Revises: 20260814_0012
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260817_0013"
down_revision: str | None = "20260814_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "consistency_audit_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("scope IN ('knowledge_base', 'global')", name="ck_audit_snapshot_scope"),
        sa.CheckConstraint("status IN ('completed', 'partial')", name="ck_audit_snapshot_status"),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_snapshot_kb_created",
        "consistency_audit_snapshots",
        ["knowledge_base_id", "created_at"],
    )
    op.create_table(
        "consistency_audit_findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("audit_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(96), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(128), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=True),
        sa.Column("safe_message", sa.String(500), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["audit_id"], ["consistency_audit_snapshots.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_findings_audit_code", "consistency_audit_findings", ["audit_id", "code"]
    )
    op.create_table(
        "consistency_repair_operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("audit_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("run_generation", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'partially_failed', 'failed', 'succeeded')",
            name="ck_consistency_repair_operation_status",
        ),
        sa.ForeignKeyConstraint(
            ["audit_id"], ["consistency_audit_snapshots.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_consistency_repair_kb_created",
        "consistency_repair_operations",
        ["knowledge_base_id", "created_at"],
    )
    op.create_table(
        "consistency_repair_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("finding_code", sa.String(96), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safe_message", sa.String(500), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'planned', 'succeeded', 'failed', "
            "'skipped', 'not_repairable', 'verification_failed')",
            name="ck_consistency_repair_item_status",
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"], ["consistency_audit_findings.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"], ["consistency_repair_operations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_consistency_repair_items_operation",
        "consistency_repair_items",
        ["operation_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_consistency_repair_items_operation", table_name="consistency_repair_items")
    op.drop_table("consistency_repair_items")
    op.drop_index("ix_consistency_repair_kb_created", table_name="consistency_repair_operations")
    op.drop_table("consistency_repair_operations")
    op.drop_index("ix_audit_findings_audit_code", table_name="consistency_audit_findings")
    op.drop_table("consistency_audit_findings")
    op.drop_index("ix_audit_snapshot_kb_created", table_name="consistency_audit_snapshots")
    op.drop_table("consistency_audit_snapshots")
