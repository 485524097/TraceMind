import os
from uuid import uuid4

import pytest
from qdrant_client import AsyncQdrantClient

from app.indexing import QdrantGateway, VectorPoint

TEST_QDRANT_URL = os.getenv("TEST_QDRANT_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not TEST_QDRANT_URL, reason="TEST_QDRANT_URL is not configured"),
]


async def test_rebuild_upsert_is_idempotent_and_historical_generation_is_not_retrieved() -> None:
    assert TEST_QDRANT_URL is not None
    client = AsyncQdrantClient(
        url=TEST_QDRANT_URL,
        check_compatibility=False,
        trust_env=False,
    )
    collection = f"tracemind_stage17_rebuild_{uuid4().hex}"
    gateway = QdrantGateway(
        client,
        collection_name=collection,
        vector_name="dense_v1",
        sparse_vector_name="bm25_v1",
        bm25_model="qdrant/bm25",
        bm25_tokenizer="multilingual",
        bm25_language="none",
        dimension=4,
        upsert_batch_size=64,
        dense_prefetch_limit=20,
        sparse_prefetch_limit=20,
    )
    knowledge_base_id, document_id = uuid4(), uuid4()
    historical_version_id, latest_version_id = uuid4(), uuid4()
    historical_generation, latest_generation = uuid4(), uuid4()
    historical_point = VectorPoint(
        id=uuid4(),
        dense_vector=[1.0, 0.0, 0.0, 0.0],
        sparse_text="historical version forbidden retrieval",
        payload={
            "source_type": "document",
            "knowledge_base_id": str(knowledge_base_id),
            "document_id": str(document_id),
            "document_version_id": str(historical_version_id),
            "chunk_id": str(uuid4()),
            "index_generation": str(historical_generation),
            "document_name": "history.md",
            "relative_path": "history.md",
            "version_number": 1,
            "chunk_index": 0,
            "content": "historical version forbidden retrieval",
            "content_hash": "a" * 64,
            "chunk_type": "paragraph",
            "language": None,
        },
    )
    latest_point = VectorPoint(
        id=uuid4(),
        dense_vector=[1.0, 0.0, 0.0, 0.0],
        sparse_text="latest version active retrieval",
        payload={
            "source_type": "document",
            "knowledge_base_id": str(knowledge_base_id),
            "document_id": str(document_id),
            "document_version_id": str(latest_version_id),
            "chunk_id": str(uuid4()),
            "index_generation": str(latest_generation),
            "document_name": "history.md",
            "relative_path": "history.md",
            "version_number": 2,
            "chunk_index": 0,
            "content": "latest version active retrieval",
            "content_hash": "b" * 64,
            "chunk_type": "paragraph",
            "language": None,
        },
    )
    try:
        await gateway.ensure_collection()
        await gateway.upsert([historical_point, latest_point])
        assert await gateway.count_generation(latest_generation) == 1

        await gateway.upsert([latest_point])
        assert await gateway.count_generation(latest_generation) == 1

        hits = await gateway.hybrid_search(
            [1.0, 0.0, 0.0, 0.0],
            "version retrieval",
            knowledge_base_id=knowledge_base_id,
            generations=[latest_generation],
            limit=5,
            language=None,
            document_id=document_id,
            dense_score_threshold=0.5,
            excluded_chunk_types=("heading",),
        )
        assert hits
        assert {hit.payload["document_version_id"] for hit in hits} == {str(latest_version_id)}
        assert all(hit.payload["document_version_id"] != str(historical_version_id) for hit in hits)
    finally:
        try:
            if await client.collection_exists(collection):
                await client.delete_collection(collection)
        finally:
            await client.close()
