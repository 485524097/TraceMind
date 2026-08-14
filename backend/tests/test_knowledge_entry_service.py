from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, ConversationMessage
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_entry import KnowledgeEntry
from app.repositories.knowledge_base import KnowledgeBaseRepository
from app.repositories.knowledge_entry import KnowledgeEntryRepository
from app.schemas.knowledge_entry import KnowledgeEntryCreate, KnowledgeEntryUpdate
from app.services.exceptions import (
    InvalidKnowledgeEntrySourceError,
    KnowledgeEntryAlreadyExistsError,
    KnowledgeEntryNotFoundError,
)
from app.services.knowledge_entry import KnowledgeEntryService
from app.services.knowledge_entry_index_dispatcher import KnowledgeEntryIndexingDispatcher


def source_payload(
    source_id: str = "S1", knowledge_base_id: object | None = None
) -> dict[str, object]:
    return {
        "source_id": source_id,
        "score": 0.9,
        "content": "safe excerpt",
        "knowledge_base_id": str(knowledge_base_id or uuid4()),
        "document_id": str(uuid4()),
        "document_version_id": str(uuid4()),
        "chunk_id": str(uuid4()),
        "index_generation": str(uuid4()),
        "document_name": "guide.md",
        "relative_path": "docs/guide.md",
        "version_number": 1,
        "chunk_index": 2,
        "content_hash": "a" * 64,
        "chunk_type": "text",
        "language": "markdown",
        "section_title": "Setup",
        "page_number": None,
        "start_line": 4,
        "end_line": 8,
        "ranking_mode": "hybrid_reranker",
        "retrieval_score": 0.8,
        "retrieval_rank": 1,
    }


def make_messages(
    status: str = "completed",
) -> tuple[Conversation, ConversationMessage, ConversationMessage]:
    now = datetime.now(UTC)
    trace_id = uuid4()
    conversation = Conversation(
        id=uuid4(), knowledge_base_id=uuid4(), title="Problem", created_at=now, updated_at=now
    )
    user = ConversationMessage(
        id=uuid4(),
        conversation_id=conversation.id,
        role="user",
        status="completed",
        content="Why does it fail?",
        trace_id=trace_id,
        created_at=now,
    )
    assistant = ConversationMessage(
        id=uuid4(),
        conversation_id=conversation.id,
        role="assistant",
        status=status,
        content="Use a transaction [S1]. Recheck [S1].",
        trace_id=trace_id,
        sources=[
            source_payload(knowledge_base_id=conversation.knowledge_base_id),
            source_payload("S2", conversation.knowledge_base_id),
        ],
        generation_metadata={
            "grounded": True,
            "retrieval_mode": "hybrid_reranker",
            "retrieval_query": "private internal query",
            "total_latency_ms": 15,
        },
        created_at=now,
    )
    return conversation, user, assistant


def make_service() -> tuple[KnowledgeEntryService, AsyncMock, AsyncMock, AsyncMock]:
    session = AsyncMock(spec=AsyncSession)
    repository = AsyncMock(spec=KnowledgeEntryRepository)
    knowledge_bases = AsyncMock(spec=KnowledgeBaseRepository)
    service = KnowledgeEntryService(
        cast(AsyncSession, session),
        cast(KnowledgeEntryRepository, repository),
        cast(KnowledgeBaseRepository, knowledge_bases),
    )
    return service, session, repository, knowledge_bases


def create_payload(message_id: object) -> KnowledgeEntryCreate:
    return KnowledgeEntryCreate(
        source_assistant_message_id=message_id,
        question="  Why?  ",
        solution="  Fix it  ",
        tags=[" Python ", "PYTHON", " postgres "],
        failed_attempts=["  restart  ", ""],
    )


async def test_create_derives_provenance_and_safe_snapshots() -> None:
    service, session, repository, knowledge_bases = make_service()
    conversation, user, assistant = make_messages()
    knowledge_bases.get_by_id.return_value = KnowledgeBase(
        id=conversation.knowledge_base_id, name="KB"
    )
    repository.get_by_source_assistant.return_value = None
    repository.source_message.return_value = (conversation, assistant)
    repository.conversation_messages.return_value = [user, assistant]
    repository.create.side_effect = lambda entry: entry

    result = await service.create(conversation.knowledge_base_id, create_payload(assistant.id))

    assert result.source_conversation_id == conversation.id
    assert result.source_user_message_id == user.id
    assert result.source_assistant_message_id == assistant.id
    assert result.question_snapshot == user.content
    assert result.answer_snapshot == assistant.content
    assert result.tags == ["python", "postgres"]
    assert result.failed_attempts == ["restart"]
    assert [source["source_id"] for source in result.sources_snapshot] == ["S1"]
    assert "score" not in result.sources_snapshot[0]
    assert "index_generation" not in result.sources_snapshot[0]
    assert result.generation_metadata_snapshot == {
        "grounded": True,
        "retrieval_mode": "hybrid_reranker",
        "total_latency_ms": 15,
    }
    session.commit.assert_awaited_once()


