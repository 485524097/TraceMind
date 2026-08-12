from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entries"
    __table_args__ = (
        CheckConstraint(
            "validation_status IN ('unverified', 'verified', 'outdated')",
            name="ck_knowledge_entries_validation_status",
        ),
        UniqueConstraint(
            "source_assistant_message_id",
            name="uq_knowledge_entries_source_assistant_message_id",
        ),
        Index("ix_knowledge_entries_knowledge_base_id", "knowledge_base_id"),
        Index(
            "ix_knowledge_entries_knowledge_base_status",
            "knowledge_base_id",
            "validation_status",
        ),
        Index(
            "ix_knowledge_entries_knowledge_base_updated",
            "knowledge_base_id",
            "updated_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "knowledge_bases.id",
            name="fk_knowledge_entries_knowledge_base_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    background: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    solution: Mapped[str] = mapped_column(Text, nullable=False)
    failed_attempts: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    validation_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unverified", server_default="unverified"
    )
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(50)), nullable=False, default=list)
    source_conversation_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "conversations.id",
            name="fk_knowledge_entries_source_conversation_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    source_user_message_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "conversation_messages.id",
            name="fk_knowledge_entries_source_user_message_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    source_assistant_message_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "conversation_messages.id",
            name="fk_knowledge_entries_source_assistant_message_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    question_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    answer_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    sources_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    generation_metadata_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
