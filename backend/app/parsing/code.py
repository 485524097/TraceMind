from pathlib import Path

from app.parsing.base import ParseContext, ParsedBlock, ParsedDocument, read_utf8_text
from app.parsing.exceptions import UnsupportedParserError

LANGUAGES = {
    ".java": "java",
    ".jsp": "jsp",
    ".js": "javascript",
    ".ts": "typescript",
    ".vue": "vue",
    ".sql": "sql",
    ".py": "python",
}


class CodeParser:
    parser_name = "code"
    parser_version = "1"
    supported_extensions = frozenset(LANGUAGES)

    def parse(self, path: Path, context: ParseContext) -> ParsedDocument:
        language = LANGUAGES.get(path.suffix.lower())
        if language is None:
            raise UnsupportedParserError()
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
                        "\n".join(lines),
                        "code",
                        start_line=start,
                        end_line=number - 1,
                        language=language,
                    )
                )
                start = None
                lines = []
        if lines and start is not None:
            blocks.append(
                ParsedBlock(
                    "\n".join(lines),
                    "code",
                    start_line=start,
                    end_line=start + len(lines) - 1,
                    language=language,
                )
            )
        return ParsedDocument(blocks, self.parser_name, self.parser_version)
