from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import Headers
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.indexing import (
    IncompatibleCollectionError,
    QdrantGateway,
    VectorIndexError,
    VectorPoint,
)


def gateway(
    client: AsyncMock,
    *,
    batch_size: int = 64,
    dense_prefetch_limit: int = 20,
    sparse_prefetch_limit: int = 20,
) -> QdrantGateway:
    return QdrantGateway(
        client,
        collection_name="tracemind_chunks",
        vector_name="dense_v1",
        sparse_vector_name="bm25_v1",
        bm25_model="qdrant/bm25",
        bm25_tokenizer="multilingual",
        bm25_language="none",
        dimension=3,
        upsert_batch_size=batch_size,
        dense_prefetch_limit=dense_prefetch_limit,
        sparse_prefetch_limit=sparse_prefetch_limit,
    )


def collection_info(
    *,
    size: int = 3,
    distance: models.Distance = models.Distance.COSINE,
    payload_schema: dict[str, object] | None = None,
    sparse: bool = True,
    sparse_modifier: models.Modifier | None = models.Modifier.IDF,
):
    vectors = {"dense_v1": models.VectorParams(size=size, distance=distance)}
    source_payload_schema = (
        {name: object() for name in QdrantGateway.payload_indexes}
        if payload_schema is None
        else payload_schema
    )
    typed_payload_schema = {
        name: (
            value
            if isinstance(value, models.PayloadIndexInfo)
            else models.PayloadIndexInfo(
                data_type=models.PayloadSchemaType.KEYWORD,
                points=0,
            )
        )
        for name, value in source_payload_schema.items()
    }
    return SimpleNamespace(
        config=SimpleNamespace(
            params=SimpleNamespace(
                vectors=vectors,
                sparse_vectors=(
                    {"bm25_v1": models.SparseVectorParams(modifier=sparse_modifier)}
                    if sparse
                    else {}
                ),
            )
        ),
        payload_schema=typed_payload_schema,
    )


async def test_collection_is_created_with_named_cosine_vector_and_payload_indexes() -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    client.collection_exists.return_value = False
    client.get_collection.side_effect = [
        collection_info(payload_schema={}),
        collection_info(),
    ]

    await gateway(client).ensure_collection()

    client.create_collection.assert_awaited_once()
    vectors = client.create_collection.await_args.kwargs["vectors_config"]
    assert vectors["dense_v1"].size == 3
    assert vectors["dense_v1"].distance == models.Distance.COSINE
    sparse = client.create_collection.await_args.kwargs["sparse_vectors_config"]
    assert sparse["bm25_v1"].modifier == models.Modifier.IDF
    assert client.create_payload_index.await_count == 7
    assert client.get_collection.await_count == 2


@pytest.mark.parametrize(
    ("size", "distance"),
    [(4, models.Distance.COSINE), (3, models.Distance.DOT)],
)
async def test_incompatible_collection_is_rejected_without_rebuild(
    size: int, distance: models.Distance
) -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    client.collection_exists.return_value = True
    client.get_collection.return_value = collection_info(size=size, distance=distance)

    with pytest.raises(IncompatibleCollectionError):
        await gateway(client).ensure_collection()

    client.delete_collection.assert_not_called()
    client.create_collection.assert_not_called()


async def test_concurrent_collection_creation_is_rechecked() -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    client.collection_exists.side_effect = [False, True]
    client.create_collection.side_effect = UnexpectedResponse(409, "Conflict", b"exists", Headers())
    client.get_collection.side_effect = [
        collection_info(payload_schema={}),
        collection_info(),
    ]

    await gateway(client).ensure_collection()

    assert client.get_collection.await_count == 2
    assert client.create_payload_index.await_count == 7


async def test_concurrent_payload_index_creation_is_rechecked() -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    existing = set(QdrantGateway.payload_indexes) - {"knowledge_base_id"}
    client.collection_exists.return_value = True
    client.get_collection.side_effect = [
        collection_info(payload_schema={name: object() for name in existing}),
        collection_info(payload_schema={name: object() for name in QdrantGateway.payload_indexes}),
    ]
    client.create_payload_index.side_effect = UnexpectedResponse(
        409, "Conflict", b"exists", Headers()
    )

    await gateway(client).ensure_collection()

    client.create_payload_index.assert_awaited_once()
    assert client.get_collection.await_count == 2


