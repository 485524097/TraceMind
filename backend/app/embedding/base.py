import math
from typing import Protocol


class EmbeddingError(Exception):
    """Safe embedding provider failure."""


class EmbeddingProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def validate_embeddings(vectors: list[list[float]], *, dimension: int) -> None:
    for vector in vectors:
        if len(vector) != dimension:
            raise EmbeddingError("Embedding provider returned an invalid vector dimension")
        if not all(math.isfinite(value) for value in vector):
            raise EmbeddingError("Embedding provider returned a non-finite vector")
