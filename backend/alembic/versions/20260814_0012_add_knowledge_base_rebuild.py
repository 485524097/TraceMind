"""Add persistent Knowledge Base derived-state rebuild operations.

Revision ID: 20260814_0012
Revises: 20260814_0011
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260814_0012"
down_revision: str | None = "20260814_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_base_rebuild_operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("run_generation", sa.Uuid(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'partially_failed', 'failed', 'succeeded')",
            name="ck_knowledge_base_rebuild_operations_status",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name="fk_knowledge_base_rebuild_operations_knowledge_base_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_base_rebuild_operations_knowledge_base_created",
        "knowledge_base_rebuild_operations",
        ["knowledge_base_id", "created_at"],
    )
    op.create_index(
        "uq_knowledge_base_rebuild_operations_active",
        "knowledge_base_rebuild_operations",
        ["knowledge_base_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_table(
        "knowledge_base_rebuild_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("work_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.CheckConstraint("attempt_count >= 0", name="ck_knowledge_base_rebuild_attempt_count"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_knowledge_base_rebuild_items_status",
        ),
        sa.CheckConstraint(
            "work_type IN ('document_parse', 'document_index', 'knowledge_entry_index')",
            name="ck_knowledge_base_rebuild_items_work_type",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["knowledge_base_rebuild_operations.id"],
            name="fk_knowledge_base_rebuild_items_operation_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operation_id",
            "work_type",
            "target_id",
            name="uq_knowledge_base_rebuild_items_target",
        ),
    )
    op.create_index(
        "ix_knowledge_base_rebuild_items_operation_status",
        "knowledge_base_rebuild_items",
        ["operation_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_base_rebuild_items_operation_status",
        table_name="knowledge_base_rebuild_items",
    )
    op.drop_table("knowledge_base_rebuild_items")
    op.drop_index(
        "uq_knowledge_base_rebuild_operations_active",
        table_name="knowledge_base_rebuild_operations",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.drop_index(
        "ix_knowledge_base_rebuild_operations_knowledge_base_created",
        table_name="knowledge_base_rebuild_operations",
    )
    op.drop_table("knowledge_base_rebuild_operations")
