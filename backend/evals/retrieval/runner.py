from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse
from uuid import UUID

import httpx

from evals.retrieval.baseline import compare_to_baseline, load_baseline
from evals.retrieval.metrics import evaluate_case, summarize
from evals.retrieval.models import (
    CaseEvaluation,
    CorpusManifest,
    EvaluationSummary,
    RegressionThresholds,
    RetrievalCase,
    RetrievalHit,
    Strategy,
)
from evals.retrieval.validate_dataset import load_cases, load_manifest

ENDPOINTS: dict[Strategy, str] = {
    "dense": "search/semantic",
    "hybrid": "search/hybrid",
}


def validate_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
    }:
        raise argparse.ArgumentTypeError("base URL must point to local TraceMind")
    if parsed.query or parsed.fragment:
        raise argparse.ArgumentTypeError("base URL must not contain query or fragment")
    return value.rstrip("/")


def strategy_endpoint(
    base_url: str,
    knowledge_base_id: UUID,
    strategy: Strategy,
) -> str:
    return f"{base_url}/api/v1/knowledge-bases/{knowledge_base_id}/{ENDPOINTS[strategy]}"


def parse_hits(payload: object, manifest: CorpusManifest) -> list[RetrievalHit]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("search API returned an invalid response")
    hits: list[RetrievalHit] = []
    for rank, item in enumerate(payload["items"], start=1):
        if not isinstance(item, dict):
            raise ValueError("search API returned an invalid item")
        document_name = str(item.get("document_name", ""))
        if document_name != manifest.corpus_filename:
            raise ValueError("search API returned a document outside the synthetic corpus")
        hits.append(
            RetrievalHit(
                rank=rank,
                score=item["score"],
                chunk_id=str(item["chunk_id"]),
                document_name=document_name,
                section_title=item.get("section_title"),
                start_line=item.get("start_line"),
                end_line=item.get("end_line"),
                content=item["content"],
            )
        )
    return hits


def evaluate_strategy(
    client: httpx.Client,
    *,
    base_url: str,
    knowledge_base_id: UUID,
    document_id: UUID,
    strategy: Strategy,
    cases: list[RetrievalCase],
    manifest: CorpusManifest,
    top_k: int,
) -> EvaluationSummary:
    endpoint = strategy_endpoint(base_url, knowledge_base_id, strategy)
    evaluations: list[CaseEvaluation] = []
    for case in cases:
        started_at = perf_counter()
        hits: list[RetrievalHit] = []
        error: str | None = None
        try:
            response = client.post(
                endpoint,
                json={
                    "query": case.query,
                    "limit": top_k,
                    "language": case.language_filter,
                    "document_id": str(document_id),
                },
            )
            response.raise_for_status()
            hits = parse_hits(response.json(), manifest)
        except httpx.HTTPStatusError as exc:
            error = f"HTTP {exc.response.status_code}"
        except httpx.TimeoutException:
            error = "request timeout"
        except httpx.HTTPError as exc:
            error = f"API unavailable: {type(exc).__name__}"
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            error = f"invalid API response: {type(exc).__name__}"
        latency_ms = (perf_counter() - started_at) * 1_000
        evaluations.append(
            evaluate_case(
                case,
                hits,
                strategy=strategy,
                latency_ms=latency_ms,
                k=top_k,
                error=error,
            )
        )
    return summarize(strategy, cases, evaluations)


def _format_metric(value: float) -> str:
    return f"{value:.4f}"


