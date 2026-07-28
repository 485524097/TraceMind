from __future__ import annotations

import re
import unicodedata

from evals.retrieval.models import GoldEvidence, RetrievalHit

_WHITESPACE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    return _WHITESPACE.sub(" ", normalized).strip().lower()


def line_ranges_overlap(
    first_start: int,
    first_end: int,
    second_start: int,
    second_end: int,
) -> bool:
    return first_start <= second_end and second_start <= first_end


def evidence_matches_hit(evidence: GoldEvidence, hit: RetrievalHit) -> bool:
    if normalize_text(evidence.document_name) != normalize_text(hit.document_name):
        return False
    if (
        hit.start_line is not None
        and hit.end_line is not None
        and not line_ranges_overlap(
            evidence.line_start,
            evidence.line_end,
            hit.start_line,
            hit.end_line,
        )
    ):
        return False
    if hit.section_title and normalize_text(evidence.section_title) != normalize_text(
        hit.section_title
    ):
        return False
    anchor = normalize_text(evidence.anchor_text)
    content = normalize_text(hit.content)
    return anchor in content or (bool(content) and content in anchor)


def matched_evidence_indexes(
    evidence: list[GoldEvidence],
    hit: RetrievalHit,
) -> list[int]:
    return [index for index, item in enumerate(evidence) if evidence_matches_hit(item, hit)]


def deduplicate_hits(hits: list[RetrievalHit]) -> list[RetrievalHit]:
    seen: set[tuple[str, str, int | None, int | None, str]] = set()
    unique: list[RetrievalHit] = []
    for hit in hits:
        key = (
            hit.chunk_id,
            normalize_text(hit.document_name),
            hit.start_line,
            hit.end_line,
            normalize_text(hit.content),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(hit.model_copy(update={"rank": len(unique) + 1}))
    return unique
