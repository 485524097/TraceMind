import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentVersion
from app.repositories.document import DocumentRecord, DocumentRepository
from app.repositories.knowledge_base import KnowledgeBaseRepository
from app.schemas.document import DocumentImportAction
from app.services.document_dispatcher import DocumentParsingDispatcher
from app.services.exceptions import (
    DocumentImportConflictError,
    DocumentNotFoundError,
    DocumentStorageError,
    KnowledgeBaseNotFoundError,
)
from app.storage.local import LocalFileStorage
from app.storage.names import normalize_document_name

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocumentImportResult:
    action: DocumentImportAction
    record: DocumentRecord
    parsing_queued: bool = False


@dataclass(frozen=True)
class DocumentDownload:
    path: Path
    filename: str
    mime_type: str | None


class DocumentService:
    def __init__(
        self,
        session: AsyncSession,
        storage: LocalFileStorage,
        allowed_extensions: set[str],
        document_repository: DocumentRepository | None = None,
        knowledge_base_repository: KnowledgeBaseRepository | None = None,
        parsing_dispatcher: DocumentParsingDispatcher | None = None,
    ) -> None:
        self.session = session
        self.storage = storage
        self.allowed_extensions = allowed_extensions
        self.repository = document_repository or DocumentRepository(session)
        self.knowledge_bases = knowledge_base_repository or KnowledgeBaseRepository(session)
        self.parsing_dispatcher = parsing_dispatcher

    async def import_document(
        self, knowledge_base_id: UUID, upload: UploadFile
    ) -> DocumentImportResult:
        await self._require_knowledge_base(knowledge_base_id)
        safe_name = normalize_document_name(upload.filename, self.allowed_extensions)
        temporary = await self.storage.write_upload(upload)
        finalized_path: str | None = None
        try:
            document = await self.repository.get_document_by_normalized_name(
                knowledge_base_id, safe_name.normalized_name
            )
            if document is not None:
                current = await self.repository.get_latest_version(knowledge_base_id, document.id)
                if current is None:
                    raise DocumentImportConflictError("Document has no current version")
                if current.content_hash == temporary.content_hash:
                    await self.storage.discard_temporary(temporary.path)
                    record = await self._require_record(knowledge_base_id, document.id)
                    queued = await self._enqueue_if_needed(current)
                    return DocumentImportResult(DocumentImportAction.unchanged, record, queued)
                version_number = current.version_number + 1
                action = DocumentImportAction.version_created
            else:
                document = Document(
                    id=uuid4(),
                    knowledge_base_id=knowledge_base_id,
                    name=safe_name.display_name,
                    normalized_name=safe_name.normalized_name,
                    source_type="upload",
                )
                await self.repository.create_document(document)
                version_number = 1
                action = DocumentImportAction.created

            version = DocumentVersion(
                id=uuid4(),
                document_id=document.id,
                version_number=version_number,
                content_hash=temporary.content_hash,
                file_size=temporary.file_size,
                mime_type=upload.content_type,
                extension=safe_name.extension,
                storage_path="",
                parse_status="pending",
                chunk_count=0,
            )
            version.storage_path = self.storage.final_relative_path(
                knowledge_base_id, document.id, version.id, safe_name.extension
            )
            await self.repository.create_version(version)
            if action is DocumentImportAction.version_created:
                document.name = safe_name.display_name
                await self.repository.touch_document(document)
            await self.storage.finalize(temporary.path, version.storage_path)
            finalized_path = version.storage_path
            await self.session.commit()
            record = DocumentRecord(
                document=document,
                latest_version=version,
                version_count=version_number,
            )
            queued = await self._enqueue_if_needed(version)
            return DocumentImportResult(action, record, queued)
        except IntegrityError as exc:
            await self.session.rollback()
            await self._clean_failed_upload(temporary.path, finalized_path)
            raise DocumentImportConflictError("Concurrent document import conflict") from exc
        except SQLAlchemyError:
            await self.session.rollback()
            await self._clean_failed_upload(temporary.path, finalized_path)
            raise
        except Exception:
            await self.session.rollback()
            await self._clean_failed_upload(temporary.path, finalized_path)
            raise

    async def list_documents(
        self,
        knowledge_base_id: UUID,
        *,
        offset: int,
        limit: int,
        query: str | None,
    ) -> tuple[list[DocumentRecord], int]:
        await self._require_knowledge_base(knowledge_base_id)
        normalized_query = query.strip() if query and query.strip() else None
        items = await self.repository.list_documents(
            knowledge_base_id, offset=offset, limit=limit, query=normalized_query
        )
        total = await self.repository.count_documents(knowledge_base_id, query=normalized_query)
        return items, total

    async def get_document(self, knowledge_base_id: UUID, document_id: UUID) -> DocumentRecord:
        await self._require_knowledge_base(knowledge_base_id)
        return await self._require_record(knowledge_base_id, document_id)

    async def list_versions(
        self, knowledge_base_id: UUID, document_id: UUID
    ) -> list[DocumentVersion]:
        await self.get_document(knowledge_base_id, document_id)
        return await self.repository.list_versions(knowledge_base_id, document_id)

    async def download_current(
        self, knowledge_base_id: UUID, document_id: UUID
    ) -> DocumentDownload:
        record = await self.get_document(knowledge_base_id, document_id)
        return self._download(record.document.name, record.latest_version)

    async def download_version(
        self, knowledge_base_id: UUID, document_id: UUID, version_id: UUID
    ) -> DocumentDownload:
        record = await self.get_document(knowledge_base_id, document_id)
        version = await self.repository.get_version(knowledge_base_id, document_id, version_id)
        if version is None:
            raise DocumentNotFoundError("Document version was not found")
        return self._download(record.document.name, version)

    async def delete_document(self, knowledge_base_id: UUID, document_id: UUID) -> None:
        document = await self.repository.get_document_by_id(knowledge_base_id, document_id)
        if document is None:
            raise DocumentNotFoundError("Document was not found")
        staged = await self.storage.stage_document_deletion(knowledge_base_id, document_id)
        try:
            await self.repository.delete_document(document)
            await self.session.commit()
        except SQLAlchemyError:
            await self.session.rollback()
            await self.storage.restore_staged(staged)
            raise
        try:
            await self.storage.purge_staged(staged)
        except DocumentStorageError:
            logger.warning("A staged document directory requires manual cleanup")

    async def _require_knowledge_base(self, knowledge_base_id: UUID) -> None:
        if await self.knowledge_bases.get_by_id(knowledge_base_id) is None:
            raise KnowledgeBaseNotFoundError(knowledge_base_id)

    async def _require_record(self, knowledge_base_id: UUID, document_id: UUID) -> DocumentRecord:
        record = await self.repository.get_document_record(knowledge_base_id, document_id)
        if record is None:
            raise DocumentNotFoundError("Document was not found")
        return record

    def _download(self, filename: str, version: DocumentVersion) -> DocumentDownload:
        path = self.storage.resolve_relative(version.storage_path)
        return DocumentDownload(path=path, filename=filename, mime_type=version.mime_type)

    async def _clean_failed_upload(self, temporary_path: Path, finalized_path: str | None) -> None:
        if finalized_path is not None:
            await self.storage.remove_final_version(finalized_path)
        else:
            await self.storage.discard_temporary(temporary_path)

    async def _enqueue_if_needed(self, version: DocumentVersion) -> bool:
        if self.parsing_dispatcher is None or version.parse_status not in {"pending", "failed"}:
            return False
        try:
            await self.parsing_dispatcher.enqueue(version.id)
        except Exception:
            logger.warning("Document version was saved but parsing could not be queued")
            return False
        return True
