"""Create problem and solution knowledge entries.

Revision ID: 20260811_0009
Revises: 20260803_0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260811_0009"
down_revision: str | None = "20260803_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("background", sa.Text(), nullable=True),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("solution", sa.Text(), nullable=False),
        sa.Column("failed_attempts", sa.JSON(), nullable=False),
        sa.Column(
            "validation_status",
            sa.String(length=16),
            server_default="unverified",
            nullable=False,
        ),
        sa.Column("tags", postgresql.ARRAY(sa.String(length=50)), nullable=False),
        sa.Column("source_conversation_id", sa.Uuid(), nullable=True),
        sa.Column("source_user_message_id", sa.Uuid(), nullable=True),
        sa.Column("source_assistant_message_id", sa.Uuid(), nullable=True),
        sa.Column("question_snapshot", sa.Text(), nullable=False),
        sa.Column("answer_snapshot", sa.Text(), nullable=False),
        sa.Column("sources_snapshot", sa.JSON(), nullable=False),
        sa.Column("generation_metadata_snapshot", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "validation_status IN ('unverified', 'verified', 'outdated')",
            name="ck_knowledge_entries_validation_status",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name="fk_knowledge_entries_knowledge_base_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_conversation_id"],
            ["conversations.id"],
            name="fk_knowledge_entries_source_conversation_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_user_message_id"],
            ["conversation_messages.id"],
            name="fk_knowledge_entries_source_user_message_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_assistant_message_id"],
            ["conversation_messages.id"],
            name="fk_knowledge_entries_source_assistant_message_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_entries")),
        sa.UniqueConstraint(
            "source_assistant_message_id",
            name="uq_knowledge_entries_source_assistant_message_id",
        ),
    )
    op.create_index(
        "ix_knowledge_entries_knowledge_base_id",
        "knowledge_entries",
        ["knowledge_base_id"],
    )
    op.create_index(
        "ix_knowledge_entries_knowledge_base_status",
        "knowledge_entries",
        ["knowledge_base_id", "validation_status"],
    )
    op.create_index(
        "ix_knowledge_entries_knowledge_base_updated",
        "knowledge_entries",
        ["knowledge_base_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_entries_knowledge_base_updated", table_name="knowledge_entries")
    op.drop_index("ix_knowledge_entries_knowledge_base_status", table_name="knowledge_entries")
    op.drop_index("ix_knowledge_entries_knowledge_base_id", table_name="knowledge_entries")
    op.drop_table("knowledge_entries")