async def test_create_rejects_non_completed_answer_and_missing_pair() -> None:
    service, _, repository, knowledge_bases = make_service()
    conversation, user, assistant = make_messages("failed")
    knowledge_bases.get_by_id.return_value = KnowledgeBase(
        id=conversation.knowledge_base_id, name="KB"
    )
    repository.get_by_source_assistant.return_value = None
    repository.source_message.return_value = (conversation, assistant)
    with pytest.raises(InvalidKnowledgeEntrySourceError):
        await service.create(conversation.knowledge_base_id, create_payload(assistant.id))

    assistant.status = "completed"
    repository.conversation_messages.return_value = [assistant]
    with pytest.raises(InvalidKnowledgeEntrySourceError):
        await service.create(conversation.knowledge_base_id, create_payload(assistant.id))


async def test_paired_user_falls_back_when_trace_is_missing() -> None:
    service, _, repository, knowledge_bases = make_service()
    conversation, user, assistant = make_messages()
    user.trace_id = None
    assistant.trace_id = None
    knowledge_bases.get_by_id.return_value = KnowledgeBase(
        id=conversation.knowledge_base_id, name="KB"
    )
    repository.get_by_source_assistant.return_value = None
    repository.source_message.return_value = (conversation, assistant)
    repository.conversation_messages.return_value = [user, assistant]
    result = await service.create(conversation.knowledge_base_id, create_payload(assistant.id))
    assert result.source_user_message_id == user.id


async def test_duplicate_and_database_errors_roll_back() -> None:
    service, session, repository, knowledge_bases = make_service()
    conversation, user, assistant = make_messages()
    knowledge_bases.get_by_id.return_value = KnowledgeBase(
        id=conversation.knowledge_base_id, name="KB"
    )
    repository.get_by_source_assistant.return_value = None
    repository.source_message.return_value = (conversation, assistant)
    repository.conversation_messages.return_value = [user, assistant]
    repository.create.side_effect = IntegrityError("insert", {}, Exception("duplicate"))
    with pytest.raises(KnowledgeEntryAlreadyExistsError):
        await service.create(conversation.knowledge_base_id, create_payload(assistant.id))
    session.rollback.assert_awaited_once()

    repository.create.side_effect = OperationalError("insert", {}, Exception("database"))
    with pytest.raises(OperationalError):
        await service.create(conversation.knowledge_base_id, create_payload(assistant.id))
    assert session.rollback.await_count == 2


async def test_list_update_delete_are_scoped_and_transactional() -> None:
    service, session, repository, knowledge_bases = make_service()
    knowledge_base_id = uuid4()
    entry = KnowledgeEntry(
        id=uuid4(),
        knowledge_base_id=knowledge_base_id,
        question="Question",
        solution="Solution",
        failed_attempts=[],
        validation_status="unverified",
        tags=["python"],
        question_snapshot="Question",
        answer_snapshot="Solution",
        sources_snapshot=[],
    )
    knowledge_bases.get_by_id.return_value = KnowledgeBase(id=knowledge_base_id, name="KB")
    repository.list.return_value = [entry]
    repository.count.return_value = 1
    repository.available_tags.return_value = ["python"]
    items, total, tags = await service.list(
        knowledge_base_id,
        query=" question ",
        validation_status="unverified",
        tag=" PYTHON ",
        offset=0,
        limit=20,
    )
    assert (items, total, tags) == ([entry], 1, ["python"])
    repository.list.assert_awaited_once_with(
        knowledge_base_id,
        query="question",
        validation_status="unverified",
        tag="python",
        offset=0,
        limit=20,
    )

    repository.get_scoped.return_value = entry
    await service.update(
        knowledge_base_id,
        entry.id,
        KnowledgeEntryUpdate(validation_status="verified"),
    )
    await service.delete(knowledge_base_id, entry.id)
    assert session.commit.await_count == 2

    repository.get_scoped.return_value = None
    with pytest.raises(KnowledgeEntryNotFoundError):
        await service.get(knowledge_base_id, uuid4())


async def test_verified_update_marks_pending_and_enqueues_index() -> None:
    service, _, repository, _ = make_service()
    dispatcher = AsyncMock(spec=KnowledgeEntryIndexingDispatcher)
    service.dispatcher = cast(KnowledgeEntryIndexingDispatcher, dispatcher)
    entry = KnowledgeEntry(
        id=uuid4(),
        knowledge_base_id=uuid4(),
        question="Question",
        solution="Solution",
        failed_attempts=[],
        validation_status="unverified",
        tags=[],
        question_snapshot="Question",
        answer_snapshot="Answer",
        sources_snapshot=[],
        index_status="not_indexed",
        indexed_chunk_count=0,
        updated_at=datetime.now(UTC),
    )
    repository.get_scoped.return_value = entry

    async def apply_update(target: KnowledgeEntry, changes: dict[str, object]) -> KnowledgeEntry:
        for key, value in changes.items():
            setattr(target, key, value)
        return target

    repository.update.side_effect = apply_update

    result = await service.update(
        entry.knowledge_base_id,
        entry.id,
        KnowledgeEntryUpdate(validation_status="verified"),
    )

    assert result.index_status == "pending"
    dispatcher.enqueue_sync.assert_awaited_once_with(entry.id, force=False)
