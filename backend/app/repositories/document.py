from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentVersion


@dataclass(frozen=True)
class DocumentRecord:
    document: Document
    latest_version: DocumentVersion
    version_count: int


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_document(self, document: Document) -> Document:
        self.session.add(document)
        await self.session.flush()
        return document

    async def create_version(self, version: DocumentVersion) -> DocumentVersion:
        self.session.add(version)
        await self.session.flush()
        return version

    async def get_document_by_id(
        self, knowledge_base_id: UUID, document_id: UUID
    ) -> Document | None:
        result = await self.session.execute(
            select(Document).where(
                Document.knowledge_base_id == knowledge_base_id,
                Document.id == document_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_document_by_normalized_name(
        self, knowledge_base_id: UUID, normalized_name: str
    ) -> Document | None:
        result = await self.session.execute(
            select(Document).where(
                Document.knowledge_base_id == knowledge_base_id,
                Document.normalized_name == normalized_name,
            )
        )
        return result.scalar_one_or_none()

    def _records_statement(self) -> Select[tuple[Document, DocumentVersion, int]]:
        version_stats = (
            select(
                DocumentVersion.document_id.label("document_id"),
                func.max(DocumentVersion.version_number).label("latest_number"),
                func.count(DocumentVersion.id).label("version_count"),
            )
            .group_by(DocumentVersion.document_id)
            .subquery()
        )
        return (
            select(Document, DocumentVersion, version_stats.c.version_count)
            .join(version_stats, version_stats.c.document_id == Document.id)
            .join(
                DocumentVersion,
                (DocumentVersion.document_id == Document.id)
                & (DocumentVersion.version_number == version_stats.c.latest_number),
            )
        )

    async def get_document_record(
        self, knowledge_base_id: UUID, document_id: UUID
    ) -> DocumentRecord | None:
        statement = self._records_statement().where(
            Document.knowledge_base_id == knowledge_base_id,
            Document.id == document_id,
        )
        row = (await self.session.execute(statement)).one_or_none()
        return self._to_record(row) if row is not None else None

    async def list_documents(
        self,
        knowledge_base_id: UUID,
        *,
        offset: int,
        limit: int,
        query: str | None,
    ) -> list[DocumentRecord]:
        statement = self._records_statement().where(Document.knowledge_base_id == knowledge_base_id)
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            statement = statement.where(Document.name.ilike(f"%{escaped}%", escape="\\"))
        statement = statement.order_by(Document.created_at.desc(), Document.id.desc())
        rows = (await self.session.execute(statement.offset(offset).limit(limit))).all()
        return [self._to_record(row) for row in rows]

    async def count_documents(self, knowledge_base_id: UUID, *, query: str | None) -> int:
        statement = (
            select(func.count())
            .select_from(Document)
            .where(Document.knowledge_base_id == knowledge_base_id)
        )
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            statement = statement.where(Document.name.ilike(f"%{escaped}%", escape="\\"))
        return int((await self.session.execute(statement)).scalar_one())

    async def count_by_knowledge_base(self, knowledge_base_id: UUID) -> int:
        return await self.count_documents(knowledge_base_id, query=None)

    async def get_latest_version(
        self, knowledge_base_id: UUID, document_id: UUID
    ) -> DocumentVersion | None:
        result = await self.session.execute(
            select(DocumentVersion)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                Document.knowledge_base_id == knowledge_base_id,
                DocumentVersion.document_id == document_id,
            )
            .order_by(DocumentVersion.version_number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_version(
        self, knowledge_base_id: UUID, document_id: UUID, version_id: UUID
    ) -> DocumentVersion | None:
        result = await self.session.execute(
            select(DocumentVersion)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                Document.knowledge_base_id == knowledge_base_id,
                DocumentVersion.document_id == document_id,
                DocumentVersion.id == version_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_versions(
        self, knowledge_base_id: UUID, document_id: UUID
    ) -> list[DocumentVersion]:
        result = await self.session.execute(
            select(DocumentVersion)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                Document.knowledge_base_id == knowledge_base_id,
                DocumentVersion.document_id == document_id,
            )
            .order_by(DocumentVersion.version_number.desc())
        )
        return list(result.scalars().all())

    async def touch_document(self, document: Document) -> None:
        document.updated_at = datetime.now(UTC)
        await self.session.flush()

    async def delete_document(self, document: Document) -> None:
        await self.session.delete(document)
        await self.session.flush()

    @staticmethod
    def _to_record(row: Row[tuple[Document, DocumentVersion, int]]) -> DocumentRecord:
        document, latest_version, version_count = row
        return DocumentRecord(
            document=document,
            latest_version=latest_version,
            version_count=int(version_count),
        )
