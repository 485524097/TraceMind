import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from evals.retrieval.baseline import compare_to_baseline, load_baseline, save_baseline
from evals.retrieval.models import (
    CorpusManifest,
    EvaluationSummary,
    RegressionThresholds,
    RetrievalCase,
)
from evals.retrieval.runner import (
    build_parser,
    evaluate_strategy,
    regression_exit_code,
    render_comparison,
    render_strategy_report,
    strategy_endpoint,
)


def summary(
    *,
    recall: float,
    mrr: float,
    hit: float,
    required: float,
    p95: float,
) -> EvaluationSummary:
    return EvaluationSummary(
        strategy="hybrid",
        generated_at=datetime.now(UTC),
        case_count=0,
        answerable_count=0,
        passed_count=0,
        failed_count=0,
        hit_at_1=hit,
        hit_at_5=hit,
        precision_at_5=0,
        recall_at_5=recall,
        mrr_at_5=mrr,
        ndcg_at_5=0,
        all_required_at_5=required,
        p50_latency_ms=10,
        p95_latency_ms=p95,
        cases=[],
        unanswerable_observations=[],
    )


def test_baseline_does_not_overwrite_and_detects_regression(tmp_path: Path) -> None:
    baseline = summary(recall=0.9, mrr=0.8, hit=0.9, required=0.9, p95=100)
    path = tmp_path / "baseline.json"
    save_baseline(baseline, path)
    assert load_baseline(path).recall_at_5 == 0.9
    with pytest.raises(FileExistsError):
        save_baseline(baseline, path)

    current = summary(recall=0.87, mrr=0.76, hit=0.84, required=0.87, p95=160)
    result = compare_to_baseline(current, baseline, RegressionThresholds())
    assert not result.passed
    assert len(result.failures) == 4
    assert result.warnings
    assert regression_exit_code(passed=result.passed, fail_on_regression=True) == 1
    assert regression_exit_code(passed=result.passed, fail_on_regression=False) == 0


def test_strategy_urls_and_document_id_is_required() -> None:
    knowledge_base_id = uuid4()
    assert strategy_endpoint("http://127.0.0.1:8000", knowledge_base_id, "dense").endswith(
        "/search/semantic"
    )
    assert strategy_endpoint("http://127.0.0.1:8000", knowledge_base_id, "hybrid").endswith(
        "/search/hybrid"
    )
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "--base-url",
                "http://127.0.0.1:8000",
                "--knowledge-base-id",
                str(knowledge_base_id),
                "--dataset",
                "dataset.jsonl",
                "--manifest",
                "manifest.json",
                "--output",
                "reports",
            ]
        )


def test_single_api_failure_does_not_stop_remaining_cases() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "score": 0.8,
                        "content": "无关结果",
                        "chunk_id": str(uuid4()),
                        "document_name": "synthetic.md",
                        "section_title": "章节",
                        "start_line": 1,
                        "end_line": 1,
                    }
                ]
            },
        )

    cases = [
        RetrievalCase(
            id=f"ret-90{index}",
            split="dev",
            query=f"query {index}",
            query_type="unanswerable",
            difficulty="easy",
            language_filter=None,
            answerable=False,
            gold_evidence=[],
            absence_terms=[f"不存在{index}"],
            notes="",
        )
        for index in range(2)
    ]
    manifest = CorpusManifest(
        dataset_version="v1",
        corpus_filename="synthetic.md",
        sha256="0" * 64,
        created_at=datetime.now(UTC),
        document_description="synthetic",
        expected_section_count=1,
        expected_question_count=2,
        chunking_snapshot={},
        embedding_snapshot={},
        notes="",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = evaluate_strategy(
            client,
            base_url="http://127.0.0.1:8000",
            knowledge_base_id=uuid4(),
            document_id=uuid4(),
            strategy="dense",
            cases=cases,
            manifest=manifest,
            top_k=5,
        )
    assert result.cases[0].error == "HTTP 503"
    assert result.cases[1].error is None
    assert len(requests) == 2
    assert all(request["document_id"] for request in requests)


def test_markdown_reports_include_metrics_and_comparison_sections() -> None:
    dense = summary(recall=0.7, mrr=0.6, hit=0.5, required=0.4, p95=100)
    dense = dense.model_copy(update={"strategy": "dense"})
    hybrid = summary(recall=0.8, mrr=0.7, hit=0.6, required=0.5, p95=120)
    assert "Recall@5" in render_strategy_report(dense)
    comparison = render_comparison(dense, hybrid, [])
    assert "| 指标 | Dense | Hybrid | 差值 |" in comparison
    assert "无答案观察结果" in comparison
