from functools import lru_cache
from pathlib import Path
from typing import Annotated, Self
from urllib.parse import quote_plus

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "TraceMind API"
    app_env: str = "development"
    app_version: str = "0.1.0"
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_user: str = "tracemind"
    postgres_password: str = "tracemind-local-only"
    postgres_db: str = "tracemind"
    database_url: str | None = None
    redis_url: str = "redis://127.0.0.1:6379/0"
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection_name: str = "tracemind_chunks"
    qdrant_dense_vector_name: str = "dense_v1"
    qdrant_sparse_vector_name: str = "bm25_v1"
    qdrant_bm25_model: str = "qdrant/bm25"
    qdrant_bm25_tokenizer: str = "multilingual"
    qdrant_bm25_language: str = "none"
    qdrant_operation_timeout_seconds: int = 60
    qdrant_upsert_batch_size: int = 64
    hybrid_dense_prefetch_limit: int = 20
    hybrid_sparse_prefetch_limit: int = 20
    semantic_search_score_threshold: float = 0.50
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_timeout_seconds: float = 120
    llm_temperature: float = 0.1
    llm_max_tokens: int = 1_200
    rag_retrieval_limit: int = 5
    rag_max_context_chars: int = 12_000
    embedding_model_name: str = "Qwen/Qwen3-Embedding-0.6B"
    embedding_dimension: int = 1_024
    embedding_batch_size: int = 16
    embedding_device: str = "auto"
    document_index_stale_after_seconds: int = 1_800
    celery_broker_url: str = "redis://127.0.0.1:6379/1"
    celery_result_backend: str = "redis://127.0.0.1:6379/2"
    healthcheck_timeout_seconds: int = 2
    document_storage_root: Path = Path("../data/uploads")
    document_max_file_size_bytes: int = 52_428_800
    document_upload_chunk_size_bytes: int = 1_048_576
    document_parse_max_extracted_chars: int = 5_000_000
    document_parse_max_pdf_pages: int = 1_000
    document_parse_stale_after_seconds: int = 1_800
    document_chunk_max_chars: int = 1_800
    document_chunk_overlap_chars: int = 200
    document_allowed_extensions: Annotated[list[str], NoDecode] = [
        ".md",
        ".txt",
        ".pdf",
        ".docx",
        ".java",
        ".jsp",
        ".js",
        ".ts",
        ".vue",
        ".sql",
        ".xml",
        ".json",
        ".yaml",
        ".yml",
        ".properties",
        ".py",
    ]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("document_allowed_extensions", mode="before")
    @classmethod
    def parse_document_extensions(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("llm_base_url", "llm_model", mode="before")
    @classmethod
    def normalize_optional_string(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("llm_api_key", mode="before")
    @classmethod
    def normalize_optional_secret(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("document_allowed_extensions")
    @classmethod
    def normalize_document_extensions(cls, value: list[str]) -> list[str]:
        normalized = sorted(
            {item.lower() if item.startswith(".") else f".{item.lower()}" for item in value}
        )
        if not normalized:
            raise ValueError("DOCUMENT_ALLOWED_EXTENSIONS must not be empty")
        return normalized

    @field_validator("document_storage_root")
    @classmethod
    def resolve_document_storage_root(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("api_v1_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("API_V1_PREFIX must start with '/'")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_required_values(self) -> Self:
        required = {
            "POSTGRES_HOST": self.postgres_host,
            "POSTGRES_USER": self.postgres_user,
            "POSTGRES_PASSWORD": self.postgres_password,
            "POSTGRES_DB": self.postgres_db,
            "REDIS_URL": self.redis_url,
            "QDRANT_URL": self.qdrant_url,
            "QDRANT_COLLECTION_NAME": self.qdrant_collection_name,
            "QDRANT_DENSE_VECTOR_NAME": self.qdrant_dense_vector_name,
            "QDRANT_SPARSE_VECTOR_NAME": self.qdrant_sparse_vector_name,
            "QDRANT_BM25_MODEL": self.qdrant_bm25_model,
            "QDRANT_BM25_TOKENIZER": self.qdrant_bm25_tokenizer,
            "QDRANT_BM25_LANGUAGE": self.qdrant_bm25_language,
            "EMBEDDING_MODEL_NAME": self.embedding_model_name,
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if missing:
            raise ValueError(f"Required settings are empty: {', '.join(missing)}")
        if self.qdrant_sparse_vector_name == self.qdrant_dense_vector_name:
            raise ValueError("Dense and sparse vector names must be different")
        for name, value in {
            "HYBRID_DENSE_PREFETCH_LIMIT": self.hybrid_dense_prefetch_limit,
            "HYBRID_SPARSE_PREFETCH_LIMIT": self.hybrid_sparse_prefetch_limit,
        }.items():
            if not 1 <= value <= 100:
                raise ValueError(f"{name} must be between 1 and 100")
        index_values = {
            "EMBEDDING_DIMENSION": self.embedding_dimension,
            "EMBEDDING_BATCH_SIZE": self.embedding_batch_size,
            "DOCUMENT_INDEX_STALE_AFTER_SECONDS": self.document_index_stale_after_seconds,
            "QDRANT_OPERATION_TIMEOUT_SECONDS": self.qdrant_operation_timeout_seconds,
            "QDRANT_UPSERT_BATCH_SIZE": self.qdrant_upsert_batch_size,
        }
        invalid_index = [name for name, value in index_values.items() if value <= 0]
        if invalid_index:
            raise ValueError(
                f"Indexing settings must be greater than zero: {', '.join(invalid_index)}"
            )
        if not 0.0 < self.semantic_search_score_threshold <= 1.0:
            raise ValueError("SEMANTIC_SEARCH_SCORE_THRESHOLD must be greater than 0 and at most 1")
        if (self.llm_base_url is None) != (self.llm_model is None):
            raise ValueError("LLM_BASE_URL and LLM_MODEL must be configured together")
        if self.llm_timeout_seconds <= 0:
            raise ValueError("LLM_TIMEOUT_SECONDS must be greater than zero")
        if not 0 <= self.llm_temperature <= 2:
            raise ValueError("LLM_TEMPERATURE must be between 0 and 2")
        if self.llm_max_tokens <= 0:
            raise ValueError("LLM_MAX_TOKENS must be greater than zero")
        if not 1 <= self.rag_retrieval_limit <= 10:
            raise ValueError("RAG_RETRIEVAL_LIMIT must be between 1 and 10")
        if self.rag_max_context_chars < 1_000:
            raise ValueError("RAG_MAX_CONTEXT_CHARS must be at least 1000")
        if self.document_max_file_size_bytes <= 0:
            raise ValueError("DOCUMENT_MAX_FILE_SIZE_BYTES must be greater than zero")
        if self.document_upload_chunk_size_bytes <= 0:
            raise ValueError("DOCUMENT_UPLOAD_CHUNK_SIZE_BYTES must be greater than zero")
        parse_values = {
            "DOCUMENT_PARSE_MAX_EXTRACTED_CHARS": self.document_parse_max_extracted_chars,
            "DOCUMENT_PARSE_MAX_PDF_PAGES": self.document_parse_max_pdf_pages,
            "DOCUMENT_PARSE_STALE_AFTER_SECONDS": self.document_parse_stale_after_seconds,
            "DOCUMENT_CHUNK_MAX_CHARS": self.document_chunk_max_chars,
            "DOCUMENT_CHUNK_OVERLAP_CHARS": self.document_chunk_overlap_chars,
        }
        invalid = [name for name, value in parse_values.items() if value <= 0]
        if invalid:
            raise ValueError(f"Parsing settings must be greater than zero: {', '.join(invalid)}")
        if self.document_chunk_overlap_chars >= self.document_chunk_max_chars:
            raise ValueError("DOCUMENT_CHUNK_OVERLAP_CHARS must be smaller than max chars")
        if self.document_parse_max_extracted_chars < self.document_chunk_max_chars:
            raise ValueError("Parse character limit must not be smaller than chunk max chars")
        return self

    @property
    def rag_llm_enabled(self) -> bool:
        return self.llm_base_url is not None and self.llm_model is not None

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        user = quote_plus(self.postgres_user)
        password = quote_plus(self.postgres_password)
        return (
            f"postgresql+asyncpg://{user}:{password}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
