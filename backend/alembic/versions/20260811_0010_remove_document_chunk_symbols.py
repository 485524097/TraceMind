"""Remove Java symbol metadata from document chunks.

Revision ID: 20260811_0010
Revises: 20260811_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260811_0010"
down_revision: str | None = "20260811_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("document_chunks", "symbol_lookup_keys")
    op.drop_column("document_chunks", "symbol_signature")
    op.drop_column("document_chunks", "symbol_qualified_name")
    op.drop_column("document_chunks", "symbol_name")
    op.drop_column("document_chunks", "symbol_kind")


def downgrade() -> None:
    op.add_column("document_chunks", sa.Column("symbol_kind", sa.String(length=32)))
    op.add_column("document_chunks", sa.Column("symbol_name", sa.String(length=255)))
    op.add_column("document_chunks", sa.Column("symbol_qualified_name", sa.String(length=1024)))
    op.add_column("document_chunks", sa.Column("symbol_signature", sa.String(length=1024)))
    op.add_column("document_chunks", sa.Column("symbol_lookup_keys", sa.JSON()))
