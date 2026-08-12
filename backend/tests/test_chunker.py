import hashlib

from app.parsing.base import ParsedBlock
from app.parsing.chunker import DeterministicChunker


def test_chunker_is_deterministic_and_sets_index_hash_and_char_count() -> None:
    blocks = [ParsedBlock("中文内容", "paragraph", start_line=1, end_line=1, section_title="说明")]
    chunker = DeterministicChunker(max_chars=20, overlap_chars=4)

    first = chunker.chunk(blocks)
    second = chunker.chunk(blocks)

    assert first == second
    assert first[0].chunk_index == 0
    assert first[0].char_count == len("中文内容")
    assert first[0].content_hash == hashlib.sha256("中文内容".encode()).hexdigest()


def test_chunker_uses_whole_line_overlap_and_source_range() -> None:
    block = ParsedBlock("1111\n2222\n3333", "code", start_line=10, end_line=12, language="python")
    chunks = DeterministicChunker(max_chars=9, overlap_chars=4).chunk([block])

    assert [chunk.content for chunk in chunks] == ["1111\n2222", "2222\n3333"]
    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [(10, 11), (11, 12)]
    assert [chunk.chunk_index for chunk in chunks] == [0, 1]


def test_chunker_hard_splits_long_single_line_with_character_overlap() -> None:
    block = ParsedBlock("abcdefghij", "code", start_line=7, end_line=7)
    chunks = DeterministicChunker(max_chars=5, overlap_chars=2).chunk([block])

    assert [chunk.content for chunk in chunks] == ["abcde", "defgh", "ghij", "j"]
    assert all(chunk.start_line == chunk.end_line == 7 for chunk in chunks)
    assert all(chunk.char_count <= 5 for chunk in chunks)


def test_chunker_never_merges_pdf_pages_or_markdown_sections() -> None:
    blocks = [
        ParsedBlock("page one", "page_text", page_number=1),
        ParsedBlock("page two", "page_text", page_number=2),
        ParsedBlock("section one", "paragraph", start_line=1, end_line=1, section_title="一"),
        ParsedBlock("section two", "paragraph", start_line=2, end_line=2, section_title="二"),
    ]
    chunks = DeterministicChunker(max_chars=20, overlap_chars=4).chunk(blocks)

    assert [chunk.page_number for chunk in chunks[:2]] == [1, 2]
    assert [chunk.section_title for chunk in chunks[2:]] == ["一", "二"]


def test_chunker_keeps_fenced_code_and_docx_table_whole_when_within_limit() -> None:
    blocks = [
        ParsedBlock("```py\nprint(1)\n```", "code", start_line=2, end_line=4),
        ParsedBlock("key\tvalue", "table", start_line=5, end_line=5),
    ]
    chunks = DeterministicChunker(max_chars=40, overlap_chars=5).chunk(blocks)
    assert [chunk.content for chunk in chunks] == ["```py\nprint(1)\n```", "key\tvalue"]
    assert [chunk.chunk_type for chunk in chunks] == ["code", "table"]
