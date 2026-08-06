from __future__ import annotations

import argparse
import hashlib
import json
import struct
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.core.config import Settings
from app.embedding import SentenceTransformerEmbeddingProvider
from app.indexing import QdrantGateway
from app.indexing.qdrant import HybridCandidate, deterministic_rrf, stable_payload_key
from evals.retrieval.matching import matched_evidence_indexes
from evals.retrieval.models import RetrievalCase, RetrievalHit
from evals.retrieval.validate_dataset import load_cases

DEFAULT_CASE_IDS = ("ret-010", "ret-015", "ret-016")


def validate_local_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
    }:
        raise argparse.ArgumentTypeError("URL must point to a local service")
    if parsed.query or parsed.fragment:
        raise argparse.ArgumentTypeError("URL must not contain query or fragment")
    return value.rstrip("/")


def point_is_target(case: RetrievalCase, payload: dict[str, Any]) -> bool:
    try:
        hit = RetrievalHit(
            rank=1,
            score=0,
            chunk_id=str(payload["chunk_id"]),
            document_name=str(payload["document_name"]),
            section_title=payload.get("section_title"),
            start_line=payload.get("start_line"),
            end_line=payload.get("end_line"),
            content=str(payload["content"]),
        )
    except (KeyError, TypeError, ValueError):
        return False
    return bool(matched_evidence_indexes(case.gold_evidence, hit))


def diagnostic_row(
    case: RetrievalCase,
    point: models.ScoredPoint,
    *,
    rank: int,
) -> dict[str, object]:
    payload = dict(point.payload or {})
    return diagnostic_payload_row(
        case,
        payload,
        point_id=str(point.id),
        score=float(point.score),
        rank=rank,
    )


def diagnostic_payload_row(
    case: RetrievalCase,
    payload: dict[str, Any],
    *,
    point_id: str,
    score: float,
    rank: int,
) -> dict[str, object]:
    return {
        "case_id": case.id,
        "relative_path": payload.get("relative_path"),
        "document_id": payload.get("document_id"),
        "document_version_id": payload.get("document_version_id"),
        "index_generation": payload.get("index_generation"),
        "point_id": point_id,
        "chunk_id": payload.get("chunk_id"),
        "chunk_index": payload.get("chunk_index"),
        "content_hash": payload.get("content_hash"),
        "start_line": payload.get("start_line"),
        "end_line": payload.get("end_line"),
        "score": score,
        "rank": rank,
        "is_target": point_is_target(case, payload),
    }


def _vector_sha256(vector: list[float]) -> str:
    return hashlib.sha256(struct.pack(f"<{len(vector)}f", *vector)).hexdigest()


def _sorted_branch(points: list[models.ScoredPoint]) -> list[models.ScoredPoint]:
    return sorted(
        points,
        key=lambda point: (
            -float(point.score),
            stable_payload_key(dict(point.payload or {}), str(point.id)),
        ),
    )


def _application_fusion_rows(
    case: RetrievalCase,
    dense: list[models.ScoredPoint],
    sparse: list[models.ScoredPoint],
    *,
    limit: int,
) -> list[dict[str, object]]:
    candidates = [
        [
            HybridCandidate(str(point.id), float(point.score), dict(point.payload or {}))
            for point in branch
        ]
        for branch in (dense, sparse)
    ]
    point_ids = {
        str(candidate.payload.get("chunk_id")): candidate.point_id
        for branch in candidates
        for candidate in branch
    }
    hits = deterministic_rrf(candidates[0], candidates[1], limit=limit)
    return [
        diagnostic_payload_row(
            case,
            hit.payload,
            point_id=point_ids.get(str(hit.payload.get("chunk_id")), ""),
            score=hit.score,
            rank=rank,
        )
        for rank, hit in enumerate(hits, start=1)
    ]


def _query_with_retry(
    client: QdrantClient, collection_name: str, **kwargs: Any
) -> list[models.ScoredPoint]:
    for attempt in range(3):
        try:
            return client.query_points(collection_name, **kwargs).points
        except UnexpectedResponse as exc:
            if exc.status_code != 502 or attempt == 2:
                raise
        time.sleep(attempt + 1)
    raise RuntimeError("unreachable Qdrant diagnostic retry state")


