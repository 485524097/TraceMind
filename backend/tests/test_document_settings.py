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
