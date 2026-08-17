from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.consistency_repair import (
    ConsistencyAuditFindingRecord,
    ConsistencyAuditSnapshotRecord,
)
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_entry import KnowledgeEntry
from app.schemas.consistency_audit import ConsistencyAuditResponse


@dataclass(frozen=True)
class AuditDocumentVersion:
    knowledge_base_id: UUID
    document_id: UUID
    version_id: UUID
    version_number: int
    content_hash: str
    file_size: int
    extension: str
    storage_path: str
    parse_status: str
    declared_chunk_count: int
    index_status: str
    active_generation: UUID | None
    attempt_generation: UUID | None
    indexed_chunk_count: int
    parsed_at: datetime | None
    indexed_at: datetime | None
    actual_chunk_count: int


@dataclass(frozen=True)
class AuditKnowledgeEntry:
    knowledge_base_id: UUID
    entry_id: UUID
    validation_status: str
    index_status: str
    active_generation: UUID | None
    attempt_generation: UUID | None
    indexed_chunk_count: int
    indexed_at: datetime | None
    indexed_source_updated_at: datetime | None
    updated_at: datetime
    question: str
    background: str | None
    root_cause: str | None
    solution: str
    failed_attempts: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True)
class ConsistencyAuditSnapshot:
    knowledge_base_ids: frozenset[UUID]
    versions: tuple[AuditDocumentVersion, ...]
    knowledge_entries: tuple[AuditKnowledgeEntry, ...]
    orphan_chunk_version_ids: tuple[UUID, ...]


class ConsistencyAuditRepository:
    """Read source/derived metadata and persist content-free audit evidence."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def knowledge_base_exists(self, knowledge_base_id: UUID) -> bool:
        result = await self.session.execute(
            select(KnowledgeBase.id).where(KnowledgeBase.id == knowledge_base_id)
        )
        return result.scalar_one_or_none() is not None

    async def load_snapshot(self, knowledge_base_id: UUID | None) -> ConsistencyAuditSnapshot:
        kb_statement = select(KnowledgeBase.id)
        if knowledge_base_id is not None:
            kb_statement = kb_statement.where(KnowledgeBase.id == knowledge_base_id)
        knowledge_base_ids = frozenset((await self.session.execute(kb_statement)).scalars().all())

        chunk_counts = (
            select(
                DocumentChunk.document_version_id.label("version_id"),
                func.count(DocumentChunk.id).label("actual_chunk_count"),
            )
            .group_by(DocumentChunk.document_version_id)
            .subquery()
        )
        version_statement = (
            select(
                Document.knowledge_base_id,
                Document.id,
                DocumentVersion.id,
                DocumentVersion.version_number,
                DocumentVersion.content_hash,
                DocumentVersion.file_size,
                DocumentVersion.extension,
                DocumentVersion.storage_path,
                DocumentVersion.parse_status,
                DocumentVersion.chunk_count,
                DocumentVersion.index_status,
                DocumentVersion.active_index_generation,
                DocumentVersion.index_attempt_generation,
                DocumentVersion.indexed_chunk_count,
                DocumentVersion.parsed_at,
                DocumentVersion.indexed_at,
                func.coalesce(chunk_counts.c.actual_chunk_count, 0),
            )
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .outerjoin(chunk_counts, chunk_counts.c.version_id == DocumentVersion.id)
            .order_by(Document.knowledge_base_id, Document.id, DocumentVersion.version_number)
        )
        if knowledge_base_id is not None:
            version_statement = version_statement.where(
                Document.knowledge_base_id == knowledge_base_id
            )
        versions = tuple(
            AuditDocumentVersion(*row) for row in (await self.session.execute(version_statement))
        )

        entry_statement = select(
            KnowledgeEntry.knowledge_base_id,
            KnowledgeEntry.id,
            KnowledgeEntry.validation_status,
            KnowledgeEntry.index_status,
            KnowledgeEntry.active_index_generation,
            KnowledgeEntry.index_attempt_generation,
            KnowledgeEntry.indexed_chunk_count,
            KnowledgeEntry.indexed_at,
            KnowledgeEntry.indexed_source_updated_at,
            KnowledgeEntry.updated_at,
            KnowledgeEntry.question,
            KnowledgeEntry.background,
            KnowledgeEntry.root_cause,
            KnowledgeEntry.solution,
            KnowledgeEntry.failed_attempts,
            KnowledgeEntry.tags,
        ).order_by(KnowledgeEntry.knowledge_base_id, KnowledgeEntry.id)
        if knowledge_base_id is not None:
            entry_statement = entry_statement.where(
                KnowledgeEntry.knowledge_base_id == knowledge_base_id
            )
        knowledge_entries = tuple(
            AuditKnowledgeEntry(
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
                row[7],
                row[8],
                row[9],
                row[10],
                row[11],
                row[12],
                row[13],
                tuple(row[14] or ()),
                tuple(row[15] or ()),
            )
            for row in (await self.session.execute(entry_statement))
        )

        orphan_chunk_version_ids: tuple[UUID, ...] = ()
        if knowledge_base_id is None:
            orphan_statement = (
                select(DocumentChunk.document_version_id)
                .outerjoin(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
                .where(DocumentVersion.id.is_(None))
                .distinct()
            )
            orphan_chunk_version_ids = tuple(
                (await self.session.execute(orphan_statement)).scalars().all()
            )
        return ConsistencyAuditSnapshot(
            knowledge_base_ids,
            versions,
            knowledge_entries,
            orphan_chunk_version_ids,
        )

    async def save_report(self, report: ConsistencyAuditResponse) -> None:
        audit = ConsistencyAuditSnapshotRecord(
            id=report.audit_id,
            scope=report.scope,
            status=report.status,
            knowledge_base_id=report.knowledge_base_id,
            started_at=report.started_at,
            completed_at=report.completed_at,
        )
        self.session.add(audit)
        self.session.add_all(
            ConsistencyAuditFindingRecord(
                id=item.finding_id,
                audit_id=report.audit_id,
                code=item.code,
                severity=item.severity,
                entity_type=item.entity_type,
                entity_id=item.entity_id,
                knowledge_base_id=item.knowledge_base_id,
                safe_message=item.safe_message,
                details=dict(item.details),
            )
            for item in report.findings
        )
        await self.session.commit()
