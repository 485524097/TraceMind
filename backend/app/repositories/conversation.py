import builtins
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, ConversationMessage


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, conversation: Conversation) -> Conversation:
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    async def get_scoped(
        self, knowledge_base_id: UUID, conversation_id: UUID
    ) -> Conversation | None:
        result = await self.session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.knowledge_base_id == knowledge_base_id,
            )
        )
        return result.scalar_one_or_none()

    async def list(
        self, knowledge_base_id: UUID, *, offset: int, limit: int
    ) -> builtins.list[Conversation]:
        result = await self.session.execute(
            select(Conversation)
            .where(Conversation.knowledge_base_id == knowledge_base_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return builtins.list(result.scalars().all())

    async def count(self, knowledge_base_id: UUID) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.knowledge_base_id == knowledge_base_id)
        )
        return int(result.scalar_one())

    async def update_title(self, conversation: Conversation, title: str) -> Conversation:
        conversation.title = title
        conversation.updated_at = datetime.now(UTC)
        await self.session.flush()
        return conversation

    async def delete(self, conversation: Conversation) -> None:
        await self.session.delete(conversation)
        await self.session.flush()

    async def add_message(
        self, conversation: Conversation, message: ConversationMessage
    ) -> ConversationMessage:
        self.session.add(message)
        conversation.updated_at = datetime.now(UTC)
        await self.session.flush()
        return message

    async def list_messages(self, conversation_id: UUID) -> builtins.list[ConversationMessage]:
        result = await self.session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at, ConversationMessage.id)
        )
        return builtins.list(result.scalars().all())
