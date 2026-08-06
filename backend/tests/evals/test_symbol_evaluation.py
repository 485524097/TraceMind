import json
from pathlib import Path

from app.parsing.base import ParseContext
from app.parsing.java import JavaTreeSitterParser
from evals.symbol.models import SymbolBaseline
from evals.symbol.runner import (
    baseline_failures,
    corpus_files,
    evaluate_case,
    load_cases,
    summarize,
    validate_base_url,
)

BACKEND = Path(__file__).parents[2]
SYMBOL_EVAL = BACKEND / "evals/symbol"
DATASET = SYMBOL_EVAL / "dataset.jsonl"
CORPUS = SYMBOL_EVAL / "corpus"
BASELINE = SYMBOL_EVAL / "baseline.json"


def test_symbol_dataset_and_corpus_are_independent_and_complete() -> None:
    cases = load_cases(DATASET)
    files = corpus_files(CORPUS)

    assert len(cases) == 12
    assert {path.relative_to(CORPUS).as_posix() for path in files} == {
        "src/main/java/demo/Item.java",
        "src/main/java/demo/Outer.java",
        "src/main/java/demo/UnicodeService.java",
        "src/main/java/demo/UserService.java",
        "src/main/java/example/UserService.java",
    }
    assert {case.expected_symbol_scope_mode for case in cases} == {"none", "exact", "fallback"}
    assert {case.expected_symbol_scope_reason for case in cases} >= {None, "not_found", "ambiguous"}
    assert validate_base_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"


def test_symbol_corpus_runs_through_real_java_parser_with_lookup_metadata() -> None:
    parser = JavaTreeSitterParser()
    blocks = [
        block
        for path in corpus_files(CORPUS)
        for block in parser.parse(
            path,
            ParseContext(max_extracted_chars=100_000, max_pdf_pages=1),
        ).blocks
        if block.symbol_kind is not None
    ]
    identities = {
        (block.symbol_kind, block.symbol_qualified_name, block.symbol_signature) for block in blocks
    }

    assert (
        "method",
        "demo.UserService.source",
        "public String source(String username)",
    ) in identities
    assert ("method", "demo.UserService.source", "public String source(int id)") in identities
    assert ("method", "demo.Outer.Nested.run", "public String run(String input)") in identities
    assert ("constructor", "demo.Item.Item", "public Item") in identities
    assert ("method", "demo.用户服务.查询", "public String 查询(String 名称)") in identities
    assert all(block.symbol_lookup_keys for block in blocks)


def test_case_assertions_and_independent_metrics_are_explicit() -> None:
    case = load_cases(DATASET)[0]
    payload: dict[str, object] = {
        "symbol_scope_mode": "exact",
        "symbol_scope_reason": None,
        "scoped_symbol_kind": "method",
        "scoped_symbol_qualified_name": "demo.UserService.source",
        "scoped_symbol_signature": "public String source(String username)",
        "items": [
            {
                "relative_path": "src/main/java/demo/UserService.java",
                "symbol_kind": "method",
                "symbol_qualified_name": "demo.UserService.source",
                "symbol_signature": "public String source(String username)",
                "ranking_mode": "symbol_exact",
                "start_line": 14,
                "end_line": 17,
            }
        ],
    }
    result = evaluate_case(case, payload, 12.5)
    summary = summarize([case], [result], cleanup_succeeded=True)
    baseline = SymbolBaseline.model_validate_json(BASELINE.read_text(encoding="utf-8"))

    assert result.passed
    assert summary.case_pass_rate == 1
    assert summary.scope_resolution_accuracy == 1
    assert summary.exact_target_recall_at_5 == 1
    assert summary.signature_exclusion_accuracy == 1
    assert summary.path_disambiguation_accuracy == 1
    assert summary.p95_latency_ms == 12.5
    assert baseline_failures(summary, baseline) == []

    payload["items"] = [
        {
            **json.loads(json.dumps(payload["items"][0])),
            "symbol_signature": "public String source(int id)",
        }
    ]
    failed = evaluate_case(case, payload, 1)
    assert not failed.passed
    assert any("required signature" in failure for failure in failed.failures)
    assert any("forbidden signature" in failure for failure in failed.failures)
