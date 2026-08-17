import logging
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.knowledge_base_rebuild import (
    KnowledgeBaseRebuildItem,
    KnowledgeBaseRebuildOperation,
)
from app.repositories.knowledge_base_rebuild import KnowledgeBaseRebuildRepository
from app.schemas.knowledge_base_rebuild import KnowledgeBaseRebuildResponse
from app.services.exceptions import (
    KnowledgeBaseNotFoundError,
    KnowledgeBaseRebuildAlreadyActiveError,
    KnowledgeBaseRebuildNotFoundError,
    KnowledgeBaseRebuildNotRetryableError,
)
from app.services.knowledge_base_rebuild_dispatcher import KnowledgeBaseRebuildDispatcher

logger = logging.getLogger(__name__)


class DocumentParsePipeline(Protocol):
    async def parse_version(
        self,
        version_id: UUID,
        *,
        force: bool = False,
        enqueue_index: bool = True,
    ) -> bool: ...


class DocumentIndexPipeline(Protocol):
    async def index_version(self, version_id: UUID, *, force: bool = False) -> bool: ...


class KnowledgeEntryIndexPipeline(Protocol):
    async def sync_entry(self, entry_id: UUID, *, force: bool = False) -> bool: ...


@dataclass(frozen=True)
class WorkResult:
    succeeded: bool
    error_code: str | None = None
    error_message: str | None = None


class KnowledgeBaseRebuildService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        dispatcher: KnowledgeBaseRebuildDispatcher,
        repository: KnowledgeBaseRebuildRepository | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.dispatcher = dispatcher
        self.repository = repository or KnowledgeBaseRebuildRepository(session)

    async def start(self, knowledge_base_id: UUID) -> KnowledgeBaseRebuildResponse:
        if not await self.repository.knowledge_base_exists(knowledge_base_id):
            raise KnowledgeBaseNotFoundError(knowledge_base_id)
        if await self.repository.get_active_operation(knowledge_base_id) is not None:
            raise KnowledgeBaseRebuildAlreadyActiveError(knowledge_base_id)
        try:
            operation = await self.repository.create_operation(knowledge_base_id)
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise KnowledgeBaseRebuildAlreadyActiveError(knowledge_base_id) from exc
        await self._dispatch(operation)
        return await self._response(operation)

    async def get_status(self, knowledge_base_id: UUID) -> KnowledgeBaseRebuildResponse:
        if not await self.repository.knowledge_base_exists(knowledge_base_id):
            raise KnowledgeBaseNotFoundError(knowledge_base_id)
        operation = await self.repository.get_latest_operation(knowledge_base_id)
        if operation is None:
            return KnowledgeBaseRebuildResponse(
                knowledge_base_id=knowledge_base_id,
                operation_id=None,
                status="not_started",
            )
        return await self._response(operation)

    async def retry(self, knowledge_base_id: UUID) -> KnowledgeBaseRebuildResponse:
        if not await self.repository.knowledge_base_exists(knowledge_base_id):
            raise KnowledgeBaseNotFoundError(knowledge_base_id)
        operation = await self.repository.get_latest_operation(knowledge_base_id)
        if operation is None:
            raise KnowledgeBaseRebuildNotFoundError(knowledge_base_id)
        operation, prepared = await self.repository.prepare_retry(
            operation.id,
            stale_after_seconds=self.settings.knowledge_base_rebuild_stale_after_seconds,
        )
        if not prepared:
            await self.session.rollback()
            if operation.status in {"queued", "running"}:
                raise KnowledgeBaseRebuildAlreadyActiveError(knowledge_base_id)
            raise KnowledgeBaseRebuildNotRetryableError(knowledge_base_id)
        await self.session.commit()
        await self._dispatch(operation)
        return await self._response(operation)

    async def _dispatch(self, operation: KnowledgeBaseRebuildOperation) -> None:
        try:
            await self.dispatcher.enqueue(operation.id, operation.run_generation)
        except Exception:
            logger.warning(
                "Knowledge Base rebuild could not be queued operation_id=%s",
                operation.id,
            )
            await self.repository.mark_queue_failed(
                operation.id,
                operation.run_generation,
                "Data was restored, but derived-state rebuild work could not be queued",
            )

    async def _response(
        self, operation: KnowledgeBaseRebuildOperation
    ) -> KnowledgeBaseRebuildResponse:
        counts = await self.repository.counts(operation.id)
        return KnowledgeBaseRebuildResponse(
            knowledge_base_id=operation.knowledge_base_id,
            operation_id=operation.id,
            status=operation.status,
            **counts.__dict__,
            started_at=operation.started_at,
            completed_at=operation.completed_at,
            error_code=operation.error_code,
            error_message=operation.error_message,
        )


