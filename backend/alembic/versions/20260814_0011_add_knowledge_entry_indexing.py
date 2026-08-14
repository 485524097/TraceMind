"""Add asynchronous vector indexing state to knowledge entries.

Revision ID: 20260814_0011
Revises: 20260811_0010
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260814_0011"
down_revision: str | None = "20260811_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_entries",
        sa.Column(
            "index_status", sa.String(length=16), server_default="not_indexed", nullable=False
        ),
    )
    op.add_column(
        "knowledge_entries", sa.Column("active_index_generation", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "knowledge_entries", sa.Column("index_attempt_generation", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "knowledge_entries",
        sa.Column("index_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "knowledge_entries", sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "knowledge_entries",
        sa.Column("indexed_source_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "knowledge_entries",
        sa.Column("last_index_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "knowledge_entries",
        sa.Column("indexed_chunk_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "knowledge_entries", sa.Column("embedding_model", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "knowledge_entries", sa.Column("embedding_dimension", sa.Integer(), nullable=True)
    )
    op.add_column(
        "knowledge_entries", sa.Column("index_error_code", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "knowledge_entries", sa.Column("index_error_message", sa.String(length=500), nullable=True)
    )
    op.create_check_constraint(
        "ck_knowledge_entries_index_status",
        "knowledge_entries",
        "index_status IN ('not_indexed', 'pending', 'processing', 'succeeded', 'failed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_knowledge_entries_index_status", "knowledge_entries", type_="check")
    for column in (
        "index_error_message",
        "index_error_code",
        "embedding_dimension",
        "embedding_model",
        "indexed_chunk_count",
        "last_index_attempt_at",
        "indexed_source_updated_at",
        "indexed_at",
        "index_started_at",
        "index_attempt_generation",
        "active_index_generation",
        "index_status",
    ):
        op.drop_column("knowledge_entries", column)
