"""Add optional Java symbol metadata to document chunks.

Revision ID: 20260730_0007
Revises: 20260730_0006
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260730_0007"
down_revision: str | None = "20260730_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column("symbol_kind", sa.String(length=32)))
    op.add_column("document_chunks", sa.Column("symbol_name", sa.String(length=255)))
    op.add_column("document_chunks", sa.Column("symbol_qualified_name", sa.String(length=1024)))
    op.add_column("document_chunks", sa.Column("symbol_signature", sa.String(length=1024)))


def downgrade() -> None:
    op.drop_column("document_chunks", "symbol_signature")
    op.drop_column("document_chunks", "symbol_qualified_name")
    op.drop_column("document_chunks", "symbol_name")
    op.drop_column("document_chunks", "symbol_kind")
