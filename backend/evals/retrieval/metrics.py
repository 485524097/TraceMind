from __future__ import annotations

import math
import statistics
from datetime import UTC, datetime

from evals.retrieval.matching import deduplicate_hits, matched_evidence_indexes
from evals.retrieval.models import (
    CaseEvaluation,
    EvaluationSummary,
    RetrievalCase,
    RetrievalHit,
    Strategy,
    UnanswerableObservation,
)


def _require_positive_k(k: int) -> None:
    if k <= 0:
        raise ValueError("k must be greater than zero")


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile_value
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def evaluate_case(
    case: RetrievalCase,
    hits: list[RetrievalHit],
    *,
    strategy: Strategy,
    latency_ms: float,
    k: int = 5,
    error: str | None = None,
) -> CaseEvaluation:
    _require_positive_k(k)
    top_hits = deduplicate_hits(hits)[:k]
    positive_indexes = {
        index for index, evidence in enumerate(case.gold_evidence) if evidence.relevance > 0
    }
    required_indexes = {
        index
        for index, evidence in enumerate(case.gold_evidence)
        if evidence.relevance > 0 and evidence.required
    }
    matched: set[int] = set()
    relevance_by_rank: list[int] = []
    first_relevant_rank: int | None = None
    relevant_hit_count = 0
    for rank, hit in enumerate(top_hits, start=1):
        indexes = set(matched_evidence_indexes(case.gold_evidence, hit)) & positive_indexes
        new_indexes = indexes - matched
        matched.update(indexes)
        relevance_by_rank.append(
            max((case.gold_evidence[index].relevance for index in indexes), default=0)
        )
        if indexes:
            relevant_hit_count += 1
            if first_relevant_rank is None:
                first_relevant_rank = rank
        if not new_indexes:
            continue

    hit_at_1 = float(bool(relevance_by_rank and relevance_by_rank[0] > 0))
    hit_at_5 = float(bool(matched))
    precision = relevant_hit_count / k
    if case.answerable:
        recall = len(matched) / len(positive_indexes)
        mrr = 0.0 if first_relevant_rank is None else 1.0 / first_relevant_rank
        ideal_relevances = sorted(
            (item.relevance for item in case.gold_evidence if item.relevance > 0),
            reverse=True,
        )[:k]
        dcg = sum(
            (2**relevance - 1) / math.log2(rank + 1)
            for rank, relevance in enumerate(relevance_by_rank, start=1)
        )
        ideal_dcg = sum(
            (2**relevance - 1) / math.log2(rank + 1)
            for rank, relevance in enumerate(ideal_relevances, start=1)
        )
        ndcg = dcg / ideal_dcg if ideal_dcg else 0.0
        all_required = float(required_indexes.issubset(matched))
        passed: bool | None = required_indexes.issubset(matched)
    else:
        recall = None
        mrr = None
        ndcg = None
        all_required = None
        passed = None

    return CaseEvaluation(
        case_id=case.id,
        query=case.query,
        strategy=strategy,
        latency_ms=latency_ms,
        hits=top_hits,
        matched_evidence=sorted(matched),
        missing_required_evidence=sorted(required_indexes - matched),
        hit_at_1=hit_at_1,
        hit_at_5=hit_at_5,
        precision_at_5=precision,
        recall_at_5=recall,
        mrr_at_5=mrr,
        ndcg_at_5=ndcg,
        all_required_at_5=all_required,
        passed=passed,
        observational=not case.answerable,
        error=error,
    )


def summarize(
    strategy: Strategy,
    cases: list[RetrievalCase],
    evaluations: list[CaseEvaluation],
) -> EvaluationSummary:
    by_id = {case.id: case for case in cases}
    answerable = [item for item in evaluations if by_id[item.case_id].answerable]
    latencies = [item.latency_ms for item in evaluations]

    def average(field: str) -> float:
        values = [getattr(item, field) for item in answerable]
        numeric = [float(value) for value in values if value is not None]
        return statistics.fmean(numeric) if numeric else 0.0

    observations: list[UnanswerableObservation] = []
    for item in evaluations:
        if by_id[item.case_id].answerable:
            continue
        top = item.hits[0] if item.hits else None
        observations.append(
            UnanswerableObservation(
                case_id=item.case_id,
                returned_count=len(item.hits),
                top1_score=top.score if top else None,
                top1_document=top.document_name if top else None,
                top1_section=top.section_title if top else None,
                top1_content_preview=top.content[:160] if top else None,
            )
        )
    return EvaluationSummary(
        strategy=strategy,
        generated_at=datetime.now(UTC),
        case_count=len(evaluations),
        answerable_count=len(answerable),
        passed_count=sum(item.passed is True for item in answerable),
        failed_count=sum(item.passed is False for item in answerable),
        hit_at_1=average("hit_at_1"),
        hit_at_5=average("hit_at_5"),
        precision_at_5=average("precision_at_5"),
        recall_at_5=average("recall_at_5"),
        mrr_at_5=average("mrr_at_5"),
        ndcg_at_5=average("ndcg_at_5"),
        all_required_at_5=average("all_required_at_5"),
        p50_latency_ms=percentile(latencies, 0.50),
        p95_latency_ms=percentile(latencies, 0.95),
        cases=evaluations,
        unanswerable_observations=observations,
    )