def render_strategy_report(summary: EvaluationSummary) -> str:
    lines = [
        f"# {summary.strategy.title()} 固定检索评测报告",
        "",
        f"- Case 数：{summary.case_count}",
        f"- 可回答 Case：{summary.answerable_count}",
        f"- Hit@1：{_format_metric(summary.hit_at_1)}",
        f"- Hit@5：{_format_metric(summary.hit_at_5)}",
        f"- Precision@5：{_format_metric(summary.precision_at_5)}",
        f"- Recall@5：{_format_metric(summary.recall_at_5)}",
        f"- MRR@5：{_format_metric(summary.mrr_at_5)}",
        f"- nDCG@5：{_format_metric(summary.ndcg_at_5)}",
        f"- All-required@5：{_format_metric(summary.all_required_at_5)}",
        f"- P50：{summary.p50_latency_ms:.2f} ms",
        f"- P95：{summary.p95_latency_ms:.2f} ms",
        "",
    ]
    for case in summary.cases:
        state = "observational" if case.observational else ("通过" if case.passed else "未通过")
        lines.extend(
            [
                f"## {case.case_id} · {state}",
                "",
                f"- Query：{case.query}",
                f"- 耗时：{case.latency_ms:.2f} ms",
                f"- 匹配 Gold Evidence：{case.matched_evidence}",
                f"- 未找到 required Evidence：{case.missing_required_evidence}",
                f"- 错误：{case.error or '无'}",
                "",
                "| Rank | Score | Chunk ID | 文档 | 章节 | 行号 | Content preview |",
                "|---:|---:|---|---|---|---|---|",
            ]
        )
        for hit in case.hits:
            preview = hit.content[:160].replace("|", "\\|").replace("\n", " ")
            line_range = f"{hit.start_line or '-'}-{hit.end_line or '-'}"
            lines.append(
                f"| {hit.rank} | {hit.score:.6f} | {hit.chunk_id} | "
                f"{hit.document_name} | {hit.section_title or '-'} | {line_range} | {preview} |"
            )
        if not case.hits:
            lines.append("| - | - | - | - | - | - | 无返回结果 |")
        lines.append("")
    if summary.unanswerable_observations:
        lines.extend(["## 无答案观察结果", ""])
        for observation in summary.unanswerable_observations:
            lines.append(
                f"- {observation.case_id}：returned_count={observation.returned_count}, "
                f"top1_score={observation.top1_score}, "
                f"top1_document={observation.top1_document}, "
                f"top1_section={observation.top1_section}, "
                f"top1_content_preview={observation.top1_content_preview}"
            )
        lines.append("")
    return "\n".join(lines)


def _case_ids(summary: EvaluationSummary, passed: bool) -> set[str]:
    return {case.case_id for case in summary.cases if case.passed is passed}


def render_comparison(
    dense: EvaluationSummary,
    hybrid: EvaluationSummary,
    cases: list[RetrievalCase],
) -> str:
    metrics = [
        ("Hit@1", dense.hit_at_1, hybrid.hit_at_1),
        ("Recall@5", dense.recall_at_5, hybrid.recall_at_5),
        ("MRR@5", dense.mrr_at_5, hybrid.mrr_at_5),
        ("nDCG@5", dense.ndcg_at_5, hybrid.ndcg_at_5),
        ("All-required@5", dense.all_required_at_5, hybrid.all_required_at_5),
        ("P50", dense.p50_latency_ms, hybrid.p50_latency_ms),
        ("P95", dense.p95_latency_ms, hybrid.p95_latency_ms),
    ]
    lines = [
        "# Dense 与 Hybrid 固定检索对比",
        "",
        "| 指标 | Dense | Hybrid | 差值 |",
        "|---|---:|---:|---:|",
    ]
    for name, dense_value, hybrid_value in metrics:
        lines.append(
            f"| {name} | {dense_value:.4f} | {hybrid_value:.4f} | "
            f"{hybrid_value - dense_value:+.4f} |"
        )
    dense_passed = _case_ids(dense, True)
    hybrid_passed = _case_ids(hybrid, True)
    both_failed = _case_ids(dense, False) & _case_ids(hybrid, False)
    case_by_id = {case.id: case for case in cases}
    multi_incomplete = sorted(
        case_id
        for case_id in both_failed | (dense_passed ^ hybrid_passed)
        if case_by_id[case_id].query_type == "multi_evidence"
    )
    lines.extend(
        [
            "",
            f"- Dense 独有通过：{', '.join(sorted(dense_passed - hybrid_passed)) or '无'}",
            f"- Hybrid 独有通过：{', '.join(sorted(hybrid_passed - dense_passed)) or '无'}",
            f"- 两者都失败：{', '.join(sorted(both_failed)) or '无'}",
            f"- 多证据不完整：{', '.join(multi_incomplete) or '无'}",
            "",
            "## 无答案观察结果",
            "",
        ]
    )
    for strategy_summary in (dense, hybrid):
        lines.append(f"### {strategy_summary.strategy.title()}")
        lines.append("")
        for item in strategy_summary.unanswerable_observations:
            lines.append(
                f"- {item.case_id}：返回 {item.returned_count} 条，"
                f"Top1={item.top1_document or '-'} / {item.top1_section or '-'}，"
                f"score={item.top1_score}"
            )
        lines.append("")
    return "\n".join(lines)


