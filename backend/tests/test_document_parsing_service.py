from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.parsing.base import ParseContext, ParsedBlock, ParsedDocument
from app.parsing.chunker import ChunkDraft
from app.parsing.exceptions import DocumentEncodingError
from app.repositories.document_parsing import DocumentParsingRepository, ParsingVersionRecord
from app.services.document_dispatcher import DocumentParsingDispatcher
from app.services.document_parsing import DocumentParsingService
from app.services.exceptions import DocumentParsingQueueError, DocumentVersionNotFoundError
from app.storage.local import LocalFileStorage


class FakeParser:
    parser_name = "fake"
    parser_version = "1"
    supported_extensions = frozenset({".md"})

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def parse(self, _path: Path, _context: ParseContext) -> ParsedDocument:
        if self.error is not None:
            raise self.error
        return ParsedDocument(
            [ParsedBlock("first\nsecond", "paragraph", start_line=1, end_line=2)],
            self.parser_name,
            self.parser_version,
        )


class FakeRegistry:
    def __init__(self, parser: FakeParser) -> None:
        self.parser = parser

    def get(self, _extension: str) -> FakeParser:
        return self.parser


class FakeParsingRepository:
    def __init__(self, document: Document, version: DocumentVersion) -> None:
        self.record = ParsingVersionRecord(document, version)
        self.chunks: list[DocumentChunk] = []
        self.deleted = 0
        self.created = 0
        self.failure: tuple[str, str, bool] | None = None

    async def lock_version_for_parsing(self, version_id: UUID) -> ParsingVersionRecord | None:
        return self.record if version_id == self.record.version.id else None

    async def get_scoped_version(
        self, knowledge_base_id: UUID, document_id: UUID, version_id: UUID
    ) -> DocumentVersion | None:
        if (
            knowledge_base_id == self.record.document.knowledge_base_id
            and document_id == self.record.document.id
            and version_id == self.record.version.id
        ):
            return self.record.version
        return None

    @staticmethod
    def is_processing_stale(
        version: DocumentVersion, *, now: datetime, stale_after_seconds: int
    ) -> bool:
        if version.parse_started_at is None:
            return True
        return version.parse_started_at <= now - timedelta(seconds=stale_after_seconds)

    async def mark_processing(self, version: DocumentVersion, now: datetime) -> None:
        version.parse_status = "processing"
        version.parse_started_at = now
        version.last_parse_attempt_at = now

    async def delete_chunks_by_version(self, _version_id: UUID) -> None:
        self.deleted += 1
        self.chunks = []

    async def create_chunks(self, version_id: UUID, drafts: list[ChunkDraft]) -> None:
        self.created += 1
        self.chunks = [
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

    async def mark_failed(
        self,
        version: DocumentVersion,
        *,
        error_code: str,
        error_message: str,
        preserve_chunks: bool,
    ) -> None:
        self.failure = (error_code, error_message, preserve_chunks)
        version.parse_status = "succeeded" if preserve_chunks else "failed"
        version.parse_error_code = error_code
        version.parse_error_message = error_message
        if not preserve_chunks:
            version.chunk_count = 0

    async def list_chunks(
        self, _version_id: UUID, *, offset: int, limit: int
    ) -> list[DocumentChunk]:
        return self.chunks[offset : offset + limit]

    async def count_chunks(self, _version_id: UUID) -> int:
        return len(self.chunks)


class FakeDispatcher:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[UUID, bool]] = []
        self.error = error

    async def enqueue(self, document_version_id: UUID, *, force: bool = False) -> None:
        self.calls.append((document_version_id, force))
        if self.error is not None:
            raise self.error


def make_version(
    tmp_path: Path, *, status: str = "pending", chunks: int = 0
) -> tuple[
    Document,
    DocumentVersion,
    LocalFileStorage,
]:
    knowledge_base_id, document_id, version_id = uuid4(), uuid4(), uuid4()
    document = Document(
        id=document_id,
        knowledge_base_id=knowledge_base_id,
        name="sample.md",
        normalized_name="sample.md",
        source_type="upload",
    )
    storage = LocalFileStorage(tmp_path / "uploads", max_size=1024, chunk_size=64)
    relative = storage.final_relative_path(knowledge_base_id, document_id, version_id, ".md")
    path = storage.resolve_relative(relative, must_exist=False)
    path.parent.mkdir(parents=True)
    path.write_text("content", encoding="utf-8")
    version = DocumentVersion(
        id=version_id,
        document_id=document_id,
        version_number=1,
        content_hash="a" * 64,
        file_size=7,
        mime_type="text/markdown",
        extension=".md",
        storage_path=relative,
        parse_status=status,
        chunk_count=chunks,
    )
    return document, version, storage


