import pytest

from evals.retrieval.metrics import evaluate_case, summarize
from evals.retrieval.models import GoldEvidence, RetrievalCase, RetrievalHit


def case(*, answerable: bool = True, multi: bool = False) -> RetrievalCase:
    evidence = [
        GoldEvidence(
            document_name="synthetic.md",
            section_title="第一节",
            line_start=10,
            line_end=10,
            anchor_text="这是第一条长度足够并且可以稳定识别的核心证据内容。",
            relevance=2,
            required=True,
            notes="",
        )
    ]
    if multi:
        evidence.append(
            GoldEvidence(
                document_name="synthetic.md",
                section_title="第二节",
                line_start=20,
                line_end=20,
                anchor_text="这是第二条长度足够并且来自不同章节的核心证据内容。",
                relevance=1,
                required=True,
                notes="",
            )
        )
    return RetrievalCase(
        id="ret-900",
        split="dev",
        query="查询",
        query_type="multi_evidence" if multi else ("semantic" if answerable else "unanswerable"),
        difficulty="easy",
        language_filter=None,
        answerable=answerable,
        gold_evidence=evidence if answerable else [],
        absence_terms=[] if answerable else ["不存在术语"],
        notes="",
    )


def hit(rank: int, section: str, line: int, content: str, chunk_id: str) -> RetrievalHit:
    return RetrievalHit(
        rank=rank,
        score=1.0 / rank,
        chunk_id=chunk_id,
        document_name="synthetic.md",
        section_title=section,
        start_line=line,
        end_line=line,
        content=content,
    )


def test_hit_recall_mrr_ndcg_and_all_required() -> None:
    evaluation = evaluate_case(
        case(multi=True),
        [
            hit(1, "干扰节", 1, "无关内容", "noise"),
            hit(2, "第一节", 10, "这是第一条长度足够并且可以稳定识别的核心证据内容。", "a"),
            hit(3, "第二节", 20, "这是第二条长度足够并且来自不同章节的核心证据内容。", "b"),
        ],
        strategy="hybrid",
        latency_ms=12,
        k=5,
    )
    assert evaluation.hit_at_1 == 0
    assert evaluation.hit_at_5 == 1
    assert evaluation.precision_at_5 == pytest.approx(0.4)
    assert evaluation.recall_at_5 == 1
    assert evaluation.mrr_at_5 == 0.5
    assert evaluation.ndcg_at_5 == pytest.approx(0.6590018048)
    assert evaluation.all_required_at_5 == 1
    assert evaluation.passed


def test_empty_results_are_safe_and_k_must_be_positive() -> None:
    evaluation = evaluate_case(
        case(),
        [],
        strategy="dense",
        latency_ms=1,
        k=5,
    )
    assert evaluation.recall_at_5 == 0
    assert evaluation.mrr_at_5 == 0
    assert evaluation.ndcg_at_5 == 0
    with pytest.raises(ValueError, match="greater than zero"):
        evaluate_case(case(), [], strategy="dense", latency_ms=1, k=0)


def test_unanswerable_case_is_excluded_from_standard_recall() -> None:
    answerable = evaluate_case(case(), [], strategy="dense", latency_ms=10)
    observational_case = case(answerable=False)
    observational_case = observational_case.model_copy(update={"id": "ret-901"})
    observational = evaluate_case(
        observational_case,
        [],
        strategy="dense",
        latency_ms=20,
    )
    summary = summarize(
        "dense",
        [case(), observational_case],
        [answerable, observational],
    )
    assert observational.recall_at_5 is None
    assert observational.observational
    assert summary.answerable_count == 1
    assert summary.recall_at_5 == 0
    assert len(summary.unanswerable_observations) == 1
