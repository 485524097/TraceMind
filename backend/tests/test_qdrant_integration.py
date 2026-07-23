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
    paragraph_chunk_id = uuid4()
    points = [
        VectorPoint(
            uuid4(),
            [1.0, 0.0, 0.0] if index < 2 else [0.0, 1.0, 0.0],
            {
                "knowledge_base_id": str(knowledge_base_id),
                "document_id": str(document_id),
                "document_version_id": str(version_id),
                "chunk_id": str(paragraph_chunk_id if index == 1 else uuid4()),
                "index_generation": str(generation),
                "document_name": "integration.md",
                "version_number": 1,
                "chunk_index": index,
                "content": f"dense semantic search {index}",
                "content_hash": "a" * 64,
                "chunk_type": "heading" if index == 0 else "paragraph",
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
            score_threshold=0.5,
            excluded_chunk_types=("heading",),
        )
        assert len(hits) == 1
        assert hits[0].payload["chunk_id"] == str(paragraph_chunk_id)
        assert hits[0].payload["chunk_type"] == "paragraph"
        assert hits[0].payload["index_generation"] == str(generation)
        assert hits[0].payload["document_id"] == str(document_id)
        assert hits[0].payload["section_title"] == "Search"
        assert hits[0].payload["start_line"] == 2

        await gateway.delete_document(document_id)
        assert await gateway.count_generation(generation) == 0
    finally:
        await client.delete_collection(collection)
        await client.close()
