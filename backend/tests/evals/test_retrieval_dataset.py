import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.retrieval.models import RetrievalCase
from evals.retrieval.validate_dataset import (
    load_cases,
    render_checklist,
    validate_dataset,
)

BACKEND = Path(__file__).parents[2]
ROOT = BACKEND.parent
DATASET = BACKEND / "evals/retrieval/datasets/synthetic_retrieval_v1.jsonl"
MANIFEST = BACKEND / "evals/retrieval/datasets/synthetic_corpus_manifest_v1.json"
CORPUS = ROOT / "docs/retrieval-evaluation/synthetic_retrieval_corpus_v1.md"
CHECKLIST = ROOT / "docs/retrieval-evaluation/synthetic_retrieval_checklist_v1.md"


def test_fixed_dataset_loads_and_matches_corpus_manifest_and_checklist() -> None:
    cases, manifest = validate_dataset(
        CORPUS,
        DATASET,
        MANIFEST,
        checklist_path=CHECKLIST,
    )
    assert len(cases) == 24
    assert sum(case.split == "dev" for case in cases) == 18
    assert sum(case.split == "test" for case in cases) == 6
    assert manifest.expected_section_count == 20
    assert CHECKLIST.read_text(encoding="utf-8") == render_checklist(cases)


def test_duplicate_case_id_is_rejected(tmp_path: Path) -> None:
    first = DATASET.read_text(encoding="utf-8").splitlines()[0]
    duplicate = tmp_path / "duplicate.jsonl"
    duplicate.write_text(f"{first}\n{first}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate case IDs"):
        load_cases(duplicate)


def test_answerable_constraints_are_enforced() -> None:
    payload = {
        "id": "ret-999",
        "split": "dev",
        "query": "没有证据的问题",
        "query_type": "semantic",
        "difficulty": "easy",
        "language_filter": None,
        "answerable": True,
        "gold_evidence": [],
        "absence_terms": [],
        "notes": "",
    }
    with pytest.raises(ValidationError, match="positive gold evidence"):
        RetrievalCase.model_validate(payload)


def test_missing_anchor_and_manifest_sha_are_rejected(tmp_path: Path) -> None:
    rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines()]
    rows[0]["gold_evidence"][0]["anchor_text"] = (
        "这是一段长度足够但并不存在于固定语料中的虚构证据文本。"
    )
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="anchor_text is not present"):
        validate_dataset(CORPUS, dataset, MANIFEST)

    manifest_payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest_payload["sha256"] = "0" * 64
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        validate_dataset(CORPUS, DATASET, manifest)
