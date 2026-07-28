from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from evals.retrieval.models import CorpusManifest, RetrievalCase

EXPECTED_QUERY_TYPES = {
    "semantic": 6,
    "config_exact": 4,
    "error_code": 3,
    "short_query": 3,
    "multi_evidence": 4,
    "concept_disambiguation": 2,
    "unanswerable": 2,
}
EXPECTED_SPLITS = {"dev": 18, "test": 6}


def load_cases(path: Path) -> list[RetrievalCase]:
    cases: list[RetrievalCase] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"dataset could not be read: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            cases.append(RetrievalCase.model_validate_json(line))
        except (ValidationError, ValueError) as exc:
            raise ValueError(f"invalid dataset line {line_number}: {exc}") from exc
    ids = [case.id for case in cases]
    duplicates = sorted(case_id for case_id, count in Counter(ids).items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate case IDs: {', '.join(duplicates)}")
    return cases


def load_manifest(path: Path) -> CorpusManifest:
    try:
        return CorpusManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise ValueError(f"manifest could not be loaded: {path}") from exc


def corpus_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _section_for_line(lines: list[str], line_number: int) -> str | None:
    for index in range(line_number - 1, -1, -1):
        if lines[index].startswith("## "):
            return lines[index][3:].strip()
    return None


def validate_dataset(
    corpus_path: Path,
    dataset_path: Path,
    manifest_path: Path,
    *,
    checklist_path: Path | None = None,
) -> tuple[list[RetrievalCase], CorpusManifest]:
    cases = load_cases(dataset_path)
    manifest = load_manifest(manifest_path)
    corpus = corpus_path.read_text(encoding="utf-8")
    lines = corpus.splitlines()
    headings = [line[3:].strip() for line in lines if line.startswith("## ")]
    errors: list[str] = []

    if corpus_sha256(corpus_path) != manifest.sha256:
        errors.append("manifest SHA-256 does not match the corpus")
    if corpus_path.name != manifest.corpus_filename:
        errors.append("manifest corpus_filename does not match the corpus path")
    if len(headings) != manifest.expected_section_count:
        errors.append("corpus section count does not match the manifest")
    if len(cases) != manifest.expected_question_count:
        errors.append("dataset question count does not match the manifest")
    if Counter(case.split for case in cases) != Counter(EXPECTED_SPLITS):
        errors.append(f"split distribution must be {EXPECTED_SPLITS}")
    if Counter(case.query_type for case in cases) != Counter(EXPECTED_QUERY_TYPES):
        errors.append(f"query type distribution must be {EXPECTED_QUERY_TYPES}")

    unanswerable = [case for case in cases if not case.answerable]
    if len(unanswerable) != 2:
        errors.append("dataset must contain exactly two unanswerable cases")
    for case in cases:
        for evidence_index, evidence in enumerate(case.gold_evidence):
            label = f"{case.id} evidence {evidence_index}"
            if not 20 <= len(evidence.anchor_text) <= 300:
                errors.append(f"{label} anchor_text length must be between 20 and 300")
            if evidence.section_title not in headings:
                errors.append(f"{label} section_title does not exist")
            if evidence.line_end > len(lines):
                errors.append(f"{label} line range exceeds the corpus")
                continue
            line_text = "\n".join(lines[evidence.line_start - 1 : evidence.line_end])
            if evidence.anchor_text not in line_text:
                errors.append(f"{label} anchor_text is not present in the declared line range")
            actual_section = _section_for_line(lines, evidence.line_start)
            if actual_section != evidence.section_title:
                errors.append(
                    f"{label} line range belongs to {actual_section!r}, "
                    f"not {evidence.section_title!r}"
                )
        for term in case.absence_terms:
            if term in corpus:
                errors.append(f"{case.id} absence term unexpectedly exists in the corpus: {term}")

    if checklist_path is not None:
        expected_checklist = render_checklist(cases)
        try:
            actual_checklist = checklist_path.read_text(encoding="utf-8")
        except OSError:
            errors.append(f"checklist could not be read: {checklist_path}")
        else:
            if actual_checklist != expected_checklist:
                errors.append("Markdown checklist is not synchronized with the JSONL dataset")
    if errors:
        raise ValueError("\n".join(errors))
    return cases, manifest


def _escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_checklist(cases: list[RetrievalCase]) -> str:
    lines = [
        "# 固定检索评测人工清单 v1",
        "",
        "> 本清单由机器可读 JSONL 数据集生成。只能上传配套语料，不能上传本清单。",
        "",
        (
            "| 编号 | 问题 | 类型 | 难度 | 预期章节 | 预期关键证据 | "
            "Top 5 通过条件 | 实际结果 | 是否通过 |"
        ),
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for case in cases:
        sections = "；".join(dict.fromkeys(item.section_title for item in case.gold_evidence))
        anchors = "；".join(item.anchor_text for item in case.gold_evidence if item.required)
        if case.answerable:
            required_count = sum(item.required for item in case.gold_evidence)
            condition = f"Top 5 命中全部 {required_count} 条 required 证据"
        else:
            sections = "无明确答案"
            anchors = "观察返回内容，不自动判定"
            condition = "observational，不计入默认回归失败"
        lines.append(
            "| "
            + " | ".join(
                [
                    case.id,
                    _escape_table(case.query),
                    case.query_type,
                    case.difficulty,
                    _escape_table(sections),
                    _escape_table(anchors),
                    condition,
                    "",
                    "",
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 汇总",
            "",
            f"- 总问题数：{len(cases)}",
            "- 通过数：",
            "- 未通过数：",
            "- Hit@1：",
            "- Recall@5：",
            "- MRR@5：",
            "- 多证据完整命中率：",
            "- 无答案观察结果：",
            "- P50：",
            "- P95：",
            "",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="校验固定检索语料和 Gold Dataset")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--checklist", type=Path)
    parser.add_argument("--write-checklist", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    cases = load_cases(args.dataset)
    if args.write_checklist is not None:
        args.write_checklist.parent.mkdir(parents=True, exist_ok=True)
        args.write_checklist.write_text(render_checklist(cases), encoding="utf-8")
    checklist = (
        args.checklist
        or args.write_checklist
        or args.corpus.with_name("synthetic_retrieval_checklist_v1.md")
    )
    validate_dataset(
        args.corpus,
        args.dataset,
        args.manifest,
        checklist_path=checklist,
    )
    print(f"数据集校验通过：{len(cases)} 条问题")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
