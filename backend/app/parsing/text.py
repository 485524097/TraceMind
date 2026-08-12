from pathlib import Path

from app.parsing.base import ParseContext, ParsedBlock, ParsedDocument, read_utf8_text


class PlainTextParser:
    parser_name = "plain_text"
    parser_version = "1"
    supported_extensions = frozenset({".txt", ".json", ".yaml", ".yml", ".xml", ".properties"})

    def parse(self, path: Path, context: ParseContext) -> ParsedDocument:
        text = read_utf8_text(path, context)
        blocks: list[ParsedBlock] = []
        start: int | None = None
        lines: list[str] = []
        for number, line in enumerate(text.split("\n"), start=1):
            if line.strip():
                if start is None:
                    start = number
                lines.append(line)
            elif lines and start is not None:
                blocks.append(
                    ParsedBlock(
                        "\n".join(lines), "paragraph", start_line=start, end_line=number - 1
                    )
                )
                start = None
                lines = []
        if lines and start is not None:
            blocks.append(
                ParsedBlock(
                    "\n".join(lines), "paragraph", start_line=start, end_line=start + len(lines) - 1
                )
            )
        return ParsedDocument(blocks, self.parser_name, self.parser_version)
