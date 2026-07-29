from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.conversations import get_conversation_service
from app.core.config import Settings
from app.main import create_app
from app.models.conversation import Conversation, ConversationMessage
from app.services.conversation import ConversationService
from app.services.exceptions import ConversationNotFoundError


def make_conversation() -> Conversation:
    now = datetime.now(UTC)
    return Conversation(
        id=uuid4(),
        knowledge_base_id=uuid4(),
        title="会话",
        created_at=now,
        updated_at=now,
    )


def make_app(service: AsyncMock) -> FastAPI:
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_conversation_service] = lambda: service
    return app


async def client_for(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


async def test_conversation_crud_and_stable_detail_shape() -> None:
    service = AsyncMock(spec=ConversationService)
    conversation = make_conversation()
    message = ConversationMessage(
        id=uuid4(),
        conversation_id=conversation.id,
        role="assistant",
        status="completed",
        content="回答",
        trace_id=uuid4(),
        sources=[{"source_id": "S1", "content": "快照"}],
        generation_metadata={"retrieval_mode": "hybrid"},
        created_at=conversation.created_at,
    )
    service.create.return_value = conversation
    service.list.return_value = ([conversation], 1)
    service.get_detail.return_value = (conversation, [message])
    service.update.return_value = conversation
    base = f"/api/v1/knowledge-bases/{conversation.knowledge_base_id}/conversations"

    async for client in client_for(make_app(service)):
        created = await client.post(base, json={})
        listed = await client.get(base)
        detail = await client.get(f"{base}/{conversation.id}")
        updated = await client.patch(f"{base}/{conversation.id}", json={"title": "会话"})
        deleted = await client.delete(f"{base}/{conversation.id}")

    assert created.status_code == 201
    assert listed.json()["total"] == 1
    assert detail.json()["messages"][0]["sources"][0]["content"] == "快照"
    assert updated.status_code == 200
    assert deleted.status_code == 204


async def test_cross_knowledge_base_access_returns_404() -> None:
    service = AsyncMock(spec=ConversationService)
    conversation_id = uuid4()
    service.get_detail.side_effect = ConversationNotFoundError(conversation_id)
    app = make_app(service)
    async for client in client_for(app):
        response = await client.get(
            f"/api/v1/knowledge-bases/{uuid4()}/conversations/{conversation_id}"
        )
    assert response.status_code == 404
    assert response.json()["detail"] == "Conversation not found"


async def test_invalid_titles_return_422() -> None:
    service = AsyncMock(spec=ConversationService)
    base = f"/api/v1/knowledge-bases/{uuid4()}/conversations"
    async for client in client_for(make_app(service)):
        create_response = await client.post(base, json={"title": "   "})
        update_response = await client.patch(f"{base}/{uuid4()}", json={})
    assert create_response.status_code == 422
    assert update_response.status_code == 422
