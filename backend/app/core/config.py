from functools import lru_cache
from pathlib import Path
from typing import Annotated, Self
from urllib.parse import quote_plus

from pydantic import field_validator, model_validator
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
    celery_broker_url: str = "redis://127.0.0.1:6379/1"
    celery_result_backend: str = "redis://127.0.0.1:6379/2"
    healthcheck_timeout_seconds: int = 2
    document_storage_root: Path = Path("../data/uploads")
    document_max_file_size_bytes: int = 52_428_800
    document_upload_chunk_size_bytes: int = 1_048_576
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
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if missing:
            raise ValueError(f"Required settings are empty: {', '.join(missing)}")
        if self.document_max_file_size_bytes <= 0:
            raise ValueError("DOCUMENT_MAX_FILE_SIZE_BYTES must be greater than zero")
        if self.document_upload_chunk_size_bytes <= 0:
            raise ValueError("DOCUMENT_UPLOAD_CHUNK_SIZE_BYTES must be greater than zero")
        return self

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
