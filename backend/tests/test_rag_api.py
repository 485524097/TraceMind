import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import cast
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.rag import RagGraph, get_rag_runtime_context
from app.core.config import Settings
from app.main import create_app
from app.rag.graph import RagRuntimeContext, RagState
from app.services.exceptions import HybridSearchUnavailableError


@dataclass
class FakeGraph:
    events: list[dict[str, object]]
    error: Exception | None = None

    async def astream(
        self,
        graph_input: RagState,
        *,
        context: RagRuntimeContext,
        stream_mode: str,
    ) -> AsyncIterator[dict[str, object]]:
        if self.error is not None:
            raise self.error
        for event in self.events:
            yield event


async def client_for(
    app: FastAPI,
    graph: FakeGraph | None = None,
) -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        if graph is not None:
            app.state.rag_graph = cast(RagGraph, graph)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


def app_with_graph() -> FastAPI:
    app = create_app(Settings(_env_file=None, app_env="test"))
    app.dependency_overrides[get_rag_runtime_context] = lambda: cast(
        RagRuntimeContext,
        object(),
    )
    return app


def event_data(response_text: str, event_type: str) -> dict[str, object]:
    event_line = f"event: {event_type}"
    for block in response_text.split("\n\n"):
        lines = block.splitlines()
        if event_line in lines:
            data = next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
            return cast(dict[str, object], json.loads(data))
    raise AssertionError(f"Missing SSE event: {event_type}")


async def test_rag_api_returns_503_when_chat_model_is_disabled() -> None:
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
    assert response.json() == {"detail": "RAG answer generation is not configured"}


async def test_rag_api_streams_v2_native_sse_events_and_validates_request() -> None:
    graph = FakeGraph(
        [
            {"type": "sources", "source_count": 0, "sources": []},
            {"type": "token", "text": "answer"},
            {
                "type": "done",
                "terminal_status": "completed",
                "route_mode": "rag",
                "grounded": False,
                "valid_citation_count": 0,
                "invalid_citation_count": 0,
            },
        ]
    )
    app = app_with_graph()
    path = f"/api/v1/knowledge-bases/{uuid4()}/rag/stream"
    async for client in client_for(app, graph):
        response = await client.post(path, json={"query": " question "})
        invalid = await client.post(path, json={"query": " "})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: sources" in response.text
    assert "event: token" in response.text
    assert "event: done" in response.text
    assert "event: pipeline" not in response.text
    assert "event: retrieval" not in response.text
    done = event_data(response.text, "done")
    assert done["terminal_status"] == "completed"
    assert isinstance(done["conversation_persistence_latency_ms"], int)
    assert isinstance(done["response_total_latency_ms"], int)
    assert invalid.status_code == 422


async def test_rag_api_maps_retrieval_failure_to_safe_error_event() -> None:
    graph = FakeGraph([], HybridSearchUnavailableError("private retrieval detail"))
    app = app_with_graph()

    async for client in client_for(app, graph):
        response = await client.post(
            f"/api/v1/knowledge-bases/{uuid4()}/rag/stream",
            json={"query": "配置是什么？"},
        )

    assert response.status_code == 200
    assert "event: error" in response.text
    assert "retrieval_unavailable" in response.text
    assert event_data(response.text, "error")["message"] == "回答生成服务暂时不可用，请稍后重试。"
    assert "private retrieval detail" not in response.text
