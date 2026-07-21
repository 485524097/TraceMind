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


async def test_real_qdrant_named_vector_payload_filter_and_cleanup() -> None:
    assert TEST_QDRANT_URL is not None
    client = AsyncQdrantClient(
        url=TEST_QDRANT_URL,
        check_compatibility=False,
        trust_env=False,
    )
    collection = f"tracemind_test_{uuid4().hex}"
    gateway = QdrantGateway(
        client,
        collection_name=collection,
        vector_name="dense_v1",
        dimension=3,
        upsert_batch_size=64,
    )
    knowledge_base_id, document_id, version_id = uuid4(), uuid4(), uuid4()
    generation = uuid4()
    points = [
        VectorPoint(
            uuid4(),
            [1.0, 0.0, 0.0],
            {
                "knowledge_base_id": str(knowledge_base_id),
                "document_id": str(document_id),
                "document_version_id": str(version_id),
                "chunk_id": str(uuid4()),
                "index_generation": str(generation),
                "document_name": "integration.md",
                "version_number": 1,
                "chunk_index": index,
                "content": f"dense semantic search {index}",
                "content_hash": "a" * 64,
                "chunk_type": "paragraph",
                "language": "markdown",
                "section_title": "Search",
                "page_number": None,
                "start_line": index + 1,
                "end_line": index + 1,
            },
        )
        for index in range(65)
    ]
    try:
        await gateway.ensure_collection()
        await gateway.upsert(points)
        assert await gateway.count_generation(generation) == 65

        hits = await gateway.search(
            [1.0, 0.0, 0.0],
            knowledge_base_id=knowledge_base_id,
            generations=[generation],
            limit=10,
            language="markdown",
            document_id=document_id,
        )
        assert hits
        assert all(hit.payload["index_generation"] == str(generation) for hit in hits)

        await gateway.delete_document(document_id)
        assert await gateway.count_generation(generation) == 0
    finally:
        await client.delete_collection(collection)
        await client.close()
