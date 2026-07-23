from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import UploadFile
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.indexing import QdrantGateway, VectorIndexError
from app.models.document import Document, DocumentVersion
from app.models.knowledge_base import KnowledgeBase
from app.repositories.document import DocumentRecord, DocumentRepository
from app.repositories.knowledge_base import KnowledgeBaseRepository
from app.schemas.document import DocumentImportAction
from app.services.document import DocumentService
from app.services.document_dispatcher import DocumentParsingDispatcher
from app.services.exceptions import (
    DocumentImportConflictError,
    DocumentNotFoundError,
    DocumentTooLargeError,
    EmptyDocumentError,
    KnowledgeBaseNotFoundError,
    UnsupportedDocumentTypeError,
)
from app.storage.local import LocalFileStorage


def upload(filename: str, content: bytes) -> UploadFile:
    return UploadFile(file=BytesIO(content), filename=filename)


class FakeKnowledgeBaseRepository:
    def __init__(self, *knowledge_base_ids: UUID) -> None:
        self.ids = set(knowledge_base_ids)

    async def get_by_id(self, knowledge_base_id: UUID) -> KnowledgeBase | None:
        if knowledge_base_id not in self.ids:
            return None
        return KnowledgeBase(id=knowledge_base_id, name="Test", description=None)


class FakeDocumentRepository:
    def __init__(self) -> None:
        self.documents: dict[UUID, Document] = {}
        self.versions: dict[UUID, list[DocumentVersion]] = {}
        self.fail_create_version = False

    async def create_document(self, document: Document) -> Document:
        now = datetime.now(UTC)
        document.created_at = now
        document.updated_at = now
        self.documents[document.id] = document
        self.versions[document.id] = []
        return document

    async def create_version(self, version: DocumentVersion) -> DocumentVersion:
        if self.fail_create_version:
            raise IntegrityError("insert", {}, Exception("duplicate"))
        version.created_at = datetime.now(UTC)
        self.versions[version.document_id].append(version)
        return version

    async def get_document_by_id(
        self, knowledge_base_id: UUID, document_id: UUID
    ) -> Document | None:
        document = self.documents.get(document_id)
        return document if document and document.knowledge_base_id == knowledge_base_id else None

    async def get_document_by_normalized_name(
        self, knowledge_base_id: UUID, normalized_name: str
    ) -> Document | None:
        return next(
            (
                document
                for document in self.documents.values()
                if document.knowledge_base_id == knowledge_base_id
                and document.normalized_name == normalized_name
            ),
            None,
        )

    async def get_document_record(
        self, knowledge_base_id: UUID, document_id: UUID
    ) -> DocumentRecord | None:
        document = await self.get_document_by_id(knowledge_base_id, document_id)
        versions = self.versions.get(document_id, [])
        if document is None or not versions:
            return None
        return DocumentRecord(
            document, max(versions, key=lambda item: item.version_number), len(versions)
        )

    async def get_latest_version(
        self, knowledge_base_id: UUID, document_id: UUID
    ) -> DocumentVersion | None:
        if await self.get_document_by_id(knowledge_base_id, document_id) is None:
            return None
        versions = self.versions.get(document_id, [])
        return max(versions, key=lambda item: item.version_number) if versions else None

    async def get_version(
        self, knowledge_base_id: UUID, document_id: UUID, version_id: UUID
    ) -> DocumentVersion | None:
        if await self.get_document_by_id(knowledge_base_id, document_id) is None:
            return None
        return next(
            (version for version in self.versions.get(document_id, []) if version.id == version_id),
            None,
        )

    async def list_versions(
        self, knowledge_base_id: UUID, document_id: UUID
    ) -> list[DocumentVersion]:
        if await self.get_document_by_id(knowledge_base_id, document_id) is None:
            return []
        return sorted(
            self.versions.get(document_id, []), key=lambda item: item.version_number, reverse=True
        )

    async def list_documents(
        self,
        knowledge_base_id: UUID,
        *,
        offset: int,
        limit: int,
        query: str | None,
    ) -> list[DocumentRecord]:
        records = [
            record
            for document_id in self.documents
            if (record := await self.get_document_record(knowledge_base_id, document_id))
            and (query is None or query.casefold() in record.document.name.casefold())
        ]
        return records[offset : offset + limit]

    async def count_documents(self, knowledge_base_id: UUID, *, query: str | None) -> int:
        return len(
            await self.list_documents(knowledge_base_id, offset=0, limit=10_000, query=query)
        )

    async def touch_document(self, document: Document) -> None:
        document.updated_at = datetime.now(UTC)

    async def delete_document(self, document: Document) -> None:
        self.documents.pop(document.id)
        self.versions.pop(document.id)


class FakeDispatcher:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[UUID, bool]] = []
        self.error = error

    async def enqueue(self, document_version_id: UUID, *, force: bool = False) -> None:
        self.calls.append((document_version_id, force))
        if self.error is not None:
            raise self.error


