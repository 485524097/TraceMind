from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from pydantic import ValidationError

from evals.symbol.models import (
    SymbolBaseline,
    SymbolCaseResult,
    SymbolEvaluationCase,
    SymbolEvaluationSummary,
    SymbolHit,
)

JAVA_CONTENT_TYPE = "text/x-java-source"


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


def load_cases(path: Path) -> list[SymbolEvaluationCase]:
    cases: list[SymbolEvaluationCase] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"symbol dataset could not be read: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            cases.append(SymbolEvaluationCase.model_validate_json(line))
        except (ValidationError, ValueError) as exc:
            raise ValueError(f"invalid symbol dataset line {line_number}: {exc}") from exc
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("symbol dataset contains duplicate case IDs")
    if not 10 <= len(cases) <= 14:
        raise ValueError("symbol dataset must contain between 10 and 14 focused cases")
    return cases


def corpus_files(root: Path) -> list[Path]:
    files = sorted(path for path in root.rglob("*.java") if path.is_file())
    if not files:
        raise ValueError("symbol corpus contains no Java files")
    return files


class SymbolEvaluationEnvironment:
    def __init__(self, client: httpx.Client, base_url: str, corpus_root: Path) -> None:
        self.client = client
        self.api = f"{base_url}/api/v1"
        self.corpus_root = corpus_root
        self.knowledge_base_id: str | None = None
        self.document_ids: dict[str, str] = {}

    def provision(self, poll_timeout: float) -> None:
        response = self.client.post(
            f"{self.api}/knowledge-bases",
            json={"name": f"symbol-eval-{uuid4().hex}", "description": "temporary evaluation"},
        )
        response.raise_for_status()
        self.knowledge_base_id = str(response.json()["id"])
        for path in corpus_files(self.corpus_root):
            relative_path = path.relative_to(self.corpus_root).as_posix()
            with path.open("rb") as source:
                upload = self.client.post(
                    f"{self.api}/knowledge-bases/{self.knowledge_base_id}/documents",
                    files={"file": (path.name, source, JAVA_CONTENT_TYPE)},
                    data={"relative_path": relative_path},
                )
            upload.raise_for_status()
            document = upload.json()["document"]
            self.document_ids[relative_path] = str(document["id"])
        self._wait_until_indexed(poll_timeout)

    def _wait_until_indexed(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        pending = set(self.document_ids.values())
        while pending and time.monotonic() < deadline:
            for document_id in tuple(pending):
                response = self.client.get(
                    f"{self.api}/knowledge-bases/{self.knowledge_base_id}/documents/{document_id}"
                )
                response.raise_for_status()
                version = response.json()["latest_version"]
                if version["parse_status"] == "failed" or version["index_status"] == "failed":
                    raise RuntimeError(f"document processing failed for {document_id}")
                if (
                    version["parse_status"] == "succeeded"
                    and version["index_status"] == "succeeded"
                ):
                    pending.remove(document_id)
            if pending:
                time.sleep(0.5)
        if pending:
            raise TimeoutError(f"timed out waiting for {len(pending)} indexed documents")

    def search(self, case: SymbolEvaluationCase, top_k: int) -> tuple[dict[str, object], float]:
        if self.knowledge_base_id is None:
            raise RuntimeError("evaluation environment is not provisioned")
        body: dict[str, object] = {"query": case.query, "language": "java", "limit": top_k}
        if case.document_scope_path is not None:
            body["document_id"] = self.document_ids[case.document_scope_path]
        started_at = time.perf_counter()
        response = self.client.post(
            f"{self.api}/knowledge-bases/{self.knowledge_base_id}/search/hybrid",
            json=body,
        )
        latency_ms = (time.perf_counter() - started_at) * 1_000
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError("symbol search API returned an invalid response")
        return payload, latency_ms

    def cleanup(self) -> list[str]:
        failures: list[str] = []
        if self.knowledge_base_id is None:
            return failures
        for relative_path, document_id in self.document_ids.items():
            try:
                response = self.client.delete(
                    f"{self.api}/knowledge-bases/{self.knowledge_base_id}/documents/{document_id}"
                )
                if response.status_code != 204:
                    failures.append(
                        f"document cleanup failed for {relative_path}: HTTP {response.status_code}"
                    )
            except httpx.HTTPError as exc:
                failures.append(
                    f"document cleanup failed for {relative_path}: {type(exc).__name__}"
                )
        try:
            response = self.client.delete(f"{self.api}/knowledge-bases/{self.knowledge_base_id}")
            if response.status_code != 204:
                failures.append(f"knowledge base cleanup failed: HTTP {response.status_code}")
        except httpx.HTTPError as exc:
            failures.append(f"knowledge base cleanup failed: {type(exc).__name__}")
        return failures


def _safe_optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def evaluate_case(
    case: SymbolEvaluationCase, payload: dict[str, object], latency_ms: float
) -> SymbolCaseResult:
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("search response items must be a list")
    hits: list[SymbolHit] = []
    for item in raw_items:
        if not isinstance(item, dict):
            raise ValueError("search response item must be an object")
        hits.append(
            SymbolHit(
                relative_path=str(item.get("relative_path") or ""),
                symbol_kind=_safe_optional_string(item.get("symbol_kind")),
                symbol_qualified_name=_safe_optional_string(item.get("symbol_qualified_name")),
                symbol_signature=_safe_optional_string(item.get("symbol_signature")),
                ranking_mode=_safe_optional_string(item.get("ranking_mode")),
                start_line=item.get("start_line")
                if isinstance(item.get("start_line"), int)
                else None,
                end_line=item.get("end_line") if isinstance(item.get("end_line"), int) else None,
            )
        )
    actual = {
        "symbol_scope_mode": str(payload.get("symbol_scope_mode") or "none"),
        "symbol_scope_reason": _safe_optional_string(payload.get("symbol_scope_reason")),
        "scoped_symbol_kind": _safe_optional_string(payload.get("scoped_symbol_kind")),
        "scoped_symbol_qualified_name": _safe_optional_string(
            payload.get("scoped_symbol_qualified_name")
        ),
        "scoped_symbol_signature": _safe_optional_string(payload.get("scoped_symbol_signature")),
    }
    expected = {
        "symbol_scope_mode": case.expected_symbol_scope_mode,
        "symbol_scope_reason": case.expected_symbol_scope_reason,
        "scoped_symbol_kind": case.expected_symbol_kind,
        "scoped_symbol_qualified_name": case.expected_scoped_symbol_qualified_name,
        "scoped_symbol_signature": case.expected_scoped_symbol_signature,
    }
    failures = [
        f"{key}: expected {expected[key]!r}, got {actual[key]!r}"
        for key in expected
        if actual[key] != expected[key]
    ]
    signatures = [hit.symbol_signature for hit in hits]
    for signature in case.required_result_signatures:
        if signature not in signatures:
            failures.append(f"required signature not found: {signature}")
    for signature in case.forbidden_symbol_signatures:
        if signature in signatures:
            failures.append(f"forbidden signature returned: {signature}")
    if case.expected_relative_path is not None and not any(
        hit.relative_path == case.expected_relative_path for hit in hits
    ):
        failures.append(f"expected path not found: {case.expected_relative_path}")
    if case.expected_ranking_mode is not None and not any(
        hit.ranking_mode == case.expected_ranking_mode for hit in hits
    ):
        failures.append(f"expected ranking mode not found: {case.expected_ranking_mode}")
    if case.target_in_top_k and not hits:
        failures.append("expected a target in top k but no hits were returned")
    if case.target_in_top_k and any(
        hit.start_line is None or hit.end_line is None
        for hit in hits
        if hit.symbol_signature in case.required_result_signatures
    ):
        failures.append("target symbol is missing a source line range")
    return SymbolCaseResult(
        case_id=case.id,
        query=case.query,
        passed=not failures,
        failures=failures,
        latency_ms=latency_ms,
        hits=hits,
        **actual,
    )


def _accuracy(values: Iterable[bool]) -> float:
    materialized = list(values)
    return sum(materialized) / len(materialized) if materialized else 1.0


def summarize(
    cases: list[SymbolEvaluationCase],
    results: list[SymbolCaseResult],
    *,
    cleanup_succeeded: bool,
) -> SymbolEvaluationSummary:
    by_id = {result.case_id: result for result in results}
    latencies = sorted(result.latency_ms for result in results)
    p95_index = max(0, min(len(latencies) - 1, int(0.95 * len(latencies) + 0.9999) - 1))
    return SymbolEvaluationSummary(
        case_count=len(cases),
        passed_count=sum(result.passed for result in results),
        case_pass_rate=_accuracy(result.passed for result in results),
        scope_resolution_accuracy=_accuracy(
            by_id[case.id].symbol_scope_mode == case.expected_symbol_scope_mode
            and by_id[case.id].scoped_symbol_kind == case.expected_symbol_kind
            and by_id[case.id].scoped_symbol_qualified_name
            == case.expected_scoped_symbol_qualified_name
            and by_id[case.id].scoped_symbol_signature == case.expected_scoped_symbol_signature
            for case in cases
        ),
        exact_target_recall_at_5=_accuracy(
            all(
                signature in [hit.symbol_signature for hit in by_id[case.id].hits]
                for signature in case.required_result_signatures
            )
            for case in cases
            if case.target_in_top_k
        ),
        signature_exclusion_accuracy=_accuracy(
            all(
                signature not in [hit.symbol_signature for hit in by_id[case.id].hits]
                for signature in case.forbidden_symbol_signatures
            )
            for case in cases
            if case.forbidden_symbol_signatures
        ),
        fallback_reason_accuracy=_accuracy(
            by_id[case.id].symbol_scope_reason == case.expected_symbol_scope_reason
            for case in cases
            if case.expected_symbol_scope_mode == "fallback"
        ),
        negative_trigger_accuracy=_accuracy(
            by_id[case.id].symbol_scope_mode == "none"
            for case in cases
            if case.expected_symbol_scope_mode == "none"
        ),
        path_disambiguation_accuracy=_accuracy(
            any(hit.relative_path == case.expected_relative_path for hit in by_id[case.id].hits)
            for case in cases
            if case.expected_relative_path is not None
            and (case.document_scope_path is not None or "/" in case.query)
        ),
        p95_latency_ms=latencies[p95_index] if latencies else 0,
        cleanup_succeeded=cleanup_succeeded,
        cases=results,
    )


def baseline_failures(summary: SymbolEvaluationSummary, baseline: SymbolBaseline) -> list[str]:
    failures = []
    for field in (
        "case_pass_rate",
        "scope_resolution_accuracy",
        "exact_target_recall_at_5",
        "signature_exclusion_accuracy",
        "fallback_reason_accuracy",
        "negative_trigger_accuracy",
        "path_disambiguation_accuracy",
    ):
        if getattr(summary, field) < getattr(baseline, field):
            failures.append(f"{field} is below baseline")
    if (
        baseline.p95_latency_ms_hard_limit is not None
        and summary.p95_latency_ms > baseline.p95_latency_ms_hard_limit
    ):
        failures.append("p95_latency_ms exceeds hard limit")
    return failures


def render_report(summary: SymbolEvaluationSummary) -> str:
    lines = [
        "# Java Symbol Retrieval Evaluation",
        "",
        f"- Case Pass Rate: {summary.case_pass_rate:.4f}",
        f"- Scope Resolution Accuracy: {summary.scope_resolution_accuracy:.4f}",
        f"- Exact Target Recall@5: {summary.exact_target_recall_at_5:.4f}",
        f"- Signature Exclusion Accuracy: {summary.signature_exclusion_accuracy:.4f}",
        f"- Fallback Reason Accuracy: {summary.fallback_reason_accuracy:.4f}",
        f"- Negative Trigger Accuracy: {summary.negative_trigger_accuracy:.4f}",
        f"- Path Disambiguation Accuracy: {summary.path_disambiguation_accuracy:.4f}",
        f"- P95 Latency: {summary.p95_latency_ms:.2f} ms (observational)",
        f"- Cleanup: {'succeeded' if summary.cleanup_succeeded else 'failed'}",
        "",
    ]
    for result in summary.cases:
        lines.extend(
            [
                f"## {result.case_id} · {'passed' if result.passed else 'failed'}",
                "",
                f"- Query: {result.query}",
                f"- Scope: {result.symbol_scope_mode} / {result.symbol_scope_reason or '-'}",
                f"- Failures: {'; '.join(result.failures) or 'none'}",
                f"- Latency: {result.latency_ms:.2f} ms",
                "",
            ]
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run isolated Java symbol retrieval evaluation")
    parser.add_argument("--base-url", type=validate_base_url, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--poll-timeout", type=float, default=180)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    cases = load_cases(args.dataset)
    baseline = SymbolBaseline.model_validate_json(args.baseline.read_text(encoding="utf-8"))
    results: list[SymbolCaseResult] = []
    cleanup_failures: list[str] = []
    environment: SymbolEvaluationEnvironment | None = None
    with httpx.Client(timeout=args.timeout, trust_env=False) as client:
        environment = SymbolEvaluationEnvironment(client, args.base_url, args.corpus_root)
        try:
            environment.provision(args.poll_timeout)
            for case in cases:
                payload, latency_ms = environment.search(case, args.top_k)
                results.append(evaluate_case(case, payload, latency_ms))
        finally:
            cleanup_failures = environment.cleanup()
    if cleanup_failures:
        print("; ".join(cleanup_failures), file=sys.stderr)
    summary = summarize(cases, results, cleanup_succeeded=not cleanup_failures)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "symbol.json").write_text(
        json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output / "symbol.md").write_text(render_report(summary), encoding="utf-8")
    failures = baseline_failures(summary, baseline)
    if cleanup_failures:
        return 3
    if failures:
        print("; ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
