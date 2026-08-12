from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.rag import get_rag_service
from app.core.config import Settings
from app.main import create_app


async def client_for(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


@dataclass
class FakeRagService:
    events: list[tuple[str, dict[str, object]]]
    prepare_error: Exception | None = None

    async def stream_query(
        self, *args: object, **kwargs: object
    ) -> AsyncGenerator[tuple[str, dict[str, object]]]:
        if self.prepare_error is not None:
            raise self.prepare_error
        for event in self.events:
            yield event


async def test_rag_api_returns_503_when_llm_is_disabled() -> None:
    app = create_app(
        Settings(
            _env_file=None,
            app_env="test",
            llm_base_url=None,
            llm_model=None,
            llm_api_key=None,
        )
    )
    async for client in client_for(app):
        response = await client.post(
            f"/api/v1/knowledge-bases/{uuid4()}/rag/stream",
            json={"query": "question"},
        )
    assert response.status_code == 503
    assert "API" not in response.text


async def test_rag_api_streams_native_sse_events_and_validates_request() -> None:
    trace_id = str(uuid4())
    service = FakeRagService(
        [
            (
                "retrieval",
                {
                    "trace_id": trace_id,
                    "source_count": 0,
                    "sources": [],
                },
            ),
            ("token", {"trace_id": trace_id, "text": "answer"}),
            (
                "done",
                {
                    "trace_id": trace_id,
                    "finish_reason": "stop",
                    "grounded": False,
                    "valid_citation_count": 0,
                    "invalid_citation_count": 0,
                    "retrieval_latency_ms": 1,
                    "llm_latency_ms": 2,
                    "total_latency_ms": 3,
                },
            ),
        ]
    )
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_rag_service] = lambda: service
    path = f"/api/v1/knowledge-bases/{uuid4()}/rag/stream"
    async for client in client_for(app):
        response = await client.post(path, json={"query": " question "})
        invalid = await client.post(path, json={"query": " "})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: retrieval" in response.text
    assert "event: token" in response.text
    assert "event: done" in response.text
    assert '"conversation_persistence_latency_ms":' in response.text
    assert '"response_total_latency_ms":' in response.text
    assert invalid.status_code == 422


async def test_rag_api_streams_safe_error_for_retrieval_failure() -> None:
    service = FakeRagService(
        [
            (
                "error",
                {
                    "trace_id": str(uuid4()),
                    "code": "retrieval_unavailable",
                    "message": "回答生成服务暂时不可用，请稍后重试。",
                },
            )
        ],
    )
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_rag_service] = lambda: service

    async for client in client_for(app):
        response = await client.post(
            f"/api/v1/knowledge-bases/{uuid4()}/rag/stream",
            json={"query": "配置是什么？"},
        )

    assert response.status_code == 200
    assert "event: error" in response.text
    assert "retrieval_unavailable" in response.text
