import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.core.config import get_settings
from app.models.conversation import Conversation, ConversationMessage
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_entry import KnowledgeEntry
from app.repositories.knowledge_entry_indexing import KnowledgeEntryIndexingRepository

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured"),
]


def require_test_database_url() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not configured")
    if not (make_url(TEST_DATABASE_URL).database or "").endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must point to a database ending in '_test'")
    return TEST_DATABASE_URL


def migrate(revision: str) -> None:
    os.environ["DATABASE_URL"] = require_test_database_url()
    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), revision)
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    await asyncio.to_thread(migrate, "head")
    engine = create_async_engine(require_test_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db_session:
        yield db_session
        await db_session.rollback()
    await engine.dispose()


async def test_conversation_delete_nulls_provenance_but_preserves_snapshots(
    session: AsyncSession,
) -> None:
    knowledge_base = KnowledgeBase(name=f"Knowledge integration {uuid4()}")
    session.add(knowledge_base)
    await session.flush()
    conversation = Conversation(knowledge_base_id=knowledge_base.id, title="Problem")
    session.add(conversation)
    await session.flush()
    user = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        status="completed",
        content="Question snapshot",
    )
    assistant = ConversationMessage(
        conversation_id=conversation.id,
        role="assistant",
        status="completed",
        content="Answer snapshot",
    )
    session.add_all([user, assistant])
    await session.flush()
    entry = KnowledgeEntry(
        knowledge_base_id=knowledge_base.id,
        question="Editable question",
        solution="Editable solution",
        failed_attempts=[],
        validation_status="unverified",
        tags=["postgres"],
        source_conversation_id=conversation.id,
        source_user_message_id=user.id,
        source_assistant_message_id=assistant.id,
        question_snapshot=user.content,
        answer_snapshot=assistant.content,
        sources_snapshot=[],
    )
    session.add(entry)
    await session.commit()

    await session.delete(conversation)
    await session.commit()
    await session.refresh(entry)

    assert entry.source_conversation_id is None
    assert entry.source_user_message_id is None
    assert entry.source_assistant_message_id is None
    assert entry.question_snapshot == "Question snapshot"
    assert entry.answer_snapshot == "Answer snapshot"

    await session.delete(entry)
    await session.commit()
    remaining = await session.execute(select(KnowledgeEntry).where(KnowledgeEntry.id == entry.id))
    assert remaining.scalar_one_or_none() is None


async def test_database_enforces_unique_answer_and_validation_status(
    session: AsyncSession,
) -> None:
    knowledge_base = KnowledgeBase(name=f"Knowledge constraints {uuid4()}")
    session.add(knowledge_base)
    await session.flush()
    conversation = Conversation(knowledge_base_id=knowledge_base.id, title="Constraints")
    session.add(conversation)
    await session.flush()
    knowledge_base_id = knowledge_base.id
    conversation_id = conversation.id
    assistant = ConversationMessage(
        conversation_id=conversation_id,
        role="assistant",
        status="completed",
        content="One answer",
    )
    session.add(assistant)
    await session.flush()

    def make_entry(*, message_id: UUID, status: str = "unverified") -> KnowledgeEntry:
        return KnowledgeEntry(
            knowledge_base_id=knowledge_base_id,
            question="Question",
            solution="Solution",
            failed_attempts=[],
            validation_status=status,
            tags=[],
            source_assistant_message_id=message_id,
            question_snapshot="Question",
            answer_snapshot="Answer",
            sources_snapshot=[],
        )

    session.add(make_entry(message_id=assistant.id))
    await session.commit()

    session.add(make_entry(message_id=assistant.id))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    assistant_two = ConversationMessage(
        conversation_id=conversation_id,
        role="assistant",
        status="completed",
        content="Another answer",
    )
    session.add(assistant_two)
    await session.flush()
    session.add(make_entry(message_id=assistant_two.id, status="failed"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_index_state_updates_preserve_knowledge_edit_timestamp(
    session: AsyncSession,
) -> None:
    knowledge_base = KnowledgeBase(name=f"Knowledge indexing {uuid4()}")
    session.add(knowledge_base)
    await session.flush()
    entry = KnowledgeEntry(
        knowledge_base_id=knowledge_base.id,
        question="Why?",
        solution="Use one transaction",
        failed_attempts=[],
        validation_status="verified",
        tags=[],
        question_snapshot="Why?",
        answer_snapshot="Use one transaction",
        sources_snapshot=[],
        index_status="pending",
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    maintained_at = entry.updated_at
    generation = uuid4()
    repository = KnowledgeEntryIndexingRepository(session)

    await repository.mark_processing(entry, generation, datetime.now(UTC))
    await session.commit()
    await session.refresh(entry)
    assert entry.updated_at == maintained_at

    await repository.mark_succeeded(
        entry,
        generation=generation,
        source_updated_at=maintained_at,
        chunk_count=1,
        model_name="fake",
        dimension=3,
        indexed_at=datetime.now(UTC),
    )
    await session.commit()
    await session.refresh(entry)
    assert entry.updated_at == maintained_at
    active = await repository.list_active_generations(knowledge_base.id)
    assert [(item.entry_id, item.generation) for item in active] == [(entry.id, generation)]


def test_knowledge_entry_migration_round_trip() -> None:
    url = require_test_database_url()
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.downgrade(config, "20260803_0008")
    command.upgrade(config, "head")
    get_settings.cache_clear()
