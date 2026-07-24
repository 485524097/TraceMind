from collections.abc import Collection
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
    dense_vector: list[float]
    sparse_text: str
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
        sparse_vector_name: str,
        bm25_model: str,
        bm25_tokenizer: str,
        bm25_language: str,
        dimension: int,
        upsert_batch_size: int,
        dense_prefetch_limit: int,
        sparse_prefetch_limit: int,
    ) -> None:
        self.client = client
        self.collection_name = collection_name
        self.vector_name = vector_name
        self.sparse_vector_name = sparse_vector_name
        self.bm25_model = bm25_model
        self.bm25_tokenizer = bm25_tokenizer
        self.bm25_language = bm25_language
        self.dimension = dimension
        self.upsert_batch_size = upsert_batch_size
        self.dense_prefetch_limit = dense_prefetch_limit
        self.sparse_prefetch_limit = sparse_prefetch_limit

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
                        sparse_vectors_config={
                            self.sparse_vector_name: models.SparseVectorParams(
                                modifier=models.Modifier.IDF
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
            sparse_vectors = info.config.params.sparse_vectors or {}
            sparse = sparse_vectors.get(self.sparse_vector_name)
            if sparse is None:
                try:
                    await self.client.create_vector_name(
                        collection_name=self.collection_name,
                        vector_name=self.sparse_vector_name,
                        vector_name_config=models.SparseVectorNameConfig(
                            sparse=models.SparseVectorConfig(modifier=models.Modifier.IDF)
                        ),
                        wait=True,
                    )
                except UnexpectedResponse as exc:
                    if exc.status_code not in {400, 409}:
                        raise
                info = await self.client.get_collection(self.collection_name)
                sparse_vectors = info.config.params.sparse_vectors or {}
                sparse = sparse_vectors.get(self.sparse_vector_name)
            if sparse is None or sparse.modifier != models.Modifier.IDF:
                raise IncompatibleCollectionError(
                    "Qdrant sparse vector configuration is incompatible"
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
                            vector={
                                self.vector_name: point.dense_vector,
                                self.sparse_vector_name: self._bm25_document(point.sparse_text),
                            },
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
        score_threshold: float,
        excluded_chunk_types: Collection[str],
    ) -> list[VectorSearchHit]:
        query_filter = self._search_filter(
            knowledge_base_id,
            generations,
            language=language,
            document_id=document_id,
            excluded_chunk_types=excluded_chunk_types,
        )
        try:
            response = await self.client.query_points(
                self.collection_name,
                query=vector,
                using=self.vector_name,
                query_filter=query_filter,
                limit=limit,
                score_threshold=score_threshold,
                with_payload=True,
                with_vectors=False,
            )
            return [
                VectorSearchHit(float(point.score), dict(point.payload or {}))
                for point in response.points
            ]
        except Exception as exc:
            raise VectorIndexError("Semantic search is unavailable") from exc

    async def hybrid_search(
        self,
        vector: list[float],
        query: str,
        *,
        knowledge_base_id: UUID,
        generations: list[UUID],
        limit: int,
        language: str | None,
        document_id: UUID | None,
        dense_score_threshold: float,
        excluded_chunk_types: Collection[str],
    ) -> list[VectorSearchHit]:
        query_filter = self._search_filter(
            knowledge_base_id,
            generations,
            language=language,
            document_id=document_id,
            excluded_chunk_types=excluded_chunk_types,
        )
        try:
            response = await self.client.query_points(
                self.collection_name,
                prefetch=[
                    models.Prefetch(
                        query=vector,
                        using=self.vector_name,
                        filter=query_filter,
                        limit=max(limit, self.dense_prefetch_limit),
                        score_threshold=dense_score_threshold,
                    ),
                    models.Prefetch(
                        query=self._bm25_document(query),
                        using=self.sparse_vector_name,
                        filter=query_filter,
                        limit=max(limit, self.sparse_prefetch_limit),
                    ),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            return [
                VectorSearchHit(float(point.score), dict(point.payload or {}))
                for point in response.points
            ]
        except Exception as exc:
            raise VectorIndexError("Hybrid search is unavailable") from exc

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

    def _bm25_document(self, text: str) -> models.Document:
        return models.Document(
            text=text,
            model=self.bm25_model,
            options={
                "language": self.bm25_language,
                "tokenizer": self.bm25_tokenizer,
            },
        )

    @classmethod
    def _search_filter(
        cls,
        knowledge_base_id: UUID,
        generations: list[UUID],
        *,
        language: str | None,
        document_id: UUID | None,
        excluded_chunk_types: Collection[str],
    ) -> models.Filter:
        must: list[models.Condition] = [
            cls._equal_condition("knowledge_base_id", knowledge_base_id),
            models.FieldCondition(
                key="index_generation",
                match=models.MatchAny(any=[str(value) for value in generations]),
            ),
        ]
        if language is not None:
            must.append(cls._equal_condition("language", language))
        if document_id is not None:
            must.append(cls._equal_condition("document_id", document_id))
        must_not: list[models.Condition] = []
        if excluded_chunk_types:
            must_not.append(
                models.FieldCondition(
                    key="chunk_type",
                    match=models.MatchAny(any=list(excluded_chunk_types)),
                )
            )
        return models.Filter(must=must, must_not=must_not)
