from pathlib import Path
from zipfile import BadZipFile

from docx import Document as OpenDocument
from docx.opc.exceptions import PackageNotFoundError
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.parsing.base import (
    BlockType,
    ParseContext,
    ParsedBlock,
    ParsedDocument,
    enforce_character_limit,
)
from app.parsing.exceptions import DocumentParseError, NoExtractableTextError


class DocxParser:
    parser_name = "docx"
    parser_version = "1"
    supported_extensions = frozenset({".docx"})

    def parse(self, path: Path, context: ParseContext) -> ParsedDocument:
        try:
            document = OpenDocument(str(path))
            blocks: list[ParsedBlock] = []
            section: str | None = None
            logical_line = 1
            total = 0
            for item in document.iter_inner_content():
                if isinstance(item, Paragraph):
                    text = item.text
                    current_line = logical_line
                    logical_line += 1
                    if not text.strip():
                        continue
                    total += len(text)
                    enforce_character_limit(total, context)
                    style = item.style.name if item.style is not None else ""
                    is_heading = style.startswith("Heading ") and style[8:].isdigit()
                    if is_heading and 1 <= int(style[8:]) <= 9:
                        section = text.strip()
                        block_type: BlockType = "heading"
                    else:
                        block_type = "paragraph"
                    blocks.append(
                        ParsedBlock(
                            text,
                            block_type,
                            start_line=current_line,
                            end_line=current_line,
                            section_title=section,
                        )
                    )
                elif isinstance(item, Table):
                    rows = ["\t".join(cell.text for cell in row.cells) for row in item.rows]
                    text = "\n".join(rows)
                    start_line = logical_line
                    logical_line += max(len(rows), 1)
                    if not text.strip():
                        continue
                    total += len(text)
                    enforce_character_limit(total, context)
                    blocks.append(
                        ParsedBlock(
                            text,
                            "table",
                            start_line=start_line,
                            end_line=start_line + len(rows) - 1,
                            section_title=section,
                        )
                    )
        except (PackageNotFoundError, BadZipFile, KeyError, ValueError, OSError) as exc:
            raise DocumentParseError() from exc
        if not blocks:
            raise NoExtractableTextError()
        return ParsedDocument(blocks, self.parser_name, self.parser_version)
