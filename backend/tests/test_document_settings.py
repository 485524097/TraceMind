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


def test_qdrant_operation_defaults_and_environment_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = Settings(_env_file=None)
    assert defaults.qdrant_operation_timeout_seconds == 60
    assert defaults.qdrant_upsert_batch_size == 64

    monkeypatch.setenv("QDRANT_OPERATION_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("QDRANT_UPSERT_BATCH_SIZE", "32")
    overridden = Settings(_env_file=None)
    assert overridden.qdrant_operation_timeout_seconds == 90
    assert overridden.qdrant_upsert_batch_size == 32


def test_semantic_search_threshold_defaults_and_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings(_env_file=None).semantic_search_score_threshold == 0.50
    monkeypatch.setenv("SEMANTIC_SEARCH_SCORE_THRESHOLD", "0.72")
    assert Settings(_env_file=None).semantic_search_score_threshold == 0.72


@pytest.mark.parametrize("value", [0, -0.1, 1.01])
def test_semantic_search_threshold_rejects_invalid_values(value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(semantic_search_score_threshold=value)


@pytest.mark.parametrize("value", [0.01, 0.5, 1.0])
def test_semantic_search_threshold_accepts_valid_values(value: float) -> None:
    assert Settings(semantic_search_score_threshold=value).semantic_search_score_threshold == value


@pytest.mark.parametrize(
    "field",
    ["qdrant_operation_timeout_seconds", "qdrant_upsert_batch_size"],
)
@pytest.mark.parametrize("value", [0, -1])
def test_qdrant_operation_settings_must_be_positive(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: value})


@pytest.mark.parametrize(
    "field",
    ["qdrant_collection_name", "qdrant_dense_vector_name", "embedding_model_name"],
)
def test_index_names_must_not_be_empty(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: " "})


def test_rag_settings_default_to_disabled_and_normalize_empty_values() -> None:
    settings = Settings(_env_file=None, llm_base_url=" ", llm_model="", llm_api_key=" ")
    assert settings.rag_llm_enabled is False
    assert settings.llm_base_url is None
    assert settings.llm_model is None
    assert settings.llm_api_key is None


def test_rag_settings_allow_empty_key_and_require_url_model_pair() -> None:
    settings = Settings(
        _env_file=None,
        llm_base_url="http://localhost:11434/v1",
        llm_model="local",
        llm_api_key=None,
    )
    assert settings.rag_llm_enabled is True
    assert settings.llm_api_key is None
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_base_url="http://localhost:11434/v1")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_model="local")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("llm_timeout_seconds", 0),
        ("llm_temperature", -0.1),
        ("llm_temperature", 2.1),
        ("llm_max_tokens", 0),
        ("rag_retrieval_limit", 0),
        ("rag_retrieval_limit", 11),
        ("rag_max_context_chars", 999),
    ],
)
def test_rag_settings_reject_invalid_limits(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_llm_secret_is_redacted() -> None:
    settings = Settings(llm_api_key="private-test-key")
    assert "private-test-key" not in repr(settings)


def test_hybrid_settings_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.qdrant_sparse_vector_name == "bm25_v1"
    assert settings.qdrant_bm25_model == "qdrant/bm25"
    assert settings.qdrant_bm25_tokenizer == "multilingual"
    assert settings.qdrant_bm25_language == "none"
    assert settings.hybrid_dense_prefetch_limit == 20
    assert settings.hybrid_sparse_prefetch_limit == 20


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("qdrant_sparse_vector_name", ""),
        ("qdrant_bm25_model", ""),
        ("qdrant_bm25_tokenizer", ""),
        ("qdrant_bm25_language", ""),
        ("hybrid_dense_prefetch_limit", 0),
        ("hybrid_dense_prefetch_limit", 101),
        ("hybrid_sparse_prefetch_limit", 0),
        ("hybrid_sparse_prefetch_limit", 101),
    ],
)
def test_hybrid_settings_reject_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_dense_and_sparse_names_must_differ() -> None:
    with pytest.raises(ValidationError):
        Settings(qdrant_dense_vector_name="same", qdrant_sparse_vector_name="same")
