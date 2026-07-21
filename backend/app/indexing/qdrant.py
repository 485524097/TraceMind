from dataclasses import dataclass
from typing import Any
from uuid import UUID

from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse


class VectorIndexError(Exception):
    """Safe Qdrant indexing or search failure."""


class IncompatibleCollectionError(VectorIndexError):
    pass


@dataclass(frozen=True)
class VectorPoint:
    id: UUID
    vector: list[float]
    payload: dict[str, Any]


@dataclass(frozen=True)
class VectorSearchHit:
    score: float
    payload: dict[str, Any]


class QdrantGateway:
    payload_indexes = (
        "knowledge_base_id",
        "document_id",
        "document_version_id",
        "index_generation",
        "language",
        "chunk_type",
    )

    def __init__(
        self,
        client: AsyncQdrantClient,
        *,
        collection_name: str,
        vector_name: str,
        dimension: int,
        upsert_batch_size: int,
    ) -> None:
        self.client = client
        self.collection_name = collection_name
        self.vector_name = vector_name
        self.dimension = dimension
        self.upsert_batch_size = upsert_batch_size

    async def ensure_collection(self) -> None:
        try:
            if not await self.client.collection_exists(self.collection_name):
                try:
                    await self.client.create_collection(
                        self.collection_name,
                        vectors_config={
                            self.vector_name: models.VectorParams(
                                size=self.dimension,
                                distance=models.Distance.COSINE,
                            )
                        },
                    )
                except UnexpectedResponse as exc:
                    if exc.status_code not in {400, 409} or not await self.client.collection_exists(
                        self.collection_name
                    ):
                        raise
            info = await self.client.get_collection(self.collection_name)
            vectors = info.config.params.vectors
            if not isinstance(vectors, dict):
                raise IncompatibleCollectionError(
                    "Qdrant collection does not use the configured named vector"
                )
            configured = vectors.get(self.vector_name)
            if (
                configured is None
                or configured.size != self.dimension
                or configured.distance != models.Distance.COSINE
            ):
                raise IncompatibleCollectionError(
                    "Qdrant collection vector configuration is incompatible"
                )
            existing_indexes = set(info.payload_schema)
            for field_name in self.payload_indexes:
                if field_name not in existing_indexes:
                    try:
                        await self.client.create_payload_index(
                            self.collection_name,
                            field_name=field_name,
                            field_schema=models.PayloadSchemaType.KEYWORD,
                            wait=True,
                        )
                    except UnexpectedResponse as exc:
                        if exc.status_code not in {400, 409}:
                            raise
                        refreshed = await self.client.get_collection(self.collection_name)
                        if field_name not in refreshed.payload_schema:
                            raise
        except VectorIndexError:
            raise
        except Exception as exc:
            raise VectorIndexError("Qdrant collection could not be prepared") from exc

    async def upsert(self, points: list[VectorPoint]) -> None:
        if not points:
            return
        try:
            for start in range(0, len(points), self.upsert_batch_size):
                batch = points[start : start + self.upsert_batch_size]
                await self.client.upsert(
                    self.collection_name,
                    points=[
                        models.PointStruct(
                            id=point.id,
                            vector={self.vector_name: point.vector},
                            payload=point.payload,
                        )
                        for point in batch
                    ],
                    wait=True,
                )
        except Exception as exc:
            raise VectorIndexError("Qdrant points could not be written") from exc

    async def count_generation(self, generation: UUID) -> int:
        try:
            result = await self.client.count(
                self.collection_name,
                count_filter=self._equal_filter("index_generation", generation),
                exact=True,
            )
            return result.count
        except Exception as exc:
            raise VectorIndexError("Qdrant point count could not be verified") from exc

    async def delete_generation(self, generation: UUID) -> None:
        await self._delete_by_filter(self._equal_filter("index_generation", generation))

    async def delete_document(self, document_id: UUID) -> None:
        await self._delete_by_filter(self._equal_filter("document_id", document_id))

    async def delete_version(self, version_id: UUID) -> None:
        await self._delete_by_filter(self._equal_filter("document_version_id", version_id))

    async def search(
        self,
        vector: list[float],
        *,
        knowledge_base_id: UUID,
        generations: list[UUID],
        limit: int,
        language: str | None,
        document_id: UUID | None,
    ) -> list[VectorSearchHit]:
        must: list[models.Condition] = [
            self._equal_condition("knowledge_base_id", knowledge_base_id),
            models.FieldCondition(
                key="index_generation",
                match=models.MatchAny(any=[str(value) for value in generations]),
            ),
        ]
        if language is not None:
            must.append(self._equal_condition("language", language))
        if document_id is not None:
            must.append(self._equal_condition("document_id", document_id))
        try:
            response = await self.client.query_points(
                self.collection_name,
                query=vector,
                using=self.vector_name,
                query_filter=models.Filter(must=must),
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            return [
                VectorSearchHit(float(point.score), dict(point.payload or {}))
                for point in response.points
            ]
        except Exception as exc:
            raise VectorIndexError("Semantic search is unavailable") from exc

    async def _delete_by_filter(self, value_filter: models.Filter) -> None:
        try:
            await self.client.delete(
                self.collection_name,
                points_selector=models.FilterSelector(filter=value_filter),
                wait=True,
            )
        except Exception as exc:
            raise VectorIndexError("Qdrant points could not be deleted") from exc

    @classmethod
    def _equal_filter(cls, key: str, value: UUID | str) -> models.Filter:
        return models.Filter(must=[cls._equal_condition(key, value)])

    @staticmethod
    def _equal_condition(key: str, value: UUID | str) -> models.FieldCondition:
        return models.FieldCondition(key=key, match=models.MatchValue(value=str(value)))
