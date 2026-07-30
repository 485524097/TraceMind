import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.services.exceptions import InvalidDocumentNameError, UnsupportedDocumentTypeError


@dataclass(frozen=True)
class SafeDocumentName:
    display_name: str
    normalized_name: str
    extension: str


@dataclass(frozen=True)
class SafeDocumentPath:
    display_name: str
    normalized_name: str
    relative_path: str
    normalized_path: str
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


def normalize_document_path(
    relative_path: str | None, allowed_extensions: set[str]
) -> SafeDocumentPath:
    if relative_path is None or "\x00" in relative_path:
        raise InvalidDocumentNameError("Invalid document path")
    raw = unicodedata.normalize("NFC", relative_path).strip().replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise InvalidDocumentNameError("Invalid document path")
    if raw.endswith("/"):
        raise InvalidDocumentNameError("Invalid document path")
    final_part = unicodedata.normalize("NFC", raw.split("/")[-1]).strip()
    if final_part in {"", ".", ".."}:
        raise InvalidDocumentNameError("Invalid document path")
    parts: list[str] = []
    for raw_part in raw.split("/"):
        part = unicodedata.normalize("NFC", raw_part).strip()
        if not part or part == ".":
            continue
        if part == "..":
            raise InvalidDocumentNameError("Invalid document path")
        if not parts and re.match(r"^[A-Za-z]:", part):
            raise InvalidDocumentNameError("Invalid document path")
        parts.append(part)
    if not parts:
        raise InvalidDocumentNameError("Invalid document path")
    safe_name = normalize_document_name(parts[-1], allowed_extensions)
    parts[-1] = safe_name.display_name
    display_path = "/".join(parts)
    if len(display_path) > 1024:
        raise InvalidDocumentNameError("Invalid document path")
    return SafeDocumentPath(
        display_name=safe_name.display_name,
        normalized_name=safe_name.normalized_name,
        relative_path=display_path,
        normalized_path=unicodedata.normalize("NFC", display_path).casefold(),
        extension=safe_name.extension,
    )
