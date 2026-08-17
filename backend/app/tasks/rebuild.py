import asyncio
from typing import Protocol, cast
from uuid import UUID

from app.core.config import get_settings
from app.db.session import Database
from app.embedding import SentenceTransformerEmbeddingProvider
from app.indexing.factory import build_qdrant_gateway
from app.integrations.qdrant import QdrantClient
from app.services.document_indexing import DocumentIndexingService
from app.services.document_parsing import DocumentParsingService
from app.services.knowledge_base_rebuild import KnowledgeBaseRebuildExecutor
from app.services.knowledge_entry_indexing import KnowledgeEntryIndexingService
from app.storage.local import LocalFileStorage
from app.worker.celery_app import celery_app


class RebuildKnowledgeBaseTask(Protocol):
    def __call__(self, operation_id: str, run_generation: str) -> bool: ...

    def delay(self, operation_id: str, run_generation: str) -> object: ...


def _execute_rebuild_knowledge_base(operation_id: str, run_generation: str) -> bool:
    return asyncio.run(_rebuild_knowledge_base(UUID(operation_id), UUID(run_generation)))


rebuild_knowledge_base = cast(
    RebuildKnowledgeBaseTask,
    celery_app.task(name="app.tasks.rebuild.rebuild_knowledge_base")(
        _execute_rebuild_knowledge_base
    ),
)


async def _rebuild_knowledge_base(operation_id: UUID, run_generation: UUID) -> bool:
    settings = get_settings()
    database = Database(settings)
    qdrant = QdrantClient(settings)
    storage = LocalFileStorage(
        settings.document_storage_root,
        max_size=settings.document_max_file_size_bytes,
        chunk_size=settings.document_upload_chunk_size_bytes,
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
            parser = DocumentParsingService(session, storage, settings)
            document_indexer = DocumentIndexingService(session, settings, provider, gateway)
            knowledge_entry_indexer = KnowledgeEntryIndexingService(
                session, settings, provider, gateway
            )
            executor = KnowledgeBaseRebuildExecutor(
                session,
                parser,
                document_indexer,
                knowledge_entry_indexer,
            )
            return await executor.run(operation_id, run_generation)
    finally:
        await qdrant.close()
        await database.close()
