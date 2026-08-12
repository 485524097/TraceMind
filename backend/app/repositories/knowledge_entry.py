from __future__ import annotations

import builtins
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import Select, any_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, ConversationMessage
from app.models.knowledge_entry import KnowledgeEntry


class KnowledgeEntryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, entry: KnowledgeEntry) -> KnowledgeEntry:
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def get_scoped(self, knowledge_base_id: UUID, entry_id: UUID) -> KnowledgeEntry | None:
        result = await self.session.execute(
            select(KnowledgeEntry).where(
                KnowledgeEntry.id == entry_id,
                KnowledgeEntry.knowledge_base_id == knowledge_base_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_source_assistant(
        self, knowledge_base_id: UUID, message_id: UUID
    ) -> KnowledgeEntry | None:
        result = await self.session.execute(
            select(KnowledgeEntry).where(
                KnowledgeEntry.knowledge_base_id == knowledge_base_id,
                KnowledgeEntry.source_assistant_message_id == message_id,
            )
        )
        return result.scalar_one_or_none()

    async def source_message(
        self, knowledge_base_id: UUID, message_id: UUID
    ) -> tuple[Conversation, ConversationMessage] | None:
        result = await self.session.execute(
            select(Conversation, ConversationMessage)
            .join(ConversationMessage, ConversationMessage.conversation_id == Conversation.id)
            .where(
                Conversation.knowledge_base_id == knowledge_base_id,
                ConversationMessage.id == message_id,
            )
        )
        row = result.one_or_none()
        return (row[0], row[1]) if row is not None else None

    async def conversation_messages(self, conversation_id: UUID) -> list[ConversationMessage]:
        result = await self.session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at, ConversationMessage.id)
        )
        return list(result.scalars().all())

    def _filtered_statement(
        self,
        knowledge_base_id: UUID,
        *,
        query: str | None,
        validation_status: str | None,
        tag: str | None,
    ) -> Select[tuple[KnowledgeEntry]]:
        statement = select(KnowledgeEntry).where(
            KnowledgeEntry.knowledge_base_id == knowledge_base_id
        )
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            statement = statement.where(
                or_(
                    KnowledgeEntry.question.ilike(pattern, escape="\\"),
                    KnowledgeEntry.background.ilike(pattern, escape="\\"),
                    KnowledgeEntry.root_cause.ilike(pattern, escape="\\"),
                    KnowledgeEntry.solution.ilike(pattern, escape="\\"),
                )
            )
        if validation_status:
            statement = statement.where(KnowledgeEntry.validation_status == validation_status)
        if tag:
            statement = statement.where(any_(KnowledgeEntry.tags) == tag)
        return statement

    async def list(
        self,
        knowledge_base_id: UUID,
        *,
        query: str | None,
        validation_status: str | None,
        tag: str | None,
        offset: int,
        limit: int,
    ) -> list[KnowledgeEntry]:
        statement = self._filtered_statement(
            knowledge_base_id,
            query=query,
            validation_status=validation_status,
            tag=tag,
        )
        result = await self.session.execute(
            statement.order_by(KnowledgeEntry.updated_at.desc(), KnowledgeEntry.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count(
        self,
        knowledge_base_id: UUID,
        *,
        query: str | None = None,
        validation_status: str | None = None,
        tag: str | None = None,
    ) -> int:
        filtered = self._filtered_statement(
            knowledge_base_id,
            query=query,
            validation_status=validation_status,
            tag=tag,
        ).subquery()
        result = await self.session.execute(select(func.count()).select_from(filtered))
        return int(result.scalar_one())

    async def available_tags(self, knowledge_base_id: UUID) -> builtins.list[str]:
        result = await self.session.execute(
            select(KnowledgeEntry.tags).where(KnowledgeEntry.knowledge_base_id == knowledge_base_id)
        )
        return sorted({tag for tags in result.scalars().all() for tag in tags})

    async def knowledge_entry_ids_by_messages(
        self, knowledge_base_id: UUID, message_ids: Iterable[UUID]
    ) -> dict[UUID, UUID]:
        ids = list(message_ids)
        if not ids:
            return {}
        result = await self.session.execute(
            select(
                KnowledgeEntry.source_assistant_message_id,
                KnowledgeEntry.id,
            ).where(
                KnowledgeEntry.knowledge_base_id == knowledge_base_id,
                KnowledgeEntry.source_assistant_message_id.in_(ids),
            )
        )
        return {message_id: entry_id for message_id, entry_id in result if message_id is not None}

    async def update(self, entry: KnowledgeEntry, changes: dict[str, object]) -> KnowledgeEntry:
        for field, value in changes.items():
            setattr(entry, field, value)
        await self.session.flush()
        return entry

    async def delete(self, entry: KnowledgeEntry) -> None:
        await self.session.delete(entry)
        await self.session.flush()

    async def list_all(self, knowledge_base_id: UUID) -> builtins.list[KnowledgeEntry]:
        result = await self.session.execute(
            select(KnowledgeEntry)
            .where(KnowledgeEntry.knowledge_base_id == knowledge_base_id)
            .order_by(KnowledgeEntry.created_at, KnowledgeEntry.id)
        )
        return list(result.scalars().all())