class FakeIndexGateway:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.deleted: list[UUID] = []

    async def delete_document(self, document_id: UUID) -> None:
        self.deleted.append(document_id)
        if self.error is not None:
            raise self.error


def make_service(
    tmp_path: Path,
    knowledge_base_ids: tuple[UUID, ...] | None = None,
    *,
    max_size: int = 1024,
    dispatcher: FakeDispatcher | None = None,
    index_gateway: FakeIndexGateway | None = None,
) -> tuple[DocumentService, AsyncMock, FakeDocumentRepository, LocalFileStorage, UUID]:
    knowledge_base_id = knowledge_base_ids[0] if knowledge_base_ids else uuid4()
    ids = knowledge_base_ids or (knowledge_base_id,)
    session = AsyncMock(spec=AsyncSession)
    repository = FakeDocumentRepository()
    storage = LocalFileStorage(tmp_path / "uploads", max_size=max_size, chunk_size=2)
    service = DocumentService(
        cast(AsyncSession, session),
        storage,
        {".md", ".txt"},
        cast(DocumentRepository, repository),
        cast(KnowledgeBaseRepository, FakeKnowledgeBaseRepository(*ids)),
        parsing_dispatcher=cast(DocumentParsingDispatcher, dispatcher) if dispatcher else None,
        index_gateway=cast(QdrantGateway, index_gateway) if index_gateway else None,
    )
    return service, session, repository, storage, knowledge_base_id


async def test_first_upload_creates_document_and_version_one(tmp_path: Path) -> None:
    service, session, _, storage, knowledge_base_id = make_service(tmp_path)

    result = await service.import_document(knowledge_base_id, upload("说明.MD", b"first"))

    assert result.action is DocumentImportAction.created
    assert result.record.version_count == 1
    assert result.record.latest_version.version_number == 1
    assert (
        storage.resolve_relative(result.record.latest_version.storage_path).read_bytes() == b"first"
    )
    session.commit.assert_awaited_once()


async def test_same_name_and_hash_is_unchanged(tmp_path: Path) -> None:
    service, session, _, storage, knowledge_base_id = make_service(tmp_path)
    first = await service.import_document(knowledge_base_id, upload("sample.md", b"same"))
    session.reset_mock()

    result = await service.import_document(knowledge_base_id, upload("SAMPLE.MD", b"same"))

    assert result.action is DocumentImportAction.unchanged
    assert result.record.document.id == first.record.document.id
    assert result.record.version_count == 1
    assert list(storage.temp_root.iterdir()) == []
    session.commit.assert_not_awaited()


async def test_same_name_changed_hash_creates_version_two(tmp_path: Path) -> None:
    service, _, _, storage, knowledge_base_id = make_service(tmp_path)
    first = await service.import_document(knowledge_base_id, upload("sample.md", b"one"))

    result = await service.import_document(knowledge_base_id, upload("sample.md", b"two"))

    assert result.action is DocumentImportAction.version_created
    assert result.record.version_count == 2
    assert result.record.latest_version.version_number == 2
    assert storage.resolve_relative(first.record.latest_version.storage_path).read_bytes() == b"one"


async def test_different_names_with_same_hash_create_different_documents(tmp_path: Path) -> None:
    service, _, _, _, knowledge_base_id = make_service(tmp_path)
    first = await service.import_document(knowledge_base_id, upload("one.md", b"same"))
    second = await service.import_document(knowledge_base_id, upload("two.md", b"same"))

    assert first.record.document.id != second.record.document.id


async def test_missing_knowledge_base_is_rejected(tmp_path: Path) -> None:
    service, _, _, _, _ = make_service(tmp_path)
    with pytest.raises(KnowledgeBaseNotFoundError):
        await service.import_document(uuid4(), upload("sample.md", b"content"))


@pytest.mark.parametrize(
    ("filename", "content", "error"),
    [
        ("sample.exe", b"data", UnsupportedDocumentTypeError),
        ("empty.md", b"", EmptyDocumentError),
        ("large.md", b"12345", DocumentTooLargeError),
    ],
)
async def test_upload_validation_errors(
    tmp_path: Path, filename: str, content: bytes, error: type[Exception]
) -> None:
    service, _, _, _, knowledge_base_id = make_service(tmp_path, max_size=4)
    with pytest.raises(error):
        await service.import_document(knowledge_base_id, upload(filename, content))


async def test_missing_and_cross_knowledge_base_document_are_rejected(tmp_path: Path) -> None:
    first_id, second_id = uuid4(), uuid4()
    service, _, _, _, _ = make_service(tmp_path, (first_id, second_id))
    imported = await service.import_document(first_id, upload("sample.md", b"content"))

    with pytest.raises(DocumentNotFoundError):
        await service.get_document(first_id, uuid4())
    with pytest.raises(DocumentNotFoundError):
        await service.get_document(second_id, imported.record.document.id)


