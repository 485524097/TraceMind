import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.services.exceptions import InvalidDocumentNameError, UnsupportedDocumentTypeError


@dataclass(frozen=True)
class SafeDocumentName:
    display_name: str
    normalized_name: str
    extension: str


def normalize_document_name(filename: str | None, allowed_extensions: set[str]) -> SafeDocumentName:
    if filename is None or "\x00" in filename:
        raise InvalidDocumentNameError("Invalid document filename")
    basename = PurePosixPath(filename.replace("\\", "/")).name
    display_name = unicodedata.normalize("NFC", basename).strip()
    if not display_name or display_name in {".", ".."} or len(display_name) > 255:
        raise InvalidDocumentNameError("Invalid document filename")
    extension = PurePosixPath(display_name).suffix.lower()
    if extension not in allowed_extensions:
        raise UnsupportedDocumentTypeError("Unsupported document extension")
    return SafeDocumentName(
        display_name=display_name,
        normalized_name=unicodedata.normalize("NFC", display_name).casefold(),
        extension=extension,
    )
