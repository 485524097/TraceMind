"""Add document parsing state and document chunks.

Revision ID: 20260717_0003
Revises: 20260717_0002
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260717_0003"
down_revision: str | None = "20260717_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column("parse_status", sa.String(length=32), server_default="pending", nullable=False),
    )
    op.add_column("document_versions", sa.Column("parser_name", sa.String(length=64)))
    op.add_column("document_versions", sa.Column("parser_version", sa.String(length=32)))
    op.add_column(
        "document_versions",
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("document_versions", sa.Column("parse_started_at", sa.DateTime(timezone=True)))
    op.add_column("document_versions", sa.Column("parsed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "document_versions", sa.Column("last_parse_attempt_at", sa.DateTime(timezone=True))
    )
    op.add_column("document_versions", sa.Column("parse_error_code", sa.String(length=64)))
    op.add_column("document_versions", sa.Column("parse_error_message", sa.String(length=500)))
    op.create_check_constraint(
        "ck_document_versions_chunk_count_nonnegative", "document_versions", "chunk_count >= 0"
    )
    op.create_check_constraint(
        "ck_document_versions_parse_status",
        "document_versions",
        "parse_status IN ('pending', 'processing', 'succeeded', 'failed')",
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer()),
        sa.Column("start_line", sa.Integer()),
        sa.Column("end_line", sa.Integer()),
        sa.Column("section_title", sa.String(length=500)),
        sa.Column("chunk_type", sa.String(length=32), nullable=False),
        sa.Column("language", sa.String(length=32)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("char_count > 0", name="ck_document_chunks_char_count_positive"),
        sa.CheckConstraint("chunk_index >= 0", name="ck_document_chunks_index_nonnegative"),
        sa.CheckConstraint("char_length(content_hash) = 64", name="ck_document_chunks_hash_length"),
        sa.CheckConstraint(
            "page_number IS NULL OR page_number > 0", name="ck_document_chunks_page_positive"
        ),
        sa.CheckConstraint(
            "start_line IS NULL OR start_line > 0", name="ck_document_chunks_start_line_positive"
        ),
        sa.CheckConstraint(
            "end_line IS NULL OR end_line > 0", name="ck_document_chunks_end_line_positive"
        ),
        sa.CheckConstraint(
            "(start_line IS NULL AND end_line IS NULL) OR "
            "(start_line IS NOT NULL AND end_line IS NOT NULL)",
            name="ck_document_chunks_line_pair",
        ),
        sa.CheckConstraint(
            "start_line IS NULL OR end_line >= start_line", name="ck_document_chunks_line_order"
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name="fk_document_chunks_document_version_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_chunks")),
        sa.UniqueConstraint(
            "document_version_id", "chunk_index", name="uq_document_chunks_version_index"
        ),
    )
    op.create_index(
        "ix_document_chunks_document_version_id", "document_chunks", ["document_version_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_document_version_id", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_constraint("ck_document_versions_parse_status", "document_versions", type_="check")
    op.drop_constraint(
        "ck_document_versions_chunk_count_nonnegative", "document_versions", type_="check"
    )
    for column in (
        "parse_error_message",
        "parse_error_code",
        "last_parse_attempt_at",
        "parsed_at",
        "parse_started_at",
        "chunk_count",
        "parser_version",
        "parser_name",
        "parse_status",
    ):
        op.drop_column("document_versions", column)