class KnowledgeBaseRebuildExecutor:
    def __init__(
        self,
        session: AsyncSession,
        parser: DocumentParsePipeline,
        document_indexer: DocumentIndexPipeline,
        knowledge_entry_indexer: KnowledgeEntryIndexPipeline,
        repository: KnowledgeBaseRebuildRepository | None = None,
    ) -> None:
        self.session = session
        self.parser = parser
        self.document_indexer = document_indexer
        self.knowledge_entry_indexer = knowledge_entry_indexer
        self.repository = repository or KnowledgeBaseRebuildRepository(session)

    async def run(self, operation_id: UUID, run_generation: UUID) -> bool:
        if not await self.repository.claim_operation(operation_id, run_generation):
            return False
        items = await self.repository.list_retryable_items(operation_id)
        for item in items:
            if not await self.repository.mark_item_running(item.id, operation_id, run_generation):
                return False
            result = await self._process(item)
            if result.succeeded:
                owned = await self.repository.mark_item_succeeded(
                    item.id, operation_id, run_generation
                )
            else:
                owned = await self.repository.mark_item_failed(
                    item.id,
                    operation_id,
                    run_generation,
                    error_code=result.error_code or "rebuild_failed",
                    error_message=result.error_message or "Derived state could not be rebuilt",
                )
            if not owned:
                return False
        return await self.repository.finalize_operation(operation_id, run_generation)

    async def _process(self, item: KnowledgeBaseRebuildItem) -> WorkResult:
        try:
            if item.work_type == "document_parse":
                return await self._parse(item.target_id)
            if item.work_type == "document_index":
                return await self._index_document(item.target_id)
            if item.work_type == "knowledge_entry_index":
                return await self._index_knowledge_entry(item.target_id)
            return WorkResult(False, "unknown_work_type", "Rebuild work type is invalid")
        except Exception as exc:
            await self.session.rollback()
            logger.error(
                "Unexpected rebuild item failure item_id=%s work_type=%s (%s)",
                item.id,
                item.work_type,
                type(exc).__name__,
            )
            return WorkResult(False, "internal_rebuild_error", "Derived state could not be rebuilt")

    async def _parse(self, version_id: UUID) -> WorkResult:
        if await self.repository.document_parse_succeeded(version_id):
            return WorkResult(True)
        await self.parser.parse_version(version_id, enqueue_index=False)
        if await self.repository.document_parse_succeeded(version_id):
            return WorkResult(True)
        return WorkResult(False, "parse_failed", "Document version could not be parsed")

    async def _index_document(self, version_id: UUID) -> WorkResult:
        if not await self.repository.document_version_is_latest(version_id):
            return WorkResult(True)
        if not await self.repository.document_parse_succeeded(version_id):
            return WorkResult(
                False,
                "parse_dependency_failed",
                "Latest document version was not parsed",
            )
        if await self.repository.document_index_succeeded(version_id):
            return WorkResult(True)
        await self.document_indexer.index_version(version_id)
        if not await self.repository.document_version_is_latest(version_id):
            return WorkResult(True)
        if await self.repository.document_index_succeeded(version_id):
            return WorkResult(True)
        return WorkResult(False, "document_index_failed", "Document index could not be rebuilt")

    async def _index_knowledge_entry(self, entry_id: UUID) -> WorkResult:
        if await self.repository.knowledge_entry_index_succeeded(entry_id):
            return WorkResult(True)
        await self.knowledge_entry_indexer.sync_entry(entry_id)
        if await self.repository.knowledge_entry_index_succeeded(entry_id):
            return WorkResult(True)
        return WorkResult(
            False,
            "knowledge_entry_index_failed",
            "Verified knowledge index could not be rebuilt",
        )
