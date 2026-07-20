import re
from pathlib import Path

from app.parsing.base import ParseContext, ParsedBlock, ParsedDocument, read_utf8_text

HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)\s*$")
FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")


class MarkdownParser:
    parser_name = "markdown"
    parser_version = "1"
    supported_extensions = frozenset({".md"})

    def parse(self, path: Path, context: ParseContext) -> ParsedDocument:
        lines = read_utf8_text(path, context).split("\n")
        blocks: list[ParsedBlock] = []
        section: str | None = None
        paragraph: list[str] = []
        paragraph_start = 0

        def flush(end_line: int) -> None:
            nonlocal paragraph, paragraph_start
            if paragraph:
                blocks.append(
                    ParsedBlock(
                        "\n".join(paragraph),
                        "paragraph",
                        start_line=paragraph_start,
                        end_line=end_line,
                        section_title=section,
                    )
                )
                paragraph = []
                paragraph_start = 0

        index = 0
        while index < len(lines):
            line = lines[index]
            line_number = index + 1
            heading = HEADING.match(line)
            fence = FENCE.match(line)
            if heading:
                flush(line_number - 1)
                section = heading.group(2).strip()
                blocks.append(
                    ParsedBlock(
                        line,
                        "heading",
                        start_line=line_number,
                        end_line=line_number,
                        section_title=section,
                    )
                )
                index += 1
                continue
            if fence:
                flush(line_number - 1)
                marker = fence.group(1)
                code_lines = [line]
                end = index
                for candidate in range(index + 1, len(lines)):
                    code_lines.append(lines[candidate])
                    end = candidate
                    if re.match(
                        rf"^[ \t]{{0,3}}{re.escape(marker[0])}{{{len(marker)},}}[ \t]*$",
                        lines[candidate],
                    ):
                        break
                language = fence.group(2).strip().split(maxsplit=1)[0] or None
                blocks.append(
                    ParsedBlock(
                        "\n".join(code_lines),
                        "code",
                        start_line=line_number,
                        end_line=end + 1,
                        section_title=section,
                        language=language,
                    )
                )
                index = end + 1
                continue
            if line.strip():
                if not paragraph:
                    paragraph_start = line_number
                paragraph.append(line)
            else:
                flush(line_number - 1)
            index += 1
        flush(len(lines))
        return ParsedDocument(blocks, self.parser_name, self.parser_version)