async def test_successful_payload_index_creation_must_be_visible_on_refresh() -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    existing = set(QdrantGateway.payload_indexes) - {"symbol_lookup_keys"}
    client.collection_exists.return_value = True
    client.get_collection.side_effect = [
        collection_info(payload_schema={name: object() for name in existing}),
        collection_info(payload_schema={name: object() for name in existing}),
    ]

    with pytest.raises(VectorIndexError, match="could not be verified"):
        await gateway(client).ensure_collection()

    client.create_payload_index.assert_awaited_once()
    assert client.get_collection.await_count == 2


async def test_collection_network_failure_is_not_treated_as_existing() -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    client.collection_exists.return_value = False
    client.create_collection.side_effect = RuntimeError("network unavailable")

    with pytest.raises(VectorIndexError, match="could not be prepared"):
        await gateway(client).ensure_collection()

    client.get_collection.assert_not_called()


def make_points(count: int) -> list[VectorPoint]:
    return [
        VectorPoint(
            uuid4(),
            [1.0, 0.0, 0.0],
            f"traceable {index}",
            {"knowledge_base_id": str(uuid4()), "content": f"traceable {index}"},
        )
        for index in range(count)
    ]


@pytest.mark.parametrize(
    ("count", "batch_lengths"),
    [(0, []), (1, [1]), (64, [64]), (65, [64, 1]), (130, [64, 64, 2])],
)
async def test_point_upsert_is_sequentially_batched(count: int, batch_lengths: list[int]) -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    points = make_points(count)

    await gateway(client).upsert(points)

    assert client.upsert.await_count == len(batch_lengths)
    for call, expected_length in zip(client.upsert.await_args_list, batch_lengths, strict=True):
        sent = call.kwargs["points"]
        assert len(sent) == expected_length
        assert call.kwargs["wait"] is True
        for point in sent:
            assert point.vector["dense_v1"] == [1.0, 0.0, 0.0]
            document = point.vector["bm25_v1"]
            assert document.text.startswith("traceable")
            assert document.model == "qdrant/bm25"
            assert document.options == {"language": "none", "tokenizer": "multilingual"}
            original = next(item for item in points if item.id == point.id)
            assert point.payload == original.payload


async def test_second_upsert_batch_failure_uses_safe_error() -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    client.upsert.side_effect = [None, RuntimeError("http://private:6333 secret document")]

    with pytest.raises(VectorIndexError) as caught:
        await gateway(client).upsert(make_points(65))

    assert str(caught.value) == "Qdrant points could not be written"
    assert "private" not in str(caught.value)
    assert "document" not in str(caught.value)
    assert client.upsert.await_count == 2


async def test_existing_dense_collection_adds_and_verifies_sparse_vector() -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    client.collection_exists.return_value = True
    client.get_collection.side_effect = [
        collection_info(sparse=False),
        collection_info(),
    ]

    await gateway(client).ensure_collection()

    config = client.create_vector_name.await_args.kwargs["vector_name_config"]
    assert config.sparse.modifier == models.Modifier.IDF


async def test_concurrent_sparse_vector_creation_is_rechecked() -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    client.collection_exists.return_value = True
    client.get_collection.side_effect = [
        collection_info(sparse=False),
        collection_info(),
    ]
    client.create_vector_name.side_effect = UnexpectedResponse(
        409, "Conflict", b"exists", Headers()
    )

    await gateway(client).ensure_collection()

    assert client.get_collection.await_count == 2


async def test_incompatible_sparse_vector_is_rejected_without_rebuild() -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    client.collection_exists.return_value = True
    client.get_collection.return_value = collection_info(sparse_modifier=None)

    with pytest.raises(IncompatibleCollectionError):
        await gateway(client).ensure_collection()

    client.delete_collection.assert_not_called()
    client.create_collection.assert_not_called()
    client.create_vector_name.assert_not_called()


