import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.document import DocumentChunk, DocumentVersion
from app.parsing import DeterministicChunker, ParseContext, ParserRegistry
from app.parsing.exceptions import DocumentParseError, NoExtractableTextError
from app.repositories.document_parsing import DocumentParsingRepository
from app.services.document_dispatcher import DocumentParsingDispatcher
from app.services.exceptions import (
    DocumentParsingQueueError,
    DocumentStorageError,
    DocumentVersionNotFoundError,
)
from app.storage.local import LocalFileStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParseRequestResult:
    queued: bool
    version: DocumentVersion


@dataclass(frozen=True)
class ChunkPage:
    version: DocumentVersion
    items: list[DocumentChunk]
    total: int


class DocumentParsingService:
    def __init__(
        self,
        session: AsyncSession,
        storage: LocalFileStorage,
        settings: Settings,
        *,
        dispatcher: DocumentParsingDispatcher | None = None,
        repository: DocumentParsingRepository | None = None,
        registry: ParserRegistry | None = None,
        chunker: DeterministicChunker | None = None,
    ) -> None:
        self.session = session
        self.storage = storage
        self.settings = settings
        self.dispatcher = dispatcher
        self.repository = repository or DocumentParsingRepository(session)
        self.registry = registry or ParserRegistry()
        self.chunker = chunker or DeterministicChunker(
            max_chars=settings.document_chunk_max_chars,
            overlap_chars=settings.document_chunk_overlap_chars,
        )

    async def request_parse(
        self,
        knowledge_base_id: UUID,
        document_id: UUID,
        version_id: UUID,
        *,
        force: bool,
    ) -> ParseRequestResult:
        version = await self._require_scoped_version(knowledge_base_id, document_id, version_id)
        dispatch_force = force
        if version.parse_status == "processing":
            if not self.repository.is_processing_stale(
                version,
                now=datetime.now(UTC),
                stale_after_seconds=self.settings.document_parse_stale_after_seconds,
            ):
                return ParseRequestResult(False, version)
            dispatch_force = True
        if version.parse_status == "succeeded" and not force:
            return ParseRequestResult(False, version)
        if self.dispatcher is None:
            raise DocumentParsingQueueError("Document parsing queue is unavailable")
        await self.dispatcher.enqueue(version.id, force=dispatch_force)
        return ParseRequestResult(True, version)

    async def get_status(
        self, knowledge_base_id: UUID, document_id: UUID, version_id: UUID
    ) -> DocumentVersion:
        return await self._require_scoped_version(knowledge_base_id, document_id, version_id)

    async def list_chunks(
        self,
        knowledge_base_id: UUID,
        document_id: UUID,
        version_id: UUID,
        *,
        offset: int,
        limit: int,
    ) -> ChunkPage:
        version = await self._require_scoped_version(knowledge_base_id, document_id, version_id)
        items = await self.repository.list_chunks(version_id, offset=offset, limit=limit)
        total = await self.repository.count_chunks(version_id)
        return ChunkPage(version, items, total)

    async def parse_version(self, version_id: UUID, *, force: bool = False) -> bool:
        claimed = await self._claim(version_id, force=force)
        if claimed is None:
            return False
        version, had_chunks, attempt_started_at = claimed
        try:
            path = self.storage.resolve_relative(version.storage_path)
            parser = self.registry.get(version.extension)
            context = ParseContext(
                max_extracted_chars=self.settings.document_parse_max_extracted_chars,
                max_pdf_pages=self.settings.document_parse_max_pdf_pages,
            )
            parsed = await asyncio.to_thread(parser.parse, path, context)
            drafts = self.chunker.chunk(parsed.blocks)
            if not drafts:
                raise NoExtractableTextError()
        except DocumentParseError as exc:
            await self._record_failure(
                version_id, exc.code, exc.safe_message, had_chunks, attempt_started_at
            )
            return False
        except DocumentStorageError:
            await self._record_failure(
                version_id,
                "storage_unavailable",
                "Stored document is unavailable",
                had_chunks,
                attempt_started_at,
            )
            return False
        except Exception as exc:
            logger.error(
                "Unexpected document parsing failure for version %s (%s)",
                version_id,
                type(exc).__name__,
            )
            await self._record_failure(
                version_id,
                "internal_parse_error",
                "Document could not be parsed",
                had_chunks,
                attempt_started_at,
            )
            return False

        try:
            record = await self.repository.lock_version_for_parsing(version_id)
            if record is None:
                raise DocumentVersionNotFoundError("Document version was not found")
            if not self.repository.is_current_attempt(record.version, attempt_started_at):
                await self.session.rollback()
                logger.info("Document parsing attempt no longer owns version %s", version_id)
                return False
            await self.repository.delete_chunks_by_version(version_id)
            await self.repository.create_chunks(version_id, drafts)
            await self.repository.mark_succeeded(
                record.version,
                parser_name=parsed.parser_name,
                parser_version=parsed.parser_version,
                chunk_count=len(drafts),
                parsed_at=datetime.now(UTC),
            )
            await self.session.commit()
            return True
        except Exception as exc:
            await self.session.rollback()
            logger.error(
                "Document chunk replacement failed for version %s (%s)",
                version_id,
                type(exc).__name__,
            )
            await self._record_failure(
                version_id,
                "internal_parse_error",
                "Document chunks could not be saved",
                had_chunks,
                attempt_started_at,
            )
            return False

    async def _claim(
        self, version_id: UUID, *, force: bool
    ) -> tuple[DocumentVersion, bool, datetime] | None:
        record = await self.repository.lock_version_for_parsing(version_id)
        if record is None:
            raise DocumentVersionNotFoundError("Document version was not found")
        version = record.version
        now = datetime.now(UTC)
        if version.parse_status == "processing" and not self.repository.is_processing_stale(
            version,
            now=now,
            stale_after_seconds=self.settings.document_parse_stale_after_seconds,
        ):
            await self.session.rollback()
            return None
        if version.parse_status == "succeeded" and not force:
            await self.session.rollback()
            return None
        had_chunks = version.chunk_count > 0
        await self.repository.mark_processing(version, now)
        await self.session.commit()
        return version, had_chunks, now

    async def _record_failure(
        self,
        version_id: UUID,
        code: str,
        message: str,
        preserve_chunks: bool,
        attempt_started_at: datetime,
    ) -> None:
        await self.session.rollback()
        record = await self.repository.lock_version_for_parsing(version_id)
        if record is None:
            return
        if not self.repository.is_current_attempt(record.version, attempt_started_at):
            await self.session.rollback()
            logger.info("Document parsing attempt no longer owns version %s", version_id)
            return
        await self.repository.mark_failed(
            record.version,
            error_code=code,
            error_message=message,
            preserve_chunks=preserve_chunks,
        )
        try:
            await self.session.commit()
        except SQLAlchemyError:
            await self.session.rollback()
            raise

    async def _require_scoped_version(
        self, knowledge_base_id: UUID, document_id: UUID, version_id: UUID
    ) -> DocumentVersion:
        version = await self.repository.get_scoped_version(
            knowledge_base_id, document_id, version_id
        )
        if version is None:
            raise DocumentVersionNotFoundError("Document version was not found")
        return version
