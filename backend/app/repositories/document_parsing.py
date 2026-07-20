from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentChunk, DocumentVersion
from app.parsing.chunker import ChunkDraft


@dataclass(frozen=True)
class ParsingVersionRecord:
    document: Document
    version: DocumentVersion


class DocumentParsingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_version_for_parsing(self, version_id: UUID) -> ParsingVersionRecord | None:
        return await self._get_version(version_id, lock=False)

    async def lock_version_for_parsing(self, version_id: UUID) -> ParsingVersionRecord | None:
        return await self._get_version(version_id, lock=True)

    async def _get_version(self, version_id: UUID, *, lock: bool) -> ParsingVersionRecord | None:
        statement = (
            select(Document, DocumentVersion)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(DocumentVersion.id == version_id)
        )
        if lock:
            statement = statement.with_for_update(of=DocumentVersion).execution_options(
                populate_existing=True
            )
        row = (await self.session.execute(statement)).one_or_none()
        return ParsingVersionRecord(*row) if row is not None else None

    async def get_scoped_version(
        self, knowledge_base_id: UUID, document_id: UUID, version_id: UUID
    ) -> DocumentVersion | None:
        result = await self.session.execute(
            select(DocumentVersion)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                Document.knowledge_base_id == knowledge_base_id,
                Document.id == document_id,
                DocumentVersion.id == version_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def is_processing_stale(
        version: DocumentVersion, *, now: datetime, stale_after_seconds: int
    ) -> bool:
        if version.parse_status != "processing" or version.parse_started_at is None:
            return version.parse_status == "processing"
        return version.parse_started_at <= now - timedelta(seconds=stale_after_seconds)

    @staticmethod
    def is_current_attempt(version: DocumentVersion, attempt_started_at: datetime) -> bool:
        if version.parse_status != "processing" or version.parse_started_at is None:
            return False

        def as_utc(value: datetime) -> datetime:
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)

        return as_utc(version.parse_started_at) == as_utc(attempt_started_at)

    async def count_chunks(self, version_id: UUID) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.document_version_id == version_id)
        )
        return int(result.scalar_one())

    async def list_chunks(
        self, version_id: UUID, *, offset: int, limit: int
    ) -> list[DocumentChunk]:
        result = await self.session.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_version_id == version_id)
            .order_by(DocumentChunk.chunk_index.asc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def delete_chunks_by_version(self, version_id: UUID) -> None:
        await self.session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_version_id == version_id)
        )

    async def create_chunks(self, version_id: UUID, drafts: list[ChunkDraft]) -> None:
        self.session.add_all(
            [
                DocumentChunk(
                    document_version_id=version_id,
                    chunk_index=draft.chunk_index,
                    content=draft.content,
                    content_hash=draft.content_hash,
                    char_count=draft.char_count,
                    page_number=draft.page_number,
                    start_line=draft.start_line,
                    end_line=draft.end_line,
                    section_title=draft.section_title,
                    chunk_type=draft.chunk_type,
                    language=draft.language,
                )
                for draft in drafts
            ]
        )
        await self.session.flush()

    async def mark_pending(self, version: DocumentVersion) -> None:
        version.parse_status = "pending"
        await self.session.flush()

    async def mark_processing(self, version: DocumentVersion, now: datetime) -> None:
        version.parse_status = "processing"
        version.parse_started_at = now
        version.last_parse_attempt_at = now
        await self.session.flush()

    async def mark_succeeded(
        self,
        version: DocumentVersion,
        *,
        parser_name: str,
        parser_version: str,
        chunk_count: int,
        parsed_at: datetime,
    ) -> None:
        version.parse_status = "succeeded"
        version.parser_name = parser_name
        version.parser_version = parser_version
        version.chunk_count = chunk_count
        version.parsed_at = parsed_at
        version.parse_error_code = None
        version.parse_error_message = None
        version.index_status = "pending"
        version.index_error_code = None
        version.index_error_message = None
        await self.session.flush()

    async def mark_failed(
        self,
        version: DocumentVersion,
        *,
        error_code: str,
        error_message: str,
        preserve_chunks: bool,
    ) -> None:
        version.parse_status = "succeeded" if preserve_chunks else "failed"
        if not preserve_chunks:
            version.chunk_count = 0
        version.parse_error_code = error_code
        version.parse_error_message = error_message[:500]
        await self.session.flush()