def make_service(
    tmp_path: Path,
    *,
    status: str = "pending",
    chunks: int = 0,
    parser: FakeParser | None = None,
    dispatcher: FakeDispatcher | None = None,
) -> tuple[
    DocumentParsingService,
    AsyncMock,
    FakeParsingRepository,
    Document,
    DocumentVersion,
]:
    document, version, storage = make_version(tmp_path, status=status, chunks=chunks)
    session = AsyncMock(spec=AsyncSession)
    repository = FakeParsingRepository(document, version)
    service = DocumentParsingService(
        cast(AsyncSession, session),
        storage,
        Settings(
            document_storage_root=storage.root,
            document_chunk_max_chars=20,
            document_chunk_overlap_chars=5,
        ),
        dispatcher=cast(DocumentParsingDispatcher, dispatcher) if dispatcher else None,
        repository=cast(DocumentParsingRepository, repository),
        registry=cast(object, FakeRegistry(parser or FakeParser())),
    )
    return service, session, repository, document, version


async def test_pending_processing_succeeded_creates_chunks(tmp_path: Path) -> None:
    service, session, repository, _, version = make_service(tmp_path)

    assert await service.parse_version(version.id)

    assert version.parse_status == "succeeded"
    assert version.chunk_count == 1
    assert version.parser_name == "fake"
    assert repository.deleted == repository.created == 1
    assert repository.chunks[0].start_line == 1
    assert session.commit.await_count == 2


async def test_initial_parse_failure_marks_failed_with_safe_error(tmp_path: Path) -> None:
    service, _, repository, _, version = make_service(
        tmp_path, parser=FakeParser(DocumentEncodingError("C:\\private\\file"))
    )

    assert not await service.parse_version(version.id)

    assert version.parse_status == "failed"
    assert repository.failure == ("invalid_encoding", "Document must use UTF-8 encoding", False)
    assert "private" not in (version.parse_error_message or "")


async def test_failed_force_reparse_preserves_existing_chunks(tmp_path: Path) -> None:
    service, _, repository, _, version = make_service(
        tmp_path,
        status="succeeded",
        chunks=2,
        parser=FakeParser(DocumentEncodingError()),
    )

    assert not await service.parse_version(version.id, force=True)

    assert version.parse_status == "succeeded"
    assert version.chunk_count == 2
    assert repository.failure is not None and repository.failure[2]
    assert repository.deleted == 0


async def test_succeeded_non_force_and_fresh_processing_are_idempotent(tmp_path: Path) -> None:
    succeeded, succeeded_session, _, _, version = make_service(
        tmp_path, status="succeeded", chunks=1
    )
    assert not await succeeded.parse_version(version.id)
    succeeded_session.rollback.assert_awaited_once()

    processing, processing_session, _, _, processing_version = make_service(
        tmp_path / "second", status="processing"
    )
    processing_version.parse_started_at = datetime.now(UTC)
    assert not await processing.parse_version(processing_version.id)
    processing_session.rollback.assert_awaited_once()


async def test_stale_processing_is_taken_over(tmp_path: Path) -> None:
    service, _, _, _, version = make_service(tmp_path, status="processing")
    version.parse_started_at = datetime.now(UTC) - timedelta(hours=1)
    assert await service.parse_version(version.id)
    assert version.parse_status == "succeeded"


async def test_missing_storage_is_safe_failed_state(tmp_path: Path) -> None:
    service, _, repository, _, version = make_service(tmp_path)
    service.storage.resolve_relative(version.storage_path).unlink()

    assert not await service.parse_version(version.id)
    assert repository.failure == (
        "storage_unavailable",
        "Stored document is unavailable",
        False,
    )


async def test_scoped_status_chunks_and_manual_queue(tmp_path: Path) -> None:
    dispatcher = FakeDispatcher()
    service, _, repository, document, version = make_service(tmp_path, dispatcher=dispatcher)

    result = await service.request_parse(
        document.knowledge_base_id, document.id, version.id, force=False
    )
    page = await service.list_chunks(
        document.knowledge_base_id, document.id, version.id, offset=0, limit=20
    )

    assert result.queued
    assert dispatcher.calls == [(version.id, False)]
    assert page.items == [] and page.total == 0
    with pytest.raises(DocumentVersionNotFoundError):
        await service.get_status(uuid4(), document.id, version.id)


async def test_manual_queue_failure_is_preserved(tmp_path: Path) -> None:
    dispatcher = FakeDispatcher(DocumentParsingQueueError("offline"))
    service, _, _, document, version = make_service(tmp_path, dispatcher=dispatcher)
    with pytest.raises(DocumentParsingQueueError):
        await service.request_parse(
            document.knowledge_base_id, document.id, version.id, force=False
        )
