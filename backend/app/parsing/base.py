from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from app.parsing.exceptions import (
    DocumentEncodingError,
    NoExtractableTextError,
    ParseLimitExceededError,
)

BlockType = Literal["paragraph", "heading", "code", "table", "page_text"]


@dataclass(frozen=True)
class ParseContext:
    max_extracted_chars: int
    max_pdf_pages: int


@dataclass(frozen=True)
class ParsedBlock:
    text: str
    block_type: BlockType
    page_number: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    section_title: str | None = None
    language: str | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("Parsed block text must not be blank")
        if self.page_number is not None and self.page_number < 1:
            raise ValueError("Page numbers are 1-based")
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("Line boundaries must be provided together")
        if self.start_line is not None and self.start_line < 1:
            raise ValueError("Line numbers are 1-based")
        if (
            self.start_line is not None
            and self.end_line is not None
            and self.end_line < self.start_line
        ):
            raise ValueError("End line must not precede start line")


@dataclass(frozen=True)
class ParsedDocument:
    blocks: list[ParsedBlock]
    parser_name: str
    parser_version: str
    warnings: list[str] = field(default_factory=list)


class DocumentParser(Protocol):
    parser_name: str
    parser_version: str
    supported_extensions: frozenset[str]

    def parse(self, path: Path, context: ParseContext) -> ParsedDocument: ...


def read_utf8_text(path: Path, context: ParseContext) -> str:
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentEncodingError() from exc
    except OSError:
        raise
    if "\x00" in text:
        raise DocumentEncodingError("Document appears to contain binary data")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    enforce_character_limit(len(normalized), context)
    if not normalized.strip():
        raise NoExtractableTextError()
    return normalized


def enforce_character_limit(total: int, context: ParseContext) -> None:
    if total > context.max_extracted_chars:
        raise ParseLimitExceededError()
