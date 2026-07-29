from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.knowledge_base import KnowledgeBase
from app.repositories.conversation import ConversationRepository
from app.repositories.knowledge_base import KnowledgeBaseRepository
from app.schemas.conversation import (
    DEFAULT_CONVERSATION_TITLE,
    ConversationCreate,
    ConversationUpdate,
)
from app.services.conversation import ConversationExchange, ConversationService
from app.services.exceptions import ConversationNotFoundError, KnowledgeBaseNotFoundError


def make_conversation(
    knowledge_base_id: object | None = None,
    *,
    title: str = DEFAULT_CONVERSATION_TITLE,
) -> Conversation:
    now = datetime.now(UTC)
    return Conversation(
        id=uuid4(),
        knowledge_base_id=knowledge_base_id or uuid4(),
        title=title,
        created_at=now,
        updated_at=now,
    )


def make_service() -> tuple[ConversationService, AsyncMock, AsyncMock, AsyncMock]:
    session = AsyncMock(spec=AsyncSession)
    repository = AsyncMock(spec=ConversationRepository)
    knowledge_bases = AsyncMock(spec=KnowledgeBaseRepository)
    service = ConversationService(
        cast(AsyncSession, session),
        cast(ConversationRepository, repository),
        cast(KnowledgeBaseRepository, knowledge_bases),
    )
    return service, session, repository, knowledge_bases


async def test_create_list_update_delete_conversation() -> None:
    service, session, repository, knowledge_bases = make_service()
    knowledge_base_id = uuid4()
    knowledge_bases.get_by_id.return_value = KnowledgeBase(id=knowledge_base_id, name="KB")
    repository.create.side_effect = lambda item: item
    created = await service.create(knowledge_base_id, ConversationCreate())
    assert created.title == DEFAULT_CONVERSATION_TITLE
    session.commit.assert_awaited_once()

    repository.list.return_value = [created]
    repository.count.return_value = 1
    assert await service.list(knowledge_base_id, offset=0, limit=20) == ([created], 1)

    repository.get_scoped.return_value = created
    updated = await service.update(
        knowledge_base_id, created.id, ConversationUpdate(title="新标题")
    )
    repository.update_title.assert_awaited_once_with(created, "新标题")
    assert updated is created

    await service.delete(knowledge_base_id, created.id)
    repository.delete.assert_awaited_once_with(created)


async def test_missing_knowledge_base_and_cross_scope_conversation_are_hidden() -> None:
    service, _, repository, knowledge_bases = make_service()
    knowledge_bases.get_by_id.return_value = None
    with pytest.raises(KnowledgeBaseNotFoundError):
        await service.create(uuid4(), ConversationCreate())

    repository.get_scoped.return_value = None
    with pytest.raises(ConversationNotFoundError):
        await service.get(uuid4(), uuid4())


async def test_first_question_creates_local_title_and_completed_user_message() -> None:
    service, session, repository, _ = make_service()
    conversation = make_conversation()
    repository.get_scoped.return_value = conversation
    trace_id = uuid4()

    exchange = await service.begin_exchange(
        conversation.knowledge_base_id,
        conversation.id,
        query="  如何配置 Nacos 服务注册与发现？  ",
        trace_id=trace_id,
    )

    message = repository.add_message.await_args.args[1]
    assert message.role == "user"
    assert message.status == "completed"
    assert message.content == "  如何配置 Nacos 服务注册与发现？  "
    assert message.trace_id == trace_id
    repository.update_title.assert_awaited_once_with(
        conversation, "如何配置 Nacos 服务注册与发现？"
    )
    assert exchange.conversation_id == conversation.id
    assert exchange.assistant_message_id is not None
    session.commit.assert_awaited_once()


async def test_custom_title_is_not_replaced() -> None:
    service, _, repository, _ = make_service()
    conversation = make_conversation(title="保留标题")
    repository.get_scoped.return_value = conversation
    await service.begin_exchange(
        conversation.knowledge_base_id,
        conversation.id,
        query="问题",
        trace_id=uuid4(),
    )
    repository.update_title.assert_not_awaited()


@pytest.mark.parametrize("status", ["completed", "no_answer", "failed", "cancelled"])
async def test_finish_exchange_saves_one_terminal_assistant_snapshot(status: str) -> None:
    service, session, repository, _ = make_service()
    conversation = make_conversation()
    repository.get_scoped.return_value = conversation
    exchange = await service.begin_exchange(
        conversation.knowledge_base_id,
        conversation.id,
        query="问题",
        trace_id=uuid4(),
    )
    repository.add_message.reset_mock()
    sources = [{"source_id": "S1", "content": "生成时正文"}]
    metadata = {"retrieval_mode": "hybrid_reranker", "reranker_fallback": False}

    await service.finish_exchange(
        exchange,
        status=status,
        content="安全回答",
        sources=sources,
        generation_metadata=metadata,
    )

    message = repository.add_message.await_args.args[1]
    assert message.id == exchange.assistant_message_id
    assert message.role == "assistant"
    assert message.status == status
    assert message.content == "安全回答"
    assert message.sources == sources
    assert message.generation_metadata == metadata
    assert message.trace_id == exchange.trace_id
    assert session.commit.await_count == 2


async def test_finish_exchange_rejects_non_terminal_status() -> None:
    service, _, _, _ = make_service()
    exchange = ConversationExchange(uuid4(), uuid4(), uuid4(), uuid4(), uuid4())
    with pytest.raises(ValueError):
        await service.finish_exchange(
            exchange,
            status="pending",
            content="",
            sources=None,
            generation_metadata=None,
        )
