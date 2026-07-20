from app.parsing.base import DocumentParser
from app.parsing.code import CodeParser
from app.parsing.docx import DocxParser
from app.parsing.exceptions import UnsupportedParserError
from app.parsing.markdown import MarkdownParser
from app.parsing.pdf import PdfParser
from app.parsing.text import PlainTextParser


class ParserRegistry:
    def __init__(self, parsers: tuple[DocumentParser, ...] | None = None) -> None:
        configured = parsers or (
            MarkdownParser(),
            PlainTextParser(),
            CodeParser(),
            PdfParser(),
            DocxParser(),
        )
        self._parsers = {
            extension: parser for parser in configured for extension in parser.supported_extensions
        }

    def get(self, extension: str) -> DocumentParser:
        parser = self._parsers.get(extension.lower())
        if parser is None:
            raise UnsupportedParserError()
        return parser

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset(self._parsers)