def diagnose(
    *,
    settings: Settings,
    qdrant_url: str,
    knowledge_base_id: UUID,
    document_id: UUID,
    generation: UUID,
    cases: list[RetrievalCase],
    repetitions: int,
) -> dict[str, object]:
    provider = SentenceTransformerEmbeddingProvider(
        settings.embedding_model_name,
        settings.embedding_dimension,
        settings.embedding_batch_size,
        settings.resolved_query_embedding_device,
    )
    client = QdrantClient(
        url=qdrant_url,
        timeout=settings.qdrant_operation_timeout_seconds,
        check_compatibility=False,
        trust_env=False,
    )
    gateway = QdrantGateway(
        client,  # type: ignore[arg-type]
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
    branch_limit = max(settings.hybrid_dense_prefetch_limit, 10)
    fusion_limit = max(
        10, settings.hybrid_dense_prefetch_limit, settings.hybrid_sparse_prefetch_limit
    )
    query_filter = gateway._search_filter(  # noqa: SLF001 - diagnostic mirrors production
        knowledge_base_id,
        [generation],
        language=None,
        document_id=document_id,
        excluded_chunk_types=("heading",),
    )
    output: list[dict[str, object]] = []
    for case in cases:
        vectors = [provider.embed_query(case.query) for _ in range(repetitions)]
        first = vectors[0]
        max_abs_diff = max(
            (
                abs(left - right)
                for vector in vectors[1:]
                for left, right in zip(first, vector, strict=True)
            ),
            default=0,
        )
        runs: list[dict[str, object]] = []
        for repetition, vector in enumerate(vectors, start=1):
            dense = _query_with_retry(
                client,
                settings.qdrant_collection_name,
                query=vector,
                using=settings.qdrant_dense_vector_name,
                query_filter=query_filter,
                limit=branch_limit,
                score_threshold=settings.semantic_search_score_threshold,
                with_payload=True,
                with_vectors=False,
            )
            dense_exact = _query_with_retry(
                client,
                settings.qdrant_collection_name,
                query=vector,
                using=settings.qdrant_dense_vector_name,
                query_filter=query_filter,
                limit=branch_limit,
                score_threshold=settings.semantic_search_score_threshold,
                search_params=models.SearchParams(exact=True),
                with_payload=True,
                with_vectors=False,
            )
            sparse = _query_with_retry(
                client,
                settings.qdrant_collection_name,
                query=gateway._bm25_document(case.query),  # noqa: SLF001
                using=settings.qdrant_sparse_vector_name,
                query_filter=query_filter,
                limit=max(settings.hybrid_sparse_prefetch_limit, 10),
                with_payload=True,
                with_vectors=False,
            )
            fusion = _query_with_retry(
                client,
                settings.qdrant_collection_name,
                prefetch=[
                    models.Prefetch(
                        query=vector,
                        using=settings.qdrant_dense_vector_name,
                        filter=query_filter,
                        limit=max(10, settings.hybrid_dense_prefetch_limit),
                        score_threshold=settings.semantic_search_score_threshold,
                    ),
                    models.Prefetch(
                        query=gateway._bm25_document(case.query),  # noqa: SLF001
                        using=settings.qdrant_sparse_vector_name,
                        filter=query_filter,
                        limit=max(10, settings.hybrid_sparse_prefetch_limit),
                    ),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=fusion_limit,
                with_payload=True,
                with_vectors=False,
            )
            runs.append(
                {
                    "repetition": repetition,
                    "query_vector_sha256": _vector_sha256(vector),
                    "dense": [
                        diagnostic_row(case, point, rank=rank)
                        for rank, point in enumerate(_sorted_branch(dense), start=1)
                    ],
                    "dense_exact": [
                        diagnostic_row(case, point, rank=rank)
                        for rank, point in enumerate(_sorted_branch(dense_exact), start=1)
                    ],
                    "sparse": [
                        diagnostic_row(case, point, rank=rank)
                        for rank, point in enumerate(_sorted_branch(sparse), start=1)
                    ],
                    "qdrant_fusion": [
                        diagnostic_row(case, point, rank=rank)
                        for rank, point in enumerate(_sorted_branch(fusion)[:10], start=1)
                    ],
                    "fusion": _application_fusion_rows(
                        case,
                        dense,
                        sparse,
                        limit=fusion_limit,
                    ),
                }
            )
        output.append(
            {
                "case_id": case.id,
                "query_vector_dimension": len(first),
                "query_vector_max_abs_diff": max_abs_diff,
                "runs": runs,
            }
        )
    client.close()
    return {
        "knowledge_base_id": str(knowledge_base_id),
        "document_id": str(document_id),
        "generation": str(generation),
        "embedding_model": settings.embedding_model_name,
        "embedding_device": settings.resolved_query_embedding_device,
        "embedding_dimension": settings.embedding_dimension,
        "dense_score_threshold": settings.semantic_search_score_threshold,
        "dense_prefetch_limit": settings.hybrid_dense_prefetch_limit,
        "sparse_prefetch_limit": settings.hybrid_sparse_prefetch_limit,
        "fusion_limit": fusion_limit,
        "api_final_limit": 5,
        "cases": output,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnose fixed Hybrid ranking layers")
    parser.add_argument("--qdrant-url", type=validate_local_url, required=True)
    parser.add_argument("--knowledge-base-id", type=UUID, required=True)
    parser.add_argument("--document-id", type=UUID, required=True)
    parser.add_argument("--generation", type=UUID, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--case-ids", nargs="+", default=DEFAULT_CASE_IDS)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.repetitions <= 0:
        raise SystemExit("--repetitions must be greater than zero")
    selected = set(args.case_ids)
    cases = [case for case in load_cases(args.dataset) if case.id in selected]
    if {case.id for case in cases} != selected:
        raise SystemExit("one or more requested cases do not exist")
    report = diagnose(
        settings=Settings(),
        qdrant_url=args.qdrant_url,
        knowledge_base_id=args.knowledge_base_id,
        document_id=args.document_id,
        generation=args.generation,
        cases=cases,
        repetitions=args.repetitions,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
