from __future__ import annotations

import json
from pathlib import Path

from evals.retrieval.models import (
    EvaluationSummary,
    RegressionResult,
    RegressionThresholds,
)


def save_baseline(summary: EvaluationSummary, path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"baseline already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_baseline(path: Path) -> EvaluationSummary:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"baseline could not be loaded: {path}") from exc
    return EvaluationSummary.model_validate(payload)


def compare_to_baseline(
    current: EvaluationSummary,
    baseline: EvaluationSummary,
    thresholds: RegressionThresholds | None = None,
) -> RegressionResult:
    limits = thresholds or RegressionThresholds()
    deltas = {
        "recall_at_5": current.recall_at_5 - baseline.recall_at_5,
        "mrr_at_5": current.mrr_at_5 - baseline.mrr_at_5,
        "all_required_at_5": current.all_required_at_5 - baseline.all_required_at_5,
        "hit_at_1": current.hit_at_1 - baseline.hit_at_1,
        "p95_latency_ms": current.p95_latency_ms - baseline.p95_latency_ms,
    }
    failures: list[str] = []
    checks = (
        ("recall_at_5", limits.recall_at_5_drop),
        ("mrr_at_5", limits.mrr_at_5_drop),
        ("all_required_at_5", limits.all_required_at_5_drop),
        ("hit_at_1", limits.hit_at_1_drop),
    )
    for metric, allowed_drop in checks:
        if deltas[metric] < -allowed_drop:
            failures.append(
                f"{metric} decreased by {-deltas[metric]:.4f}, threshold is {allowed_drop:.4f}"
            )
    warnings: list[str] = []
    if baseline.p95_latency_ms > 0 and current.p95_latency_ms > baseline.p95_latency_ms * (
        1 + limits.p95_latency_increase_ratio
    ):
        warnings.append(
            f"p95_latency_ms increased by more than {limits.p95_latency_increase_ratio:.0%}"
        )
    return RegressionResult(
        passed=not failures,
        failures=failures,
        warnings=warnings,
        deltas=deltas,
    )
