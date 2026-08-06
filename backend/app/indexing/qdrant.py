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


@dataclass(frozen=True)
class HybridCandidate:
    point_id: str
    score: float
    payload: dict[str, Any]


@dataclass(frozen=True)
class PayloadPoint:
    id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class PayloadScrollResult:
    points: list[PayloadPoint]
    truncated: bool = False


def stable_payload_key(payload: dict[str, Any], point_id: str) -> tuple[object, ...]:
    """Return a generation-independent ordering key without inspecting content."""

    def text(key: str) -> str:
        value = payload.get(key)
        return value if isinstance(value, str) else ""

    def line(key: str) -> tuple[bool, int]:
        value = payload.get(key)
        if type(value) is int:
            return False, value
        return True, 0

    def integer(key: str) -> tuple[bool, int]:
        value = payload.get(key)
        if type(value) is int:
            return False, value
        return True, 0

    relative_path = text("relative_path") or text("document_name")
    return (
        relative_path,
        line("start_line"),
        line("end_line"),
        integer("chunk_index"),
        text("content_hash"),
        text("document_id"),
        text("chunk_id"),
        point_id,
    )


def deterministic_rrf(
    dense_candidates: Collection[HybridCandidate],
    sparse_candidates: Collection[HybridCandidate],
    *,
    limit: int,
) -> list[VectorSearchHit]:
    """Fuse independently stable branches using Qdrant's default RRF k=2."""

    def branch_order(candidate: HybridCandidate) -> tuple[object, ...]:
        return (
            -candidate.score,
            stable_payload_key(candidate.payload, candidate.point_id),
        )

    scores: dict[str, float] = {}
    candidates_by_id: dict[str, HybridCandidate] = {}
    for branch in (dense_candidates, sparse_candidates):
        for rank, candidate in enumerate(sorted(branch, key=branch_order)):
            scores[candidate.point_id] = scores.get(candidate.point_id, 0.0) + 1.0 / (2 + rank)
            candidates_by_id.setdefault(candidate.point_id, candidate)

    fused = sorted(
        candidates_by_id.values(),
        key=lambda candidate: (
            -scores[candidate.point_id],
            stable_payload_key(candidate.payload, candidate.point_id),
        ),
    )
    return [
        VectorSearchHit(score=scores[candidate.point_id], payload=candidate.payload)
        for candidate in fused[:limit]
    ]


class QdrantGateway:
    payload_indexes = (
        "knowledge_base_id",
        "document_id",
        "document_version_id",
        "index_generation",
        "language",
        "chunk_type",
        "symbol_lookup_keys",
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
            missing_payload_indexes = [
                field_name
                for field_name in self.payload_indexes
                if info.payload_schema.get(field_name) is None
            ]
            for field_name in missing_payload_indexes:
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
            if missing_payload_indexes:
                info = await self.client.get_collection(self.collection_name)
            for field_name in self.payload_indexes:
                current = info.payload_schema.get(field_name)
                if current is None:
                    raise VectorIndexError("Qdrant payload index could not be verified")
                if getattr(current, "data_type", None) != models.PayloadSchemaType.KEYWORD:
                    raise IncompatibleCollectionError(
                        f"Qdrant payload index {field_name} is incompatible"
                    )
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
        symbol_lookup_key: str | None = None,
    ) -> list[VectorSearchHit]:
        query_filter = self._search_filter(
            knowledge_base_id,
            generations,
            language=language,
            document_id=document_id,
            excluded_chunk_types=excluded_chunk_types,
            symbol_lookup_key=symbol_lookup_key,
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
        symbol_lookup_key: str | None = None,
    ) -> list[VectorSearchHit]:
        query_filter = self._search_filter(
            knowledge_base_id,
            generations,
            language=language,
            document_id=document_id,
            excluded_chunk_types=excluded_chunk_types,
            symbol_lookup_key=symbol_lookup_key,
        )
        try:
            responses = await self.client.query_batch_points(
                self.collection_name,
                requests=[
                    models.QueryRequest(
                        query=vector,
                        using=self.vector_name,
                        filter=query_filter,
                        limit=max(limit, self.dense_prefetch_limit),
                        score_threshold=dense_score_threshold,
                        with_payload=True,
                        with_vector=False,
                    ),
                    models.QueryRequest(
                        query=self._bm25_document(query),
                        using=self.sparse_vector_name,
                        filter=query_filter,
                        limit=max(limit, self.sparse_prefetch_limit),
                        with_payload=True,
                        with_vector=False,
                    ),
                ],
            )
            if len(responses) != 2:
                raise VectorIndexError("Hybrid search returned an unexpected branch count")
            dense_response, sparse_response = responses
            dense_candidates = [
                HybridCandidate(str(point.id), float(point.score), dict(point.payload or {}))
                for point in dense_response.points
            ]
            sparse_candidates = [
                HybridCandidate(str(point.id), float(point.score), dict(point.payload or {}))
                for point in sparse_response.points
            ]
            return deterministic_rrf(dense_candidates, sparse_candidates, limit=limit)
        except VectorIndexError:
            raise
        except Exception as exc:
            raise VectorIndexError("Hybrid search is unavailable") from exc

    async def scroll_symbol_matches(
        self,
        *,
        knowledge_base_id: UUID,
        generations: list[UUID],
        symbol_lookup_key: str,
        language: str | None,
        document_id: UUID | None,
        max_points: int = 200,
        page_size: int = 64,
    ) -> PayloadScrollResult:
        query_filter = self._search_filter(
            knowledge_base_id,
            generations,
            language=language,
            document_id=document_id,
            excluded_chunk_types=("heading",),
            symbol_lookup_key=symbol_lookup_key,
        )
        points: list[PayloadPoint] = []
        offset: models.ExtendedPointId | None = None
        try:
            while len(points) < max_points:
                records, next_offset = await self.client.scroll(
                    self.collection_name,
                    scroll_filter=query_filter,
                    limit=min(page_size, max_points - len(points)),
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                points.extend(
                    PayloadPoint(str(record.id), dict(record.payload or {})) for record in records
                )
                if next_offset is None:
                    return PayloadScrollResult(points)
                if not records:
                    return PayloadScrollResult(points, truncated=True)
                offset = next_offset
            return PayloadScrollResult(points, truncated=offset is not None)
        except Exception as exc:
            raise VectorIndexError("Symbol scope validation is unavailable") from exc

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
        symbol_lookup_key: str | None = None,
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
        if symbol_lookup_key is not None:
            must.append(cls._equal_condition("symbol_lookup_keys", symbol_lookup_key))
        must_not: list[models.Condition] = []
        if excluded_chunk_types:
            must_not.append(
                models.FieldCondition(
                    key="chunk_type",
                    match=models.MatchAny(any=list(excluded_chunk_types)),
                )
            )
        return models.Filter(must=must, must_not=must_not)