async def test_hybrid_search_uses_shared_filters_and_rrf() -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    client.query_points.return_value = SimpleNamespace(
        points=[SimpleNamespace(id="point-a", score=0.7, payload={"content": "result"})]
    )
    knowledge_base_id, document_id, generation = uuid4(), uuid4(), uuid4()

    hits = await gateway(client).hybrid_search(
        [1.0, 0.0, 0.0],
        "DiscoveryClient",
        knowledge_base_id=knowledge_base_id,
        generations=[generation],
        limit=5,
        language="java",
        document_id=document_id,
        dense_score_threshold=0.5,
        excluded_chunk_types=("heading",),
    )

    assert hits[0].score == 0.7
    call = client.query_points.await_args.kwargs
    assert call["query"].fusion == models.Fusion.RRF
    assert "score_threshold" not in call
    dense, sparse = call["prefetch"]
    assert dense.using == "dense_v1"
    assert dense.score_threshold == 0.5
    assert dense.limit == 20
    assert sparse.using == "bm25_v1"
    assert sparse.score_threshold is None
    assert sparse.limit == 20
    assert sparse.query.text == "DiscoveryClient"
    assert sparse.query.options == {"language": "none", "tokenizer": "multilingual"}
    assert dense.filter == sparse.filter
    assert call["limit"] == 20


async def test_hybrid_search_sorts_scores_and_equal_scores_by_point_id() -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    payload_a = {"content": "same-score-a", "marker": ["unchanged"]}
    payload_b = {"content": "same-score-b"}
    payload_high = {"content": "higher-score"}
    client.query_points.return_value = SimpleNamespace(
        points=[
            SimpleNamespace(id="point-b", score=0.75, payload=payload_b),
            SimpleNamespace(id="point-low", score=0.25, payload={"content": "low"}),
            SimpleNamespace(id="point-high", score=0.833333, payload=payload_high),
            SimpleNamespace(id="point-a", score=0.75, payload=payload_a),
        ]
    )

    hits = await gateway(
        client,
        dense_prefetch_limit=8,
        sparse_prefetch_limit=12,
    ).hybrid_search(
        [1.0, 0.0, 0.0],
        "query",
        knowledge_base_id=uuid4(),
        generations=[uuid4()],
        limit=3,
        language=None,
        document_id=None,
        dense_score_threshold=0.5,
        excluded_chunk_types=("heading",),
    )

    assert [hit.score for hit in hits] == [0.833333, 0.75, 0.75]
    assert [hit.payload["content"] for hit in hits] == [
        "higher-score",
        "same-score-a",
        "same-score-b",
    ]
    assert hits[1].payload == payload_a
    assert payload_a == {"content": "same-score-a", "marker": ["unchanged"]}
    assert client.query_points.await_args.kwargs["limit"] == 12


async def test_hybrid_search_order_is_stable_for_permuted_equal_score_candidates() -> None:
    candidates = [
        SimpleNamespace(id="point-c", score=0.583333, payload={"content": "c"}),
        SimpleNamespace(id="point-a", score=0.583333, payload={"content": "a"}),
        SimpleNamespace(id="point-b", score=0.583333, payload={"content": "b"}),
    ]
    orders: list[list[str]] = []
    for points in (candidates, list(reversed(candidates))):
        client = AsyncMock(spec=AsyncQdrantClient)
        client.query_points.return_value = SimpleNamespace(points=points)
        hits = await gateway(client).hybrid_search(
            [1.0, 0.0, 0.0],
            "query",
            knowledge_base_id=uuid4(),
            generations=[uuid4()],
            limit=2,
            language=None,
            document_id=None,
            dense_score_threshold=0.5,
            excluded_chunk_types=("heading",),
        )
        orders.append([str(hit.payload["content"]) for hit in hits])
        assert len(hits) == 2
        assert client.query_points.await_args.kwargs["limit"] == 20

    assert orders == [["a", "b"], ["a", "b"]]


