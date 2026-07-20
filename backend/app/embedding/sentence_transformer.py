import threading
from typing import Any

from app.embedding.base import EmbeddingError, validate_embeddings

QUERY_INSTRUCTION = (
    "Given a developer knowledge-base query, retrieve relevant code and documentation chunks."
)
_MODEL_CACHE: dict[tuple[str, str], Any] = {}
_MODEL_LOCK = threading.Lock()


class SentenceTransformerEmbeddingProvider:
    def __init__(
        self,
        model_name: str,
        dimension: int,
        batch_size: int,
        device: str,
    ) -> None:
        self._model_name = model_name
        self._dimension = dimension
        self.batch_size = batch_size
        self.device = device

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def _model(self) -> Any:
        key = (self.model_name, self.device)
        with _MODEL_LOCK:
            model = _MODEL_CACHE.get(key)
            if model is None:
                try:
                    from sentence_transformers import SentenceTransformer

                    kwargs = {} if self.device == "auto" else {"device": self.device}
                    model = SentenceTransformer(self.model_name, **kwargs)
                except Exception as exc:
                    raise EmbeddingError("Embedding model could not be loaded") from exc
                _MODEL_CACHE[key] = model
        return model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts)

    def embed_query(self, text: str) -> list[float]:
        vectors = self._encode([text], prompt=f"{QUERY_INSTRUCTION}\nQuery: ")
        return vectors[0]

    def _encode(self, texts: list[str], *, prompt: str | None = None) -> list[list[float]]:
        if not texts:
            return []
        try:
            encoded = self._model().encode(
                texts,
                prompt=prompt,
                batch_size=self.batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
                truncate_dim=self.dimension,
            )
            vectors = [[float(value) for value in row] for row in encoded]
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError("Text embedding could not be generated") from exc
        validate_embeddings(vectors, dimension=self.dimension)
        return vectors
