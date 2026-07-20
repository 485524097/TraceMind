from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_document_settings_normalize_root_and_extensions(tmp_path: Path) -> None:
    settings = Settings(
        document_storage_root=tmp_path / "missing" / "uploads",
        document_allowed_extensions="MD, .Pdf,.md",
    )

    assert settings.document_storage_root.is_absolute()
    assert settings.document_allowed_extensions == [".md", ".pdf"]


@pytest.mark.parametrize(
    "field",
    ["document_max_file_size_bytes", "document_upload_chunk_size_bytes"],
)
def test_document_size_settings_must_be_positive(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: 0})


@pytest.mark.parametrize(
    "field",
    [
        "document_parse_max_extracted_chars",
        "document_parse_max_pdf_pages",
        "document_parse_stale_after_seconds",
        "document_chunk_max_chars",
        "document_chunk_overlap_chars",
    ],
)
def test_parse_settings_must_be_positive(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: 0})


def test_chunk_overlap_and_extraction_limits_are_consistent() -> None:
    with pytest.raises(ValidationError):
        Settings(document_chunk_max_chars=100, document_chunk_overlap_chars=100)
    with pytest.raises(ValidationError):
        Settings(document_parse_max_extracted_chars=99, document_chunk_max_chars=100)


@pytest.mark.parametrize(
    "field",
    ["embedding_dimension", "embedding_batch_size", "document_index_stale_after_seconds"],
)
def test_index_settings_must_be_positive(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: 0})


@pytest.mark.parametrize(
    "field",
    ["qdrant_collection_name", "qdrant_dense_vector_name", "embedding_model_name"],
)
def test_index_names_must_not_be_empty(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: " "})
