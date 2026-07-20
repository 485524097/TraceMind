class DocumentParseError(Exception):
    code = "invalid_document"
    safe_message = "Document could not be parsed"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.safe_message)


class DocumentEncodingError(DocumentParseError):
    code = "invalid_encoding"
    safe_message = "Document must use UTF-8 encoding"


class PdfEncryptedError(DocumentParseError):
    code = "encrypted_pdf"
    safe_message = "Encrypted PDF documents are not supported"


class NoExtractableTextError(DocumentParseError):
    code = "no_extractable_text"
    safe_message = "Document contains no extractable text"


class ParseLimitExceededError(DocumentParseError):
    code = "parse_limit_exceeded"
    safe_message = "Document exceeds the configured parsing limit"


class UnsupportedParserError(DocumentParseError):
    code = "unsupported_parser"
    safe_message = "Document type is not supported for parsing"
