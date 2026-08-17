import asyncio
from typing import Protocol, cast
from uuid import UUID

from app.core.config import Settings, get_settings
from app.db.session import Database
from app.embedding import SentenceTransformerEmbeddingProvider
from app.indexing import QdrantGateway
from app.indexing.factory import build_qdrant_gateway
from app.integrations.qdrant import QdrantClient
from app.services.knowledge_entry_indexing import KnowledgeEntryIndexingService
from app.worker.celery_app import celery_app


class SyncKnowledgeEntryIndexTask(Protocol):
    def __call__(self, entry_id: str, force: bool = False) -> bool: ...

    def delay(self, entry_id: str, *, force: bool = False) -> object: ...


class DeleteKnowledgeEntryIndexTask(Protocol):
    def __call__(self, entry_id: str) -> bool: ...

    def delay(self, entry_id: str) -> object: ...


def _gateway(settings: Settings, qdrant: QdrantClient) -> QdrantGateway:
    return build_qdrant_gateway(settings, qdrant.client)


def _execute_sync(entry_id: str, force: bool = False) -> bool:
    return asyncio.run(_sync(UUID(entry_id), force=force))


def _execute_delete(entry_id: str) -> bool:
    return asyncio.run(_delete(UUID(entry_id)))


sync_knowledge_entry_index = cast(
    SyncKnowledgeEntryIndexTask,
    celery_app.task(name="app.tasks.knowledge_entries.sync_knowledge_entry_index")(_execute_sync),
)
delete_knowledge_entry_index = cast(
    DeleteKnowledgeEntryIndexTask,
    celery_app.task(name="app.tasks.knowledge_entries.delete_knowledge_entry_index")(
        _execute_delete
    ),
)


async def _sync(entry_id: UUID, *, force: bool) -> bool:
    settings = get_settings()
    database = Database(settings)
    qdrant = QdrantClient(settings)
    provider = SentenceTransformerEmbeddingProvider(
        settings.embedding_model_name,
        settings.embedding_dimension,
        settings.embedding_batch_size,
        settings.resolved_index_embedding_device,
    )
    try:
        async with database.session_factory() as session:
            service = KnowledgeEntryIndexingService(
                session, settings, provider, _gateway(settings, qdrant)
            )
            return await service.sync_entry(entry_id, force=force)
    finally:
        await qdrant.close()
        await database.close()


async def _delete(entry_id: UUID) -> bool:
    settings = get_settings()
    qdrant = QdrantClient(settings)
    try:
        gateway = _gateway(settings, qdrant)
        await gateway.ensure_collection()
        await gateway.delete_knowledge_entry(entry_id)
        return True
    finally:
        await qdrant.close()
