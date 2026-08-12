from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.knowledge_entries import get_knowledge_entry_service
from app.core.config import Settings
from app.main import create_app
from app.models.knowledge_entry import KnowledgeEntry
from app.services.exceptions import KnowledgeEntryAlreadyExistsError, KnowledgeEntryNotFoundError
from app.services.knowledge_entry import KnowledgeEntryService


def make_entry() -> KnowledgeEntry:
    now = datetime.now(UTC)
    return KnowledgeEntry(
        id=uuid4(),
        knowledge_base_id=uuid4(),
        question="Why?",
        background=None,
        root_cause="A race",
        solution="Use one transaction",
        failed_attempts=["retry"],
        validation_status="unverified",
        tags=["python"],
        source_conversation_id=uuid4(),
        source_user_message_id=uuid4(),
        source_assistant_message_id=uuid4(),
        question_snapshot="Why?",
        answer_snapshot="Use one transaction [S1]",
        sources_snapshot=[],
        generation_metadata_snapshot=None,
        created_at=now,
        updated_at=now,
    )


def make_app(service: AsyncMock) -> FastAPI:
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_knowledge_entry_service] = lambda: service
    return app


async def client_for(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


async def test_knowledge_entry_crud_contract() -> None:
    service = AsyncMock(spec=KnowledgeEntryService)
    entry = make_entry()
    service.create.return_value = entry
    service.list.return_value = ([entry], 1, ["python"])
    service.get.return_value = entry
    service.update.return_value = entry
    base = f"/api/v1/knowledge-bases/{entry.knowledge_base_id}/knowledge-entries"
    payload = {
        "source_assistant_message_id": str(entry.source_assistant_message_id),
        "question": entry.question,
        "solution": entry.solution,
        "tags": ["Python"],
    }
    async for client in client_for(make_app(service)):
        created = await client.post(base, json=payload)
        listed = await client.get(f"{base}?validation_status=unverified&tag=python")
        detail = await client.get(f"{base}/{entry.id}")
        updated = await client.patch(f"{base}/{entry.id}", json={"validation_status": "verified"})
        deleted = await client.delete(f"{base}/{entry.id}")
    assert created.status_code == 201
    assert listed.json()["available_tags"] == ["python"]
    assert detail.json()["question_snapshot"] == "Why?"
    assert updated.status_code == 200
    assert deleted.status_code == 204


async def test_duplicate_missing_and_invalid_payload_errors() -> None:
    service = AsyncMock(spec=KnowledgeEntryService)
    entry = make_entry()
    base = f"/api/v1/knowledge-bases/{entry.knowledge_base_id}/knowledge-entries"
    service.create.side_effect = KnowledgeEntryAlreadyExistsError(
        entry.source_assistant_message_id or uuid4()
    )
    service.get.side_effect = KnowledgeEntryNotFoundError(entry.id)
    async for client in client_for(make_app(service)):
        duplicate = await client.post(
            base,
            json={
                "source_assistant_message_id": str(entry.source_assistant_message_id),
                "question": "Why?",
                "solution": "Fix",
            },
        )
        missing = await client.get(f"{base}/{entry.id}")
        invalid = await client.post(
            base,
            json={
                "source_assistant_message_id": str(uuid4()),
                "question": " ",
                "solution": "Fix",
            },
        )
    assert duplicate.status_code == 409
    assert missing.status_code == 404
    assert invalid.status_code == 422
