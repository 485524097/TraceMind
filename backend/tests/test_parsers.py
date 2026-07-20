from pathlib import Path
from typing import cast

import pytest
from docx import Document
from pypdf import PdfReader as RealPdfReader

from app.parsing.base import ParseContext
from app.parsing.code import LANGUAGES, CodeParser
from app.parsing.docx import DocxParser
from app.parsing.exceptions import (
    DocumentEncodingError,
    DocumentParseError,
    NoExtractableTextError,
    ParseLimitExceededError,
    PdfEncryptedError,
)
from app.parsing.markdown import MarkdownParser
from app.parsing.pdf import PdfParser
from app.parsing.registry import ParserRegistry
from app.parsing.text import PlainTextParser

CONTEXT = ParseContext(max_extracted_chars=10_000, max_pdf_pages=10)


def write(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


def test_plain_text_utf8_sig_crlf_chinese_and_lines(tmp_path: Path) -> None:
    path = write(tmp_path / "sample.txt", "first\r\n\r\n中文".encode("utf-8-sig"))
    parsed = PlainTextParser().parse(path, CONTEXT)

    assert [block.text for block in parsed.blocks] == ["first", "中文"]
    assert [(block.start_line, block.end_line) for block in parsed.blocks] == [(1, 1), (3, 3)]


@pytest.mark.parametrize("content", [b"\xff", b"text\x00binary"])
def test_plain_text_rejects_invalid_utf8_and_nul(tmp_path: Path, content: bytes) -> None:
    with pytest.raises(DocumentEncodingError):
        PlainTextParser().parse(write(tmp_path / "sample.txt", content), CONTEXT)


def test_plain_text_rejects_empty_and_character_limit(tmp_path: Path) -> None:
    with pytest.raises(NoExtractableTextError):
        PlainTextParser().parse(write(tmp_path / "empty.txt", b" \n"), CONTEXT)
    with pytest.raises(ParseLimitExceededError):
        PlainTextParser().parse(
            write(tmp_path / "large.txt", b"12345"),
            ParseContext(max_extracted_chars=4, max_pdf_pages=10),
        )


def test_markdown_headings_sections_fences_and_lines(tmp_path: Path) -> None:
    content = "# 安装\n说明\n\n```python\nprint('ok')\n```\n## 配置\n值"
    parsed = MarkdownParser().parse(write(tmp_path / "sample.md", content.encode()), CONTEXT)

    assert [block.block_type for block in parsed.blocks] == [
        "heading",
        "paragraph",
        "code",
        "heading",
        "paragraph",
    ]
    assert parsed.blocks[1].section_title == "安装"
    assert parsed.blocks[2].language == "python"
    assert (parsed.blocks[2].start_line, parsed.blocks[2].end_line) == (4, 6)
    assert parsed.blocks[-1].section_title == "配置"


@pytest.mark.parametrize(("extension", "language"), sorted(LANGUAGES.items()))
def test_code_parser_language_indentation_and_lines(
    tmp_path: Path, extension: str, language: str
) -> None:
    path = write(tmp_path / f"sample{extension}", b"class A:\n    value = 1\n\nreturn value")
    parsed = CodeParser().parse(path, CONTEXT)

    assert parsed.blocks[0].text == "class A:\n    value = 1"
    assert parsed.blocks[0].language == language
    assert (parsed.blocks[0].start_line, parsed.blocks[0].end_line) == (1, 2)
    assert (parsed.blocks[1].start_line, parsed.blocks[1].end_line) == (4, 4)


class FakePage:
    def __init__(self, text: str | None) -> None:
        self.text = text

    def extract_text(self) -> str | None:
        return self.text


class FakeReader:
    def __init__(self, pages: list[FakePage], *, encrypted: bool = False) -> None:
        self.pages = pages
        self.is_encrypted = encrypted

    def decrypt(self, _password: str) -> int:
        return 0


def test_pdf_pages_are_one_based_and_blank_pages_are_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.parsing.pdf.PdfReader",
        lambda *_args, **_kwargs: FakeReader([FakePage("one"), FakePage(None), FakePage("三")]),
    )
    parsed = PdfParser().parse(tmp_path / "sample.pdf", CONTEXT)
    assert [(block.page_number, block.text) for block in parsed.blocks] == [(1, "one"), (3, "三")]


def test_pdf_encryption_no_text_page_and_character_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.parsing.pdf.PdfReader", lambda *_args, **_kwargs: FakeReader([], encrypted=True)
    )
    with pytest.raises(PdfEncryptedError):
        PdfParser().parse(tmp_path / "encrypted.pdf", CONTEXT)

    monkeypatch.setattr(
        "app.parsing.pdf.PdfReader", lambda *_args, **_kwargs: FakeReader([FakePage(None)])
    )
    with pytest.raises(NoExtractableTextError):
        PdfParser().parse(tmp_path / "blank.pdf", CONTEXT)

    monkeypatch.setattr(
        "app.parsing.pdf.PdfReader", lambda *_args, **_kwargs: FakeReader([FakePage("12345")])
    )
    with pytest.raises(ParseLimitExceededError):
        PdfParser().parse(
            tmp_path / "large.pdf", ParseContext(max_extracted_chars=4, max_pdf_pages=10)
        )


def test_pdf_page_limit_and_invalid_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.parsing.pdf.PdfReader",
        lambda *_args, **_kwargs: FakeReader([FakePage("one"), FakePage("two")]),
    )
    with pytest.raises(ParseLimitExceededError):
        PdfParser().parse(
            tmp_path / "pages.pdf", ParseContext(max_extracted_chars=100, max_pdf_pages=1)
        )
    monkeypatch.setattr("app.parsing.pdf.PdfReader", RealPdfReader)
    with pytest.raises(DocumentParseError):
        PdfParser().parse(write(tmp_path / "broken.pdf", b"not a pdf"), CONTEXT)


def test_docx_preserves_heading_paragraph_table_order_and_chinese(tmp_path: Path) -> None:
    path = tmp_path / "sample.docx"
    document = Document()
    document.add_heading("架构", level=1)
    document.add_paragraph("")
    document.add_paragraph("中文说明")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "键"
    table.cell(0, 1).text = "值"
    document.save(path)

    parsed = DocxParser().parse(path, CONTEXT)

    assert [block.block_type for block in parsed.blocks] == ["heading", "paragraph", "table"]
    assert [block.section_title for block in parsed.blocks] == ["架构", "架构", "架构"]
    assert parsed.blocks[-1].text == "键\t值"
    assert parsed.blocks[-1].page_number is None


def test_docx_rejects_broken_document(tmp_path: Path) -> None:
    with pytest.raises(DocumentParseError):
        DocxParser().parse(write(tmp_path / "broken.docx", b"not a zip"), CONTEXT)


def test_registry_maps_every_supported_extension() -> None:
    registry = ParserRegistry()
    expected = {
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".xml",
        ".properties",
        ".java",
        ".jsp",
        ".js",
        ".ts",
        ".vue",
        ".sql",
        ".py",
        ".pdf",
        ".docx",
    }
    assert registry.supported_extensions == expected
    assert cast(object, registry.get(".MD")).parser_name == "markdown"
