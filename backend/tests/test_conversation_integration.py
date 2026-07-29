import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.core.config import get_settings
from app.models.conversation import Conversation, ConversationMessage
from app.models.knowledge_base import KnowledgeBase
from app.repositories.conversation import ConversationRepository

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


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    os.environ["DATABASE_URL"] = require_test_database_url()
    get_settings.cache_clear()
    await asyncio.to_thread(command.upgrade, Config("alembic.ini"), "head")
    get_settings.cache_clear()
    engine = create_async_engine(require_test_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db_session:
        yield db_session
        await db_session.rollback()
    await engine.dispose()


async def test_scope_stable_message_order_and_cascade_delete(session: AsyncSession) -> None:
    first_kb = KnowledgeBase(name=f"Conversation KB {uuid4()}")
    second_kb = KnowledgeBase(name=f"Other KB {uuid4()}")
    session.add_all([first_kb, second_kb])
    await session.flush()
    conversation = Conversation(
        knowledge_base_id=first_kb.id,
        title="Integration conversation",
    )
    session.add(conversation)
    await session.flush()
    same_time = datetime.now(UTC)
    later_id = uuid4()
    earlier_id = uuid4()
    if str(later_id) < str(earlier_id):
        later_id, earlier_id = earlier_id, later_id
    session.add_all(
        [
            ConversationMessage(
                id=later_id,
                conversation_id=conversation.id,
                role="assistant",
                status="completed",
                content="second",
                created_at=same_time,
            ),
            ConversationMessage(
                id=earlier_id,
                conversation_id=conversation.id,
                role="user",
                status="completed",
                content="first",
                created_at=same_time,
            ),
        ]
    )
    await session.commit()

    repository = ConversationRepository(session)
    assert await repository.get_scoped(first_kb.id, conversation.id) is conversation
    assert await repository.get_scoped(second_kb.id, conversation.id) is None
    messages = await repository.list_messages(conversation.id)
    assert [message.id for message in messages] == [earlier_id, later_id]

    await session.delete(first_kb)
    await session.commit()
    remaining = await session.execute(
        select(ConversationMessage).where(ConversationMessage.conversation_id == conversation.id)
    )
    assert remaining.scalars().all() == []
