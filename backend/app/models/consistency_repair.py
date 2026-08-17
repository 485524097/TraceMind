from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.knowledge_base import KnowledgeBase


class ConsistencyAuditSnapshotRecord(Base):
    __tablename__ = "consistency_audit_snapshots"
    __table_args__ = (
        CheckConstraint("scope IN ('knowledge_base', 'global')", name="ck_audit_snapshot_scope"),
        CheckConstraint("status IN ('completed', 'partial')", name="ck_audit_snapshot_status"),
        Index("ix_audit_snapshot_kb_created", "knowledge_base_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    knowledge_base_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    findings: Mapped[list["ConsistencyAuditFindingRecord"]] = relationship(
        back_populates="audit", cascade="all, delete-orphan", passive_deletes=True, lazy="raise"
    )


class ConsistencyAuditFindingRecord(Base):
    __tablename__ = "consistency_audit_findings"
    __table_args__ = (Index("ix_audit_findings_audit_code", "audit_id", "code"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    audit_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("consistency_audit_snapshots.id", ondelete="CASCADE")
    )
    code: Mapped[str] = mapped_column(String(96), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    knowledge_base_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    safe_message: Mapped[str] = mapped_column(String(500), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    audit: Mapped[ConsistencyAuditSnapshotRecord] = relationship(
        back_populates="findings", lazy="raise"
    )


class ConsistencyRepairOperation(Base):
    __tablename__ = "consistency_repair_operations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'partially_failed', 'failed', 'succeeded')",
            name="ck_consistency_repair_operation_status",
        ),
        Index(
            "uq_consistency_repair_operations_active",
            "knowledge_base_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index("ix_consistency_repair_kb_created", "knowledge_base_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    audit_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("consistency_audit_snapshots.id", ondelete="CASCADE")
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    run_generation: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, default=uuid4)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    knowledge_base: Mapped["KnowledgeBase"] = relationship(lazy="raise")
    items: Mapped[list["ConsistencyRepairItem"]] = relationship(
        back_populates="operation", cascade="all, delete-orphan", passive_deletes=True, lazy="raise"
    )


class ConsistencyRepairItem(Base):
    __tablename__ = "consistency_repair_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'planned', 'succeeded', 'failed', 'skipped', "
            "'not_repairable', 'verification_failed')",
            name="ck_consistency_repair_item_status",
        ),
        Index("ix_consistency_repair_items_operation", "operation_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    operation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("consistency_repair_operations.id", ondelete="CASCADE")
    )
    finding_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("consistency_audit_findings.id", ondelete="CASCADE")
    )
    finding_code: Mapped[str] = mapped_column(String(96), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    safe_message: Mapped[str] = mapped_column(String(500), nullable=False)
    operation: Mapped[ConsistencyRepairOperation] = relationship(
        back_populates="items", lazy="raise"
    )
