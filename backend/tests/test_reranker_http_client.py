from collections.abc import Callable

import httpx
import pytest

from app.reranker import (
    HttpRerankerProvider,
    RerankerCandidate,
    RerankerUnavailableError,
)


def candidates() -> list[RerankerCandidate]:
    return [
        RerankerCandidate("c1", "first"),
        RerankerCandidate("c2", "second"),
    ]


def provider(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[HttpRerankerProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:8011",
        transport=httpx.MockTransport(handler),
    )
    return (
        HttpRerankerProvider(
            "http://127.0.0.1:8011",
            read_timeout_seconds=12,
            max_candidates=20,
            client=client,
        ),
        client,
    )


async def test_http_provider_posts_and_validates_normal_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rerank"
        assert request.method == "POST"
        return httpx.Response(
            200,
            json={
                "model": "test",
                "items": [
                    {"candidate_id": "c2", "rank": 1, "score": 4.2},
                    {"candidate_id": "c1", "rank": 2, "score": -1.0},
                ],
                "latency_ms": 12,
            },
        )

    service, client = provider(handler)
    results = await service.rerank("query", candidates(), limit=2)
    assert [(item.candidate_id, item.score) for item in results] == [
        ("c2", 4.2),
        ("c1", -1.0),
    ]
    await service.close()
    assert client.is_closed


@pytest.mark.parametrize(
    ("exception", "reason"),
    [
        (httpx.ConnectError("private endpoint"), "unavailable"),
        (httpx.ReadTimeout("private query"), "timeout"),
    ],
)
async def test_http_provider_converts_transport_errors(
    exception: httpx.HTTPError, reason: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exception

    service, _ = provider(handler)
    with pytest.raises(RerankerUnavailableError) as caught:
        await service.rerank("private query", candidates(), limit=1)
    assert str(caught.value) == "Reranker is unavailable"
    assert caught.value.reason == reason
    assert "private" not in str(caught.value)
    await service.close()


async def test_http_provider_hides_non_success_response_body() -> None:
    service, _ = provider(
        lambda request: httpx.Response(503, text="private traceback and candidate")
    )
    with pytest.raises(RerankerUnavailableError) as caught:
        await service.rerank("query", candidates(), limit=1)
    assert "private" not in str(caught.value)
    await service.close()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json={"model": "test", "items": [], "latency_ms": 1}),
        httpx.Response(
            200,
            json={
                "model": "test",
                "items": [{"candidate_id": "unknown", "rank": 1, "score": 1}],
                "latency_ms": 1,
            },
        ),
        httpx.Response(
            200,
            json={
                "model": "test",
                "items": [
                    {"candidate_id": "c1", "rank": 1, "score": 2},
                    {"candidate_id": "c1", "rank": 2, "score": 1},
                ],
                "latency_ms": 1,
            },
        ),
        httpx.Response(
            200,
            json={
                "model": "test",
                "items": [{"candidate_id": "c1", "rank": 2, "score": 1}],
                "latency_ms": 1,
            },
        ),
    ],
)
async def test_http_provider_rejects_invalid_responses(response: httpx.Response) -> None:
    service, _ = provider(lambda request: response)
    with pytest.raises(RerankerUnavailableError) as caught:
        await service.rerank("query", candidates(), limit=1)
    assert caught.value.reason == "invalid_response"
    await service.close()
