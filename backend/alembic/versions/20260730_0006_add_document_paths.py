"""Add relative and normalized document paths.

Revision ID: 20260730_0006
Revises: 20260729_0005
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260730_0006"
down_revision: str | None = "20260729_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("relative_path", sa.String(length=1024)))
    op.add_column("documents", sa.Column("normalized_path", sa.String(length=1024)))
    op.execute(
        sa.text("UPDATE documents SET relative_path = name, normalized_path = normalized_name")
    )
    op.alter_column("documents", "relative_path", nullable=False)
    op.alter_column("documents", "normalized_path", nullable=False)
    op.drop_constraint(
        "uq_documents_knowledge_base_normalized_name",
        "documents",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_documents_knowledge_base_normalized_path",
        "documents",
        ["knowledge_base_id", "normalized_path"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_documents_knowledge_base_normalized_path",
        "documents",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_documents_knowledge_base_normalized_name",
        "documents",
        ["knowledge_base_id", "normalized_name"],
    )
    op.drop_column("documents", "normalized_path")
    op.drop_column("documents", "relative_path")
