from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.parsing.base import ParseContext, ParsedBlock, ParsedDocument, enforce_character_limit
from app.parsing.exceptions import (
    DocumentParseError,
    NoExtractableTextError,
    ParseLimitExceededError,
    PdfEncryptedError,
)


class PdfParser:
    parser_name = "pdf"
    parser_version = "1"
    supported_extensions = frozenset({".pdf"})

    def parse(self, path: Path, context: ParseContext) -> ParsedDocument:
        try:
            reader = PdfReader(path, strict=False)
            if reader.is_encrypted and reader.decrypt("") == 0:
                raise PdfEncryptedError()
            if len(reader.pages) > context.max_pdf_pages:
                raise ParseLimitExceededError()
            blocks: list[ParsedBlock] = []
            total = 0
            for page_number, page in enumerate(reader.pages, start=1):
                extracted = (page.extract_text() or "").replace("\r\n", "\n").replace("\r", "\n")
                if not extracted.strip():
                    continue
                total += len(extracted)
                enforce_character_limit(total, context)
                blocks.append(ParsedBlock(extracted.strip(), "page_text", page_number=page_number))
        except (PdfEncryptedError, DocumentParseError):
            raise
        except (PdfReadError, OSError, ValueError) as exc:
            raise DocumentParseError() from exc
        if not blocks:
            raise NoExtractableTextError()
        return ParsedDocument(blocks, self.parser_name, self.parser_version)
