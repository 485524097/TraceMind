import builtins
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, ConversationMessage
from app.repositories.conversation import ConversationRepository
from app.repositories.knowledge_base import KnowledgeBaseRepository
from app.repositories.knowledge_entry import KnowledgeEntryRepository
from app.schemas.conversation import (
    DEFAULT_CONVERSATION_TITLE,
    ConversationCreate,
    ConversationUpdate,
)
from app.services.exceptions import ConversationNotFoundError, KnowledgeBaseNotFoundError

TITLE_MAX_CHARS = 40


@dataclass(frozen=True)
class ConversationExchange:
    knowledge_base_id: UUID
    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID
    trace_id: UUID
    history: tuple["ConversationTurn", ...] = ()


@dataclass(frozen=True)
class ConversationTurn:
    user: str
    assistant: str


class ConversationService:
    def __init__(
        self,
        session: AsyncSession,
        repository: ConversationRepository | None = None,
        knowledge_bases: KnowledgeBaseRepository | None = None,
        knowledge_entries: KnowledgeEntryRepository | None = None,
    ) -> None:
        self.session = session
        self.repository = repository or ConversationRepository(session)
        self.knowledge_bases = knowledge_bases or KnowledgeBaseRepository(session)
        self.knowledge_entries = knowledge_entries or KnowledgeEntryRepository(session)

    async def _require_knowledge_base(self, knowledge_base_id: UUID) -> None:
        if await self.knowledge_bases.get_by_id(knowledge_base_id) is None:
            raise KnowledgeBaseNotFoundError(knowledge_base_id)

    async def create(self, knowledge_base_id: UUID, payload: ConversationCreate) -> Conversation:
        await self._require_knowledge_base(knowledge_base_id)
        conversation = Conversation(knowledge_base_id=knowledge_base_id, title=payload.title)
        try:
            await self.repository.create(conversation)
            await self.session.commit()
            await self.session.refresh(conversation)
        except SQLAlchemyError:
            await self.session.rollback()
            raise
        return conversation

    async def get(self, knowledge_base_id: UUID, conversation_id: UUID) -> Conversation:
        conversation = await self.repository.get_scoped(knowledge_base_id, conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)
        return conversation

    async def get_detail(
        self, knowledge_base_id: UUID, conversation_id: UUID
    ) -> tuple[Conversation, list[ConversationMessage]]:
        conversation = await self.get(knowledge_base_id, conversation_id)
        messages = await self.repository.list_messages(conversation.id)
        return conversation, messages

    async def get_detail_with_knowledge(
        self, knowledge_base_id: UUID, conversation_id: UUID
    ) -> tuple[Conversation, list[ConversationMessage], dict[UUID, UUID]]:
        conversation, messages = await self.get_detail(knowledge_base_id, conversation_id)
        entry_ids = await self.knowledge_entries.knowledge_entry_ids_by_messages(
            knowledge_base_id,
            (message.id for message in messages if message.role == "assistant"),
        )
        return conversation, messages, entry_ids

    async def list(
        self, knowledge_base_id: UUID, *, offset: int, limit: int
    ) -> tuple[builtins.list[Conversation], int]:
        await self._require_knowledge_base(knowledge_base_id)
        items = await self.repository.list(knowledge_base_id, offset=offset, limit=limit)
        return items, await self.repository.count(knowledge_base_id)

    async def update(
        self,
        knowledge_base_id: UUID,
        conversation_id: UUID,
        payload: ConversationUpdate,
    ) -> Conversation:
        conversation = await self.get(knowledge_base_id, conversation_id)
        try:
            await self.repository.update_title(conversation, payload.title or "")
            await self.session.commit()
            await self.session.refresh(conversation)
        except SQLAlchemyError:
            await self.session.rollback()
            raise
        return conversation

    async def delete(self, knowledge_base_id: UUID, conversation_id: UUID) -> None:
        conversation = await self.get(knowledge_base_id, conversation_id)
        try:
            await self.repository.delete(conversation)
            await self.session.commit()
        except SQLAlchemyError:
            await self.session.rollback()
            raise

    async def begin_exchange(
        self,
        knowledge_base_id: UUID,
        conversation_id: UUID,
        *,
        query: str,
        trace_id: UUID,
        history_max_turns: int = 4,
        history_max_chars: int = 6_000,
    ) -> ConversationExchange:
        conversation = await self.get(knowledge_base_id, conversation_id)
        messages = await self.repository.list_messages(conversation.id)
        history = self._select_history(
            messages,
            max_turns=history_max_turns,
            max_chars=history_max_chars,
        )
        user_message = ConversationMessage(
            id=uuid4(),
            conversation_id=conversation.id,
            role="user",
            status="completed",
            content=query,
            trace_id=trace_id,
        )
        try:
            await self.repository.add_message(conversation, user_message)
            if conversation.title == DEFAULT_CONVERSATION_TITLE:
                await self.repository.update_title(
                    conversation, query.strip()[:TITLE_MAX_CHARS] or DEFAULT_CONVERSATION_TITLE
                )
            await self.session.commit()
            await self.session.refresh(user_message)
        except SQLAlchemyError:
            await self.session.rollback()
            raise
        return ConversationExchange(
            knowledge_base_id,
            conversation.id,
            user_message.id,
            uuid4(),
            trace_id,
            history,
        )

    @staticmethod
    def _select_history(
        messages: builtins.list[ConversationMessage],
        *,
        max_turns: int,
        max_chars: int,
    ) -> tuple[ConversationTurn, ...]:
        completed: builtins.list[ConversationTurn] = []
        pending_user: str | None = None
        for message in messages:
            if message.role == "user":
                pending_user = message.content if message.status == "completed" else None
                continue
            if message.role != "assistant":
                pending_user = None
                continue
            if pending_user is not None and message.status == "completed":
                completed.append(ConversationTurn(pending_user, message.content))
            pending_user = None

        selected: builtins.list[ConversationTurn] = []
        total_chars = 0
        for turn in reversed(completed[-max_turns:]):
            turn_chars = len(turn.user) + len(turn.assistant)
            if total_chars + turn_chars > max_chars:
                break
            selected.append(turn)
            total_chars += turn_chars
        selected.reverse()
        return tuple(selected)

    async def finish_exchange(
        self,
        exchange: ConversationExchange,
        *,
        status: str,
        content: str,
        sources: builtins.list[dict[str, Any]] | None,
        generation_metadata: dict[str, Any] | None,
    ) -> ConversationMessage:
        if status not in {"completed", "no_answer", "failed", "cancelled"}:
            raise ValueError("Assistant message status must be terminal")
        conversation = await self.repository.get_scoped(
            exchange.knowledge_base_id,
            exchange.conversation_id,
        )
        if conversation is None:
            raise ConversationNotFoundError(exchange.conversation_id)
        message = ConversationMessage(
            id=exchange.assistant_message_id,
            conversation_id=exchange.conversation_id,
            role="assistant",
            status=status,
            content=content,
            trace_id=exchange.trace_id,
            sources=sources,
            generation_metadata=generation_metadata,
        )
        try:
            await self.repository.add_message(conversation, message)
            await self.session.commit()
            await self.session.refresh(message)
        except SQLAlchemyError:
            await self.session.rollback()
            raise
        return message
