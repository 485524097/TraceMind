from evals.retrieval.matching import (
    deduplicate_hits,
    evidence_matches_hit,
    normalize_text,
)
from evals.retrieval.models import GoldEvidence, RetrievalHit


def evidence() -> GoldEvidence:
    return GoldEvidence(
        document_name="synthetic.md",
        section_title="路径处理",
        line_start=20,
        line_end=24,
        anchor_text="主路径容量不足时，可以拆分为两条互不共享脆弱节点的路径。",
        relevance=2,
        required=True,
        notes="",
    )


def hit(
    *,
    chunk_id: str = "changed-id",
    section_title: str | None = "路径处理",
    start_line: int | None = 18,
    end_line: int | None = 22,
    content: str = "主路径容量不足时，可以拆分为两条互不共享脆弱节点的路径。",
) -> RetrievalHit:
    return RetrievalHit(
        rank=1,
        score=0.8,
        chunk_id=chunk_id,
        document_name="synthetic.md",
        section_title=section_title,
        start_line=start_line,
        end_line=end_line,
        content=content,
    )


def test_normalization_is_limited_and_deterministic() -> None:
    assert normalize_text("ＡBC\r\n  路径") == "abc 路径"


def test_line_overlap_section_and_anchor_match_without_chunk_id() -> None:
    assert evidence_matches_hit(evidence(), hit(chunk_id="any-new-id"))
    assert not evidence_matches_hit(evidence(), hit(start_line=25, end_line=30))
    assert not evidence_matches_hit(evidence(), hit(section_title="储能处理"))


def test_chunk_boundary_change_still_matches_when_chunk_is_inside_longer_anchor() -> None:
    changed = hit(
        start_line=20,
        end_line=20,
        content="可以拆分为两条互不共享脆弱节点的路径",
    )
    assert evidence_matches_hit(evidence(), changed)


def test_duplicate_retrieval_results_are_removed_and_reranked() -> None:
    duplicate = hit(chunk_id="same")
    unique = deduplicate_hits([duplicate, duplicate, hit(chunk_id="other")])
    assert [item.chunk_id for item in unique] == ["same", "other"]
    assert [item.rank for item in unique] == [1, 2]
