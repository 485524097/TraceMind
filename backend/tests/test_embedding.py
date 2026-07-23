import math

import pytest

from app.embedding import EmbeddingError, SentenceTransformerEmbeddingProvider
from app.embedding.sentence_transformer import _MODEL_CACHE, QUERY_INSTRUCTION


class FakeArray:
    def __init__(self, rows: list[list[float]]) -> None:
        self.rows = rows

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self.rows)


class FakeModel:
    def __init__(self, rows: list[list[float]]) -> None:
        self.rows = rows
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def encode(self, texts: list[str], **kwargs: object) -> FakeArray:
        self.calls.append((texts, kwargs))
        return FakeArray(self.rows[: len(texts)])


def provider_with(model: FakeModel, *, dimension: int = 3) -> SentenceTransformerEmbeddingProvider:
    provider = SentenceTransformerEmbeddingProvider("fake/model", dimension, 2, "cpu")
    _MODEL_CACHE[(provider.model_name, "cpu")] = model
    return provider


def test_document_embedding_uses_batching_normalization_and_no_prompt() -> None:
    model = FakeModel([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    provider = provider_with(model)

    assert provider.embed_documents(["one", "two"]) == model.rows
    _, kwargs = model.calls[0]
    assert kwargs["batch_size"] == 2
    assert kwargs["normalize_embeddings"] is True
    assert kwargs["prompt"] is None


def test_query_embedding_uses_general_knowledge_retrieval_instruction() -> None:
    model = FakeModel([[1.0, 0.0, 0.0]])
    provider = provider_with(model)

    query = "where is the service"
    assert provider.embed_query(query) == model.rows[0]
    texts, kwargs = model.calls[0]
    prompt = str(kwargs["prompt"])
    assert prompt.startswith("Instruct:")
    assert "personal profiles" in prompt
    assert "software projects" in prompt
    assert "code" in prompt
    assert "technical documents" in prompt
    assert "Query:" in prompt
    assert (prompt + "".join(texts)).count(query) == 1
    assert QUERY_INSTRUCTION in prompt


@pytest.mark.parametrize(
    "rows",
    [
        [[1.0, 2.0]],
        [[1.0, math.nan, 3.0]],
        [[1.0, math.inf, 3.0]],
    ],
)
def test_embedding_rejects_invalid_dimension_and_non_finite_values(
    rows: list[list[float]],
) -> None:
    with pytest.raises(EmbeddingError):
        provider_with(FakeModel(rows)).embed_documents(["text"])
