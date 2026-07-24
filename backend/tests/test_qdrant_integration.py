import os
from uuid import uuid4

import pytest
from qdrant_client import AsyncQdrantClient, models

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
        sparse_vector_name="bm25_v1",
        bm25_model="qdrant/bm25",
        bm25_tokenizer="multilingual",
        bm25_language="none",
        dimension=4,
        upsert_batch_size=64,
        dense_prefetch_limit=20,
        sparse_prefetch_limit=20,
    )
    knowledge_base_id, document_id, version_id = uuid4(), uuid4(), uuid4()
    generation = uuid4()
    paragraph_chunk_id = uuid4()
    probe_texts = [
        "Nacos 标题",
        "Nacos 提供配置中心能力。",
        "使用 spring.cloud.nacos.discovery.server-addr 配置服务地址。",
        "DiscoveryClient 可以发现 Nacos 服务。",
        "Redis 用于缓存。",
        "PostgreSQL 支持事务。",
    ]
    points = [
        VectorPoint(
            uuid4(),
            (
                [1.0, 0.0, 0.0, 0.0]
                if index < 2
                else [0.0, 1.0, 0.0, 0.0]
                if index == 2
                else [0.0, 0.0, 1.0, 0.0]
                if index == 3
                else [0.0, 0.0, 0.0, 1.0]
            ),
            probe_texts[index] if index < len(probe_texts) else f"noise {index}",
            {
                "knowledge_base_id": str(knowledge_base_id),
                "document_id": str(document_id),
                "document_version_id": str(version_id),
                "chunk_id": str(paragraph_chunk_id if index == 1 else uuid4()),
                "index_generation": str(generation),
                "document_name": "integration.md",
                "version_number": 1,
                "chunk_index": index,
                "content": (probe_texts[index] if index < len(probe_texts) else f"noise {index}"),
                "content_hash": "a" * 64,
                "chunk_type": "heading" if index == 0 else "paragraph",
                "language": "markdown",
                "section_title": "Search",
                "page_number": None,
                "start_line": index + 1,
                "end_line": index + 1,
                "active": index != 3,
            },
        )
        for index in range(65)
    ]
    try:
        await gateway.ensure_collection()
        await gateway.upsert(points)
        assert await gateway.count_generation(generation) == 65

        hits = await gateway.search(
            [1.0, 0.0, 0.0, 0.0],
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

        chinese = await gateway.hybrid_search(
            [1.0, 0.0, 0.0, 0.0],
            "配置中心",
            knowledge_base_id=knowledge_base_id,
            generations=[generation],
            limit=5,
            language="markdown",
            document_id=document_id,
            dense_score_threshold=0.5,
            excluded_chunk_types=("heading",),
        )
        assert chinese[0].payload["chunk_index"] == 1

        exact = await gateway.hybrid_search(
            [0.0, 1.0, 0.0, 0.0],
            "spring.cloud.nacos.discovery.server-addr",
            knowledge_base_id=knowledge_base_id,
            generations=[generation],
            limit=5,
            language="markdown",
            document_id=document_id,
            dense_score_threshold=0.5,
            excluded_chunk_types=("heading",),
        )
        assert exact[0].payload["chunk_index"] == 2
        assert len({item.payload["chunk_id"] for item in exact}) == len(exact)

        discovery = await gateway.hybrid_search(
            [0.0, 0.0, 1.0, 0.0],
            "DiscoveryClient",
            knowledge_base_id=knowledge_base_id,
            generations=[generation],
            limit=5,
            language="markdown",
            document_id=document_id,
            dense_score_threshold=0.5,
            excluded_chunk_types=("heading",),
        )
        assert discovery[0].payload["chunk_index"] == 3

        dense_only_id = uuid4()
        await client.upsert(
            collection,
            points=[
                models.PointStruct(
                    id=dense_only_id,
                    vector={"dense_v1": [1.0, 0.0, 0.0, 0.0]},
                    payload={
                        **points[1].payload,
                        "chunk_id": str(uuid4()),
                        "chunk_index": 100,
                        "content": "Stage 7 dense-only point",
                    },
                )
            ],
            wait=True,
        )
        compatible = await gateway.hybrid_search(
            [1.0, 0.0, 0.0, 0.0],
            "nacos的作用",
            knowledge_base_id=knowledge_base_id,
            generations=[generation],
            limit=10,
            language="markdown",
            document_id=document_id,
            dense_score_threshold=0.5,
            excluded_chunk_types=("heading",),
        )
        assert any(item.payload["chunk_index"] == 100 for item in compatible)

        active_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="active",
                    match=models.MatchValue(value=True),
                )
            ]
        )
        filtered = await client.query_points(
            collection,
            prefetch=[
                models.Prefetch(
                    query=[0.0, 0.0, 1.0, 0.0],
                    using="dense_v1",
                    filter=active_filter,
                    limit=10,
                ),
                models.Prefetch(
                    query=models.Document(
                        text="DiscoveryClient",
                        model="qdrant/bm25",
                        options={"language": "none", "tokenizer": "multilingual"},
                    ),
                    using="bm25_v1",
                    filter=active_filter,
                    limit=10,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=10,
            with_payload=True,
        )
        assert all(point.payload and point.payload["active"] is True for point in filtered.points)

        await gateway.delete_document(document_id)
        assert await gateway.count_generation(generation) == 0
    finally:
        try:
            if await client.collection_exists(collection):
                await client.delete_collection(collection)
        finally:
            await client.close()


async def test_real_qdrant_online_sparse_schema_upgrade() -> None:
    assert TEST_QDRANT_URL is not None
    client = AsyncQdrantClient(
        url=TEST_QDRANT_URL,
        check_compatibility=False,
        trust_env=False,
    )
    collection = f"tracemind_sparse_upgrade_test_{uuid4().hex}"
    dense_only_id = uuid4()
    gateway = QdrantGateway(
        client,
        collection_name=collection,
        vector_name="dense_v1",
        sparse_vector_name="bm25_v1",
        bm25_model="qdrant/bm25",
        bm25_tokenizer="multilingual",
        bm25_language="none",
        dimension=3,
        upsert_batch_size=64,
        dense_prefetch_limit=20,
        sparse_prefetch_limit=20,
    )
    try:
        await client.create_collection(
            collection,
            vectors_config={
                "dense_v1": models.VectorParams(
                    size=3,
                    distance=models.Distance.COSINE,
                )
            },
        )
        await client.upsert(
            collection,
            points=[
                models.PointStruct(
                    id=dense_only_id,
                    vector={"dense_v1": [1.0, 0.0, 0.0]},
                    payload={"content": "existing Stage 7 point"},
                )
            ],
            wait=True,
        )

        await gateway.ensure_collection()

        info = await client.get_collection(collection)
        sparse_vectors = info.config.params.sparse_vectors or {}
        assert sparse_vectors["bm25_v1"].modifier == models.Modifier.IDF
        existing = await client.retrieve(
            collection,
            ids=[dense_only_id],
            with_payload=True,
            with_vectors=True,
        )
        assert len(existing) == 1
        assert existing[0].payload == {"content": "existing Stage 7 point"}
        assert existing[0].vector == {"dense_v1": [1.0, 0.0, 0.0]}
    finally:
        try:
            if await client.collection_exists(collection):
                await client.delete_collection(collection)
        finally:
            await client.close()
