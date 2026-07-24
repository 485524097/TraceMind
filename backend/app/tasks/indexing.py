import asyncio
from typing import Protocol, cast
from uuid import UUID

from app.core.config import get_settings
from app.db.session import Database
from app.embedding import SentenceTransformerEmbeddingProvider
from app.indexing import QdrantGateway
from app.integrations.qdrant import QdrantClient
from app.services.document_indexing import DocumentIndexingService
from app.worker.celery_app import celery_app


class IndexDocumentTask(Protocol):
    def __call__(self, document_version_id: str, force: bool = False) -> bool: ...

    def delay(self, document_version_id: str, *, force: bool = False) -> object: ...


def _execute_index_document_version(document_version_id: str, force: bool = False) -> bool:
    return asyncio.run(_index_document_version(UUID(document_version_id), force=force))


index_document_version = cast(
    IndexDocumentTask,
    celery_app.task(name="app.tasks.indexing.index_document_version")(
        _execute_index_document_version
    ),
)


async def _index_document_version(document_version_id: UUID, *, force: bool) -> bool:
    settings = get_settings()
    database = Database(settings)
    qdrant = QdrantClient(settings)
    provider = SentenceTransformerEmbeddingProvider(
        settings.embedding_model_name,
        settings.embedding_dimension,
        settings.embedding_batch_size,
        settings.embedding_device,
    )
    gateway = QdrantGateway(
        qdrant.client,
        collection_name=settings.qdrant_collection_name,
        vector_name=settings.qdrant_dense_vector_name,
        sparse_vector_name=settings.qdrant_sparse_vector_name,
        bm25_model=settings.qdrant_bm25_model,
        bm25_tokenizer=settings.qdrant_bm25_tokenizer,
        bm25_language=settings.qdrant_bm25_language,
        dimension=settings.embedding_dimension,
        upsert_batch_size=settings.qdrant_upsert_batch_size,
        dense_prefetch_limit=settings.hybrid_dense_prefetch_limit,
        sparse_prefetch_limit=settings.hybrid_sparse_prefetch_limit,
    )
    try:
        async with database.session_factory() as session:
            service = DocumentIndexingService(session, settings, provider, gateway)
            return await service.index_version(document_version_id, force=force)
    finally:
        await qdrant.close()
        await database.close()
