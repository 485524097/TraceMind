from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.knowledge_base import KnowledgeBase


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_knowledge_base_id", "knowledge_base_id"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "knowledge_bases.id",
            name="fk_conversations_knowledge_base_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    knowledge_base: Mapped["KnowledgeBase"] = relationship(
        back_populates="conversations", lazy="raise"
    )
    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
        order_by=lambda: (ConversationMessage.created_at, ConversationMessage.id),
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_conversation_messages_role"),
        CheckConstraint(
            "status IN ('completed', 'no_answer', 'failed', 'cancelled')",
            name="ck_conversation_messages_status",
        ),
        Index("ix_conversation_messages_conversation_id", "conversation_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "conversations.id",
            name="fk_conversation_messages_conversation_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    trace_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    sources: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    generation_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages", lazy="raise")
