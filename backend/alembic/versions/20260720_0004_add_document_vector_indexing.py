"""Add dense vector indexing state to document versions.

Revision ID: 20260720_0004
Revises: 20260717_0003
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260720_0004"
down_revision: str | None = "20260717_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column("index_status", sa.String(length=32), server_default="pending", nullable=False),
    )
    op.add_column("document_versions", sa.Column("active_index_generation", sa.Uuid()))
    op.add_column("document_versions", sa.Column("index_started_at", sa.DateTime(timezone=True)))
    op.add_column("document_versions", sa.Column("indexed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "document_versions", sa.Column("last_index_attempt_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "document_versions",
        sa.Column("indexed_chunk_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("document_versions", sa.Column("embedding_model", sa.String(length=255)))
    op.add_column("document_versions", sa.Column("embedding_dimension", sa.Integer()))
    op.add_column("document_versions", sa.Column("index_error_code", sa.String(length=64)))
    op.add_column("document_versions", sa.Column("index_error_message", sa.String(length=500)))
    op.create_check_constraint(
        "ck_document_versions_index_status",
        "document_versions",
        "index_status IN ('pending', 'processing', 'succeeded', 'failed')",
    )
    op.create_check_constraint(
        "ck_document_versions_indexed_chunk_count_nonnegative",
        "document_versions",
        "indexed_chunk_count >= 0",
    )
    op.create_check_constraint(
        "ck_document_versions_embedding_dimension_positive",
        "document_versions",
        "embedding_dimension IS NULL OR embedding_dimension > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_document_versions_embedding_dimension_positive",
        "document_versions",
        type_="check",
    )
    op.drop_constraint(
        "ck_document_versions_indexed_chunk_count_nonnegative",
        "document_versions",
        type_="check",
    )
    op.drop_constraint("ck_document_versions_index_status", "document_versions", type_="check")
    for column in (
        "index_error_message",
        "index_error_code",
        "embedding_dimension",
        "embedding_model",
        "indexed_chunk_count",
        "last_index_attempt_at",
        "indexed_at",
        "index_started_at",
        "active_index_generation",
        "index_status",
    ):
        op.drop_column("document_versions", column)