def write_summary(output: Path, summary: EvaluationSummary) -> None:
    output.mkdir(parents=True, exist_ok=True)
    stem = summary.strategy
    (output / f"{stem}.json").write_text(
        json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / f"{stem}.md").write_text(
        render_strategy_report(summary),
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="通过 TraceMind HTTP API 运行固定检索评测")
    parser.add_argument("--base-url", type=validate_base_url, required=True)
    parser.add_argument("--knowledge-base-id", type=UUID, required=True)
    parser.add_argument("--document-id", type=UUID, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--strategies",
        nargs="+",
        choices=("dense", "hybrid"),
        default=("dense", "hybrid"),
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--split", choices=("all", "dev", "test"), default="all")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--fail-on-regression", action="store_true")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--recall-drop", type=float, default=0.02)
    parser.add_argument("--mrr-drop", type=float, default=0.03)
    parser.add_argument("--all-required-drop", type=float, default=0.02)
    parser.add_argument("--hit-at-1-drop", type=float, default=0.05)
    parser.add_argument("--p95-increase-ratio", type=float, default=0.50)
    return parser


def _selected_cases(cases: Iterable[RetrievalCase], split: str) -> list[RetrievalCase]:
    return [case for case in cases if split == "all" or case.split == split]


def regression_exit_code(*, passed: bool, fail_on_regression: bool) -> int:
    return 1 if fail_on_regression and not passed else 0


def main() -> int:
    args = build_parser().parse_args()
    if args.top_k <= 0:
        raise SystemExit("--top-k must be greater than zero")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be greater than zero")
    if args.fail_on_regression and args.baseline is None:
        raise SystemExit("--fail-on-regression requires --baseline")
    cases = _selected_cases(load_cases(args.dataset), args.split)
    manifest = load_manifest(args.manifest)
    summaries: dict[Strategy, EvaluationSummary] = {}
    with httpx.Client(timeout=args.timeout, trust_env=False) as client:
        for strategy_value in args.strategies:
            strategy: Strategy = strategy_value
            summary = evaluate_strategy(
                client,
                base_url=args.base_url,
                knowledge_base_id=args.knowledge_base_id,
                document_id=args.document_id,
                strategy=strategy,
                cases=cases,
                manifest=manifest,
                top_k=args.top_k,
            )
            summaries[strategy] = summary
            write_summary(args.output, summary)
    if {"dense", "hybrid"}.issubset(summaries):
        (args.output / "comparison.md").write_text(
            render_comparison(summaries["dense"], summaries["hybrid"], cases),
            encoding="utf-8",
        )
    all_failed = all(
        case.error is not None for summary in summaries.values() for case in summary.cases
    )
    if all_failed:
        print("所有评测请求均失败，请检查本地 TraceMind API 与 document_id。", file=sys.stderr)
        return 2
    if args.baseline is not None:
        baseline = load_baseline(args.baseline)
        current = summaries.get(baseline.strategy)
        if current is None:
            raise SystemExit("baseline strategy was not selected")
        result = compare_to_baseline(
            current,
            baseline,
            RegressionThresholds(
                recall_at_5_drop=args.recall_drop,
                mrr_at_5_drop=args.mrr_drop,
                all_required_at_5_drop=args.all_required_drop,
                hit_at_1_drop=args.hit_at_1_drop,
                p95_latency_increase_ratio=args.p95_increase_ratio,
            ),
        )
        (args.output / "regression.json").write_text(
            json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        exit_code = regression_exit_code(
            passed=result.passed,
            fail_on_regression=args.fail_on_regression,
        )
        if exit_code:
            return exit_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
