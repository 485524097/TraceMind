from qdrant_client import AsyncQdrantClient

from app.core.config import Settings
from app.indexing.qdrant import QdrantGateway


def build_qdrant_gateway(settings: Settings, client: AsyncQdrantClient) -> QdrantGateway:
    return QdrantGateway(
        client,
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
