from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.knowledge_base import KnowledgeBase


class KnowledgeBaseRebuildOperation(Base):
    __tablename__ = "knowledge_base_rebuild_operations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'partially_failed', 'failed', 'succeeded')",
            name="ck_knowledge_base_rebuild_operations_status",
        ),
        Index(
            "uq_knowledge_base_rebuild_operations_active",
            "knowledge_base_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index(
            "ix_knowledge_base_rebuild_operations_knowledge_base_created",
            "knowledge_base_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "knowledge_bases.id",
            name="fk_knowledge_base_rebuild_operations_knowledge_base_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="queued", server_default="queued"
    )
    run_generation: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, default=uuid4)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    knowledge_base: Mapped["KnowledgeBase"] = relationship(lazy="raise")
    items: Mapped[list["KnowledgeBaseRebuildItem"]] = relationship(
        back_populates="operation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )


class KnowledgeBaseRebuildItem(Base):
    __tablename__ = "knowledge_base_rebuild_items"
    __table_args__ = (
        CheckConstraint(
            "work_type IN ('document_parse', 'document_index', 'knowledge_entry_index')",
            name="ck_knowledge_base_rebuild_items_work_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_knowledge_base_rebuild_items_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_knowledge_base_rebuild_attempt_count"),
        UniqueConstraint(
            "operation_id",
            "work_type",
            "target_id",
            name="uq_knowledge_base_rebuild_items_target",
        ),
        Index("ix_knowledge_base_rebuild_items_operation_status", "operation_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    operation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "knowledge_base_rebuild_operations.id",
            name="fk_knowledge_base_rebuild_items_operation_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    work_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    operation: Mapped[KnowledgeBaseRebuildOperation] = relationship(
        back_populates="items", lazy="raise"
    )