async def test_commit_failure_rolls_back_and_removes_final_file(tmp_path: Path) -> None:
    service, session, _, storage, knowledge_base_id = make_service(tmp_path)
    session.commit.side_effect = OperationalError("commit", {}, Exception("offline"))

    with pytest.raises(OperationalError):
        await service.import_document(knowledge_base_id, upload("sample.md", b"content"))

    session.rollback.assert_awaited_once()
    assert list(storage.root.glob("*/*/*/content.*")) == []
    assert list(storage.temp_root.iterdir()) == []


async def test_unique_race_rolls_back_and_cleans_temporary_file(tmp_path: Path) -> None:
    service, session, repository, storage, knowledge_base_id = make_service(tmp_path)
    repository.fail_create_version = True

    with pytest.raises(DocumentImportConflictError):
        await service.import_document(knowledge_base_id, upload("sample.md", b"content"))

    session.rollback.assert_awaited_once()
    assert list(storage.temp_root.iterdir()) == []


async def test_download_current_and_historical_versions(tmp_path: Path) -> None:
    service, _, repository, _, knowledge_base_id = make_service(tmp_path)
    first = await service.import_document(knowledge_base_id, upload("sample.md", b"one"))
    await service.import_document(knowledge_base_id, upload("sample.md", b"two"))

    current = await service.download_current(knowledge_base_id, first.record.document.id)
    historical = await service.download_version(
        knowledge_base_id,
        first.record.document.id,
        repository.versions[first.record.document.id][0].id,
    )

    assert current.path.read_bytes() == b"two"
    assert historical.path.read_bytes() == b"one"


async def test_delete_success_removes_database_and_files(tmp_path: Path) -> None:
    service, session, repository, storage, knowledge_base_id = make_service(tmp_path)
    imported = await service.import_document(knowledge_base_id, upload("sample.md", b"content"))
    session.reset_mock()

    await service.delete_document(knowledge_base_id, imported.record.document.id)

    assert imported.record.document.id not in repository.documents
    assert not (storage.root / str(knowledge_base_id) / str(imported.record.document.id)).exists()
    assert list(storage.trash_root.iterdir()) == []


async def test_delete_qdrant_failure_does_not_restore_document(tmp_path: Path) -> None:
    gateway = FakeIndexGateway(VectorIndexError("offline"))
    service, _, repository, storage, knowledge_base_id = make_service(
        tmp_path, index_gateway=gateway
    )
    imported = await service.import_document(knowledge_base_id, upload("sample.md", b"content"))
    document_id = imported.record.document.id

    await service.delete_document(knowledge_base_id, document_id)

    assert gateway.deleted == [document_id]
    assert document_id not in repository.documents
    assert not (storage.root / str(knowledge_base_id) / str(document_id)).exists()


async def test_delete_database_failure_restores_directory(tmp_path: Path) -> None:
    service, session, _, storage, knowledge_base_id = make_service(tmp_path)
    imported = await service.import_document(knowledge_base_id, upload("sample.md", b"content"))
    session.reset_mock()
    session.commit.side_effect = OperationalError("commit", {}, Exception("offline"))

    with pytest.raises(OperationalError):
        await service.delete_document(knowledge_base_id, imported.record.document.id)

    assert storage.resolve_relative(imported.record.latest_version.storage_path).exists()
    session.rollback.assert_awaited_once()


async def test_created_version_is_queued_after_commit(tmp_path: Path) -> None:
    dispatcher = FakeDispatcher()
    service, session, _, _, knowledge_base_id = make_service(tmp_path, dispatcher=dispatcher)

    result = await service.import_document(knowledge_base_id, upload("sample.md", b"content"))

    assert result.parsing_queued
    assert dispatcher.calls == [(result.record.latest_version.id, False)]
    session.commit.assert_awaited_once()


async def test_queue_failure_does_not_rollback_saved_upload(tmp_path: Path) -> None:
    dispatcher = FakeDispatcher(RuntimeError("redis offline"))
    service, session, _, storage, knowledge_base_id = make_service(tmp_path, dispatcher=dispatcher)

    result = await service.import_document(knowledge_base_id, upload("sample.md", b"content"))

    assert not result.parsing_queued
    assert storage.resolve_relative(result.record.latest_version.storage_path).is_file()
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


async def test_unchanged_pending_requeues_but_succeeded_does_not(tmp_path: Path) -> None:
    dispatcher = FakeDispatcher()
    service, _, _, _, knowledge_base_id = make_service(tmp_path, dispatcher=dispatcher)
    first = await service.import_document(knowledge_base_id, upload("sample.md", b"same"))
    dispatcher.calls.clear()

    pending = await service.import_document(knowledge_base_id, upload("sample.md", b"same"))
    assert pending.parsing_queued
    assert dispatcher.calls == [(first.record.latest_version.id, False)]

    first.record.latest_version.parse_status = "succeeded"
    dispatcher.calls.clear()
    succeeded = await service.import_document(knowledge_base_id, upload("sample.md", b"same"))
    assert not succeeded.parsing_queued
    assert dispatcher.calls == []
