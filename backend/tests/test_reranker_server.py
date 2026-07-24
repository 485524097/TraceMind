from fastapi.testclient import TestClient

from app.core.config import Settings
from app.reranker import RerankerResult, RerankerUnavailableError
from app.reranker_server import create_reranker_app


class FakeServerProvider:
    def __init__(self, *, ready: bool = True, fail: bool = False) -> None:
        self._ready = ready
        self.fail = fail
        self.closed = False
        self.calls = 0

    @property
    def ready(self) -> bool:
        return self._ready

    async def rerank(self, query, candidates, *, limit):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.fail:
            raise RerankerUnavailableError(reason="cuda_oom")
        return [
            RerankerResult(candidate.candidate_id, 3.5 - index, index + 1)
            for index, candidate in enumerate(candidates[:limit])
        ]

    async def close(self) -> None:
        self.closed = True


def test_reranker_server_live_ready_rerank_and_close() -> None:
    provider = FakeServerProvider()
    app = create_reranker_app(
        Settings(_env_file=None, app_env="test"),
        provider_factory=lambda settings: provider,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/health/live").json() == {"live": True}
        assert client.get("/health/ready").json() == {"ready": True}
        response = client.post(
            "/rerank",
            json={
                "query": "DiscoveryClient",
                "limit": 1,
                "candidates": [
                    {"candidate_id": "c1", "text": "first"},
                    {"candidate_id": "c2", "text": "second"},
                ],
            },
        )
        assert response.status_code == 200
        assert response.json()["items"] == [{"candidate_id": "c1", "rank": 1, "score": 3.5}]
        assert response.json()["model"] == "Qwen/Qwen3-Reranker-0.6B"
    assert provider.closed


def test_reranker_server_not_ready_when_model_load_fails() -> None:
    def fail(settings: Settings) -> FakeServerProvider:
        raise RuntimeError("private cache path")

    app = create_reranker_app(
        Settings(_env_file=None, app_env="test"),
        provider_factory=fail,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/health/live").status_code == 200
        ready = client.get("/health/ready")
        assert ready.status_code == 503
        assert ready.json() == {"ready": False}
        response = client.post(
            "/rerank",
            json={
                "query": "query",
                "limit": 1,
                "candidates": [{"candidate_id": "c1", "text": "text"}],
            },
        )
        assert response.status_code == 503
        assert "private" not in response.text


def test_reranker_server_validates_candidate_limits_and_duplicates() -> None:
    app = create_reranker_app(
        Settings(_env_file=None, app_env="test"),
        provider_factory=lambda settings: FakeServerProvider(),
    )
    too_many = [{"candidate_id": f"c{index}", "text": "text"} for index in range(21)]
    with TestClient(app, raise_server_exceptions=False) as client:
        overflow = client.post(
            "/rerank",
            json={"query": "query", "limit": 1, "candidates": too_many},
        )
        duplicate = client.post(
            "/rerank",
            json={
                "query": "query",
                "limit": 1,
                "candidates": [
                    {"candidate_id": "same", "text": "one"},
                    {"candidate_id": "same", "text": "two"},
                ],
            },
        )
    assert overflow.status_code == duplicate.status_code == 422


def test_reranker_server_returns_safe_503_without_traceback() -> None:
    provider = FakeServerProvider(fail=True)
    app = create_reranker_app(
        Settings(_env_file=None, app_env="test"),
        provider_factory=lambda settings: provider,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/rerank",
            json={
                "query": "private query",
                "limit": 1,
                "candidates": [{"candidate_id": "c1", "text": "private candidate"}],
            },
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "Reranker is unavailable"}
    assert "traceback" not in response.text.lower()
    assert "private" not in response.text