async def test_search_passes_threshold_and_heading_exclusion_with_existing_filters() -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    client.query_points.return_value = SimpleNamespace(
        points=[SimpleNamespace(score=0.82, payload={"content": "result"})]
    )
    knowledge_base_id, document_id = uuid4(), uuid4()
    generations = [uuid4(), uuid4()]

    hits = await gateway(client).search(
        [1.0, 0.0, 0.0],
        knowledge_base_id=knowledge_base_id,
        generations=generations,
        limit=5,
        language="java",
        document_id=document_id,
        score_threshold=0.5,
        excluded_chunk_types=("heading",),
    )

    assert hits[0].score == 0.82
    call = client.query_points.await_args.kwargs
    assert call["score_threshold"] == 0.5
    query_filter = call["query_filter"]
    must_keys = {condition.key for condition in query_filter.must}
    assert must_keys == {"knowledge_base_id", "index_generation", "language", "document_id"}
    generation_condition = next(
        condition for condition in query_filter.must if condition.key == "index_generation"
    )
    assert set(generation_condition.match.any) == {str(value) for value in generations}
    assert len(query_filter.must_not) == 1
    assert query_filter.must_not[0].key == "chunk_type"
    assert query_filter.must_not[0].match.any == ["heading"]


async def test_search_returns_empty_and_converts_client_errors() -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    client.query_points.return_value = SimpleNamespace(points=[])
    search_kwargs = {
        "knowledge_base_id": uuid4(),
        "generations": [uuid4()],
        "limit": 5,
        "language": None,
        "document_id": None,
        "score_threshold": 0.5,
        "excluded_chunk_types": ("heading",),
    }

    assert await gateway(client).search([1.0, 0.0, 0.0], **search_kwargs) == []

    client.query_points.side_effect = RuntimeError("http://private sensitive query")
    with pytest.raises(VectorIndexError) as caught:
        await gateway(client).search([1.0, 0.0, 0.0], **search_kwargs)
    assert str(caught.value) == "Semantic search is unavailable"
    assert "private" not in str(caught.value)


async def test_incompatible_payload_index_type_is_rejected_without_rebuild() -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    client.collection_exists.return_value = True
    schema = {
        name: models.PayloadIndexInfo(
            data_type=(
                models.PayloadSchemaType.TEXT
                if name == "symbol_lookup_keys"
                else models.PayloadSchemaType.KEYWORD
            ),
            points=0,
        )
        for name in QdrantGateway.payload_indexes
    }
    client.get_collection.return_value = collection_info(payload_schema=schema)

    with pytest.raises(IncompatibleCollectionError, match="symbol_lookup_keys"):
        await gateway(client).ensure_collection()

    client.delete_collection.assert_not_called()


async def test_symbol_filter_combines_with_existing_search_scope() -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    client.query_points.return_value = SimpleNamespace(points=[])
    lookup_key = "v1:method:UserService#source"

    await gateway(client).search(
        [1.0, 0.0, 0.0],
        knowledge_base_id=uuid4(),
        generations=[uuid4()],
        limit=5,
        language="java",
        document_id=uuid4(),
        score_threshold=0.5,
        excluded_chunk_types=("heading",),
        symbol_lookup_key=lookup_key,
    )

    query_filter = client.query_points.await_args.kwargs["query_filter"]
    symbol_condition = next(
        condition for condition in query_filter.must if condition.key == "symbol_lookup_keys"
    )
    assert symbol_condition.match.value == lookup_key


async def test_symbol_scroll_is_paginated_without_vectors() -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    first = SimpleNamespace(id="a", payload={"content": "a"})
    second = SimpleNamespace(id="b", payload={"content": "b"})
    client.scroll.side_effect = [([first], "b"), ([second], None)]

    result = await gateway(client).scroll_symbol_matches(
        knowledge_base_id=uuid4(),
        generations=[uuid4()],
        symbol_lookup_key="v1:method:UserService#source",
        language="java",
        document_id=None,
        max_points=10,
        page_size=1,
    )

    assert [point.id for point in result.points] == ["a", "b"]
    assert not result.truncated
    assert client.scroll.await_count == 2
    assert client.scroll.await_args_list[0].kwargs["with_payload"] is True
    assert client.scroll.await_args_list[0].kwargs["with_vectors"] is False
    assert client.scroll.await_args_list[1].kwargs["offset"] == "b"


async def test_symbol_scroll_reports_truncation_when_more_points_exist() -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    client.scroll.return_value = ([SimpleNamespace(id="a", payload={})], "next")

    result = await gateway(client).scroll_symbol_matches(
        knowledge_base_id=uuid4(),
        generations=[uuid4()],
        symbol_lookup_key="v1:method:UserService#source",
        language=None,
        document_id=None,
        max_points=1,
        page_size=1,
    )

    assert result.truncated
    assert len(result.points) == 1
