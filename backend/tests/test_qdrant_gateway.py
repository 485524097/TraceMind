from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from qdrant_client import AsyncQdrantClient, models

from app.indexing import IncompatibleCollectionError, QdrantGateway, VectorPoint


def gateway(client: AsyncMock) -> QdrantGateway:
    return QdrantGateway(
        client,
        collection_name="tracemind_chunks",
        vector_name="dense_v1",
        dimension=3,
    )


def collection_info(*, size: int = 3, distance: models.Distance = models.Distance.COSINE):
    vectors = {"dense_v1": models.VectorParams(size=size, distance=distance)}
    return SimpleNamespace(
        config=SimpleNamespace(params=SimpleNamespace(vectors=vectors)),
        payload_schema={},
    )


async def test_collection_is_created_with_named_cosine_vector_and_payload_indexes() -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    client.collection_exists.return_value = False
    client.get_collection.return_value = collection_info()

    await gateway(client).ensure_collection()

    client.create_collection.assert_awaited_once()
    vectors = client.create_collection.await_args.kwargs["vectors_config"]
    assert vectors["dense_v1"].size == 3
    assert vectors["dense_v1"].distance == models.Distance.COSINE
    assert client.create_payload_index.await_count == 6


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


async def test_point_upsert_uses_named_vector_and_complete_payload() -> None:
    client = AsyncMock(spec=AsyncQdrantClient)
    point_id = uuid4()
    payload = {"knowledge_base_id": str(uuid4()), "content": "traceable"}

    await gateway(client).upsert([VectorPoint(point_id, [1.0, 0.0, 0.0], payload)])

    sent = client.upsert.await_args.kwargs["points"][0]
    assert sent.id == point_id
    assert sent.vector == {"dense_v1": [1.0, 0.0, 0.0]}
    assert sent.payload == payload
    assert client.upsert.await_args.kwargs["wait"] is True
