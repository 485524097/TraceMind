"""Add optional symbol lookup keys to document chunks.

Revision ID: 20260803_0008
Revises: 20260730_0007
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260803_0008"
down_revision: str | None = "20260730_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column("symbol_lookup_keys", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_chunks", "symbol_lookup_keys")
