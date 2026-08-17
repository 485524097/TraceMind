import asyncio
from typing import Protocol, cast
from uuid import UUID

from app.core.config import get_settings
from app.db.session import Database
from app.embedding import SentenceTransformerEmbeddingProvider
from app.indexing.factory import build_qdrant_gateway
from app.integrations.qdrant import QdrantClient
from app.repositories.consistency_audit import ConsistencyAuditRepository
from app.repositories.knowledge_base_restore_lock import RestoreAdvisoryLock
from app.services.consistency_audit import ConsistencyAuditService
from app.services.consistency_repair import ConsistencyRepairExecutor
from app.services.document_indexing import DocumentIndexingService
from app.services.document_parsing import DocumentParsingService
from app.services.knowledge_entry_indexing import KnowledgeEntryIndexingService
from app.storage.archive import LocalArchiveStorage, archive_limits_from_settings
from app.storage.local import LocalFileStorage
from app.worker.celery_app import celery_app


class RepairConsistencyTask(Protocol):
    def __call__(self, operation_id: str, run_generation: str) -> bool: ...
    def delay(self, operation_id: str, run_generation: str) -> object: ...


def _execute(operation_id: str, run_generation: str) -> bool:
    return asyncio.run(_run(UUID(operation_id), UUID(run_generation)))


repair_consistency_findings = cast(
    RepairConsistencyTask,
    celery_app.task(name="app.tasks.repair.repair_consistency_findings")(_execute),
)


async def _run(operation_id: UUID, run_generation: UUID) -> bool:
    settings = get_settings()
    database = Database(settings)
    qdrant = QdrantClient(settings)
    document_storage = LocalFileStorage(
        settings.document_storage_root,
        max_size=settings.document_max_file_size_bytes,
        chunk_size=settings.document_upload_chunk_size_bytes,
    )
    archive_storage = LocalArchiveStorage(
        settings.document_storage_root, archive_limits_from_settings(settings)
    )
    provider = SentenceTransformerEmbeddingProvider(
        settings.embedding_model_name,
        settings.embedding_dimension,
        settings.embedding_batch_size,
        settings.resolved_index_embedding_device,
    )
    gateway = build_qdrant_gateway(settings, qdrant.client)
    try:
        async with database.session_factory() as session:
            audit_repository = ConsistencyAuditRepository(session)
            audit_service = ConsistencyAuditService(
                settings, audit_repository, document_storage, archive_storage, gateway
            )
            executor = ConsistencyRepairExecutor(
                session,
                audit_service,
                DocumentParsingService(session, document_storage, settings),
                DocumentIndexingService(session, settings, provider, gateway),
                KnowledgeEntryIndexingService(session, settings, provider, gateway),
                gateway,
                archive_storage,
                audit_repository,
                stale_after_seconds=settings.consistency_repair_stale_after_seconds,
                restore_lock=RestoreAdvisoryLock(database.engine),
            )
            return await executor.run(operation_id, run_generation)
    finally:
        await qdrant.close()
        await database.close()
