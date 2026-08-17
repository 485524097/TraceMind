from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, ConversationMessage
from app.models.document import Document, DocumentVersion
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_entry import KnowledgeEntry


@dataclass(frozen=True)
class KnowledgeBaseArchiveSnapshot:
    knowledge_base: KnowledgeBase
    documents: tuple[Document, ...]
    document_versions: tuple[DocumentVersion, ...]
    conversations: tuple[Conversation, ...]
    messages: tuple[ConversationMessage, ...]
    knowledge_entries: tuple[KnowledgeEntry, ...]


@dataclass(frozen=True)
class RestoreConflictCheck:
    knowledge_base_id: UUID
    knowledge_base_name: str
    document_ids: tuple[UUID, ...]
    document_version_ids: tuple[UUID, ...]
    conversation_ids: tuple[UUID, ...]
    message_ids: tuple[UUID, ...]
    knowledge_entry_ids: tuple[UUID, ...]
    normalized_paths: tuple[str, ...]
    source_assistant_message_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class KnowledgeBaseRestoreEntities:
    knowledge_base: KnowledgeBase
    documents: tuple[Document, ...]
    document_versions: tuple[DocumentVersion, ...]
    conversations: tuple[Conversation, ...]
    messages: tuple[ConversationMessage, ...]
    knowledge_entries: tuple[KnowledgeEntry, ...]


class KnowledgeBaseArchiveRepository:
    """Read a stable, locked source-of-truth snapshot without committing it."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load_export_snapshot(
        self, knowledge_base_id: UUID
    ) -> KnowledgeBaseArchiveSnapshot | None:
        knowledge_base = (
            await self.session.execute(
                select(KnowledgeBase)
                .where(KnowledgeBase.id == knowledge_base_id)
                .with_for_update(read=True, of=KnowledgeBase)
            )
        ).scalar_one_or_none()
        if knowledge_base is None:
            return None

        documents = tuple(
            (
                await self.session.execute(
                    select(Document)
                    .where(Document.knowledge_base_id == knowledge_base_id)
                    .order_by(Document.created_at, Document.id)
                    .with_for_update(read=True, of=Document)
                )
            )
            .scalars()
            .all()
        )
        document_versions = tuple(
            (
                await self.session.execute(
                    select(DocumentVersion)
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(Document.knowledge_base_id == knowledge_base_id)
                    .order_by(
                        DocumentVersion.document_id,
                        DocumentVersion.version_number,
                        DocumentVersion.id,
                    )
                    .with_for_update(read=True, of=DocumentVersion)
                )
            )
            .scalars()
            .all()
        )
        conversations = tuple(
            (
                await self.session.execute(
                    select(Conversation)
                    .where(Conversation.knowledge_base_id == knowledge_base_id)
                    .order_by(Conversation.created_at, Conversation.id)
                    .with_for_update(read=True, of=Conversation)
                )
            )
            .scalars()
            .all()
        )
        messages = tuple(
            (
                await self.session.execute(
                    select(ConversationMessage)
                    .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
                    .where(Conversation.knowledge_base_id == knowledge_base_id)
                    .order_by(ConversationMessage.created_at, ConversationMessage.id)
                    .with_for_update(read=True, of=ConversationMessage)
                )
            )
            .scalars()
            .all()
        )
        knowledge_entries = tuple(
            (
                await self.session.execute(
                    select(KnowledgeEntry)
                    .where(KnowledgeEntry.knowledge_base_id == knowledge_base_id)
                    .order_by(KnowledgeEntry.created_at, KnowledgeEntry.id)
                    .with_for_update(read=True, of=KnowledgeEntry)
                )
            )
            .scalars()
            .all()
        )
        return KnowledgeBaseArchiveSnapshot(
            knowledge_base=knowledge_base,
            documents=documents,
            document_versions=document_versions,
            conversations=conversations,
            messages=messages,
            knowledge_entries=knowledge_entries,
        )

    async def find_restore_conflicts(self, check: RestoreConflictCheck) -> list[str]:
        conflicts: list[str] = []
        knowledge_bases = (
            await self.session.execute(
                select(KnowledgeBase.id, KnowledgeBase.name).where(
                    or_(
                        KnowledgeBase.id == check.knowledge_base_id,
                        KnowledgeBase.name == check.knowledge_base_name,
                    )
                )
            )
        ).all()
        if any(item.id == check.knowledge_base_id for item in knowledge_bases):
            conflicts.append("knowledge_base_id")
        if any(item.name == check.knowledge_base_name for item in knowledge_bases):
            conflicts.append("knowledge_base_name")

        if check.document_ids:
            existing = (
                await self.session.execute(
                    select(Document.id).where(Document.id.in_(check.document_ids))
                )
            ).scalars()
            if existing.first() is not None:
                conflicts.append("document_id")
        if check.document_version_ids:
            existing = (
                await self.session.execute(
                    select(DocumentVersion.id).where(
                        DocumentVersion.id.in_(check.document_version_ids)
                    )
                )
            ).scalars()
            if existing.first() is not None:
                conflicts.append("document_version_id")
        if check.conversation_ids:
            existing = (
                await self.session.execute(
                    select(Conversation.id).where(Conversation.id.in_(check.conversation_ids))
                )
            ).scalars()
            if existing.first() is not None:
                conflicts.append("conversation_id")
        if check.message_ids:
            existing = (
                await self.session.execute(
                    select(ConversationMessage.id).where(
                        ConversationMessage.id.in_(check.message_ids)
                    )
                )
            ).scalars()
            if existing.first() is not None:
                conflicts.append("message_id")
        if check.knowledge_entry_ids or check.source_assistant_message_ids:
            predicates = []
            if check.knowledge_entry_ids:
                predicates.append(KnowledgeEntry.id.in_(check.knowledge_entry_ids))
            if check.source_assistant_message_ids:
                predicates.append(
                    KnowledgeEntry.source_assistant_message_id.in_(
                        check.source_assistant_message_ids
                    )
                )
            existing_entries = (
                await self.session.execute(
                    select(
                        KnowledgeEntry.id,
                        KnowledgeEntry.source_assistant_message_id,
                    ).where(or_(*predicates))
                )
            ).all()
            if any(item.id in check.knowledge_entry_ids for item in existing_entries):
                conflicts.append("knowledge_entry_id")
            if any(
                item.source_assistant_message_id in check.source_assistant_message_ids
                for item in existing_entries
            ):
                conflicts.append("knowledge_source_assistant")
        if check.normalized_paths:
            existing = (
                await self.session.execute(
                    select(Document.id).where(
                        Document.knowledge_base_id == check.knowledge_base_id,
                        Document.normalized_path.in_(check.normalized_paths),
                    )
                )
            ).scalars()
            if existing.first() is not None:
                conflicts.append("normalized_document_path")
        return conflicts

    async def add_restore_entities(self, entities: KnowledgeBaseRestoreEntities) -> None:
        self.session.add(entities.knowledge_base)
        await self.session.flush()
        self.session.add_all(entities.documents)
        await self.session.flush()
        self.session.add_all(entities.document_versions)
        await self.session.flush()
        self.session.add_all(entities.conversations)
        await self.session.flush()
        self.session.add_all(entities.messages)
        await self.session.flush()
        self.session.add_all(entities.knowledge_entries)
        await self.session.flush()

    async def knowledge_base_exists(self, knowledge_base_id: UUID) -> bool:
        result = await self.session.execute(
            select(KnowledgeBase.id).where(KnowledgeBase.id == knowledge_base_id)
        )
        return result.scalar_one_or_none() is not None
