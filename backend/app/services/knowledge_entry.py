import re
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import ConversationMessage
from app.models.knowledge_entry import KnowledgeEntry
from app.repositories.knowledge_base import KnowledgeBaseRepository
from app.repositories.knowledge_entry import KnowledgeEntryRepository
from app.schemas.knowledge_entry import KnowledgeEntryCreate, KnowledgeEntryUpdate
from app.schemas.rag import RagSource
from app.services.exceptions import (
    InvalidKnowledgeEntrySourceError,
    KnowledgeBaseNotFoundError,
    KnowledgeEntryAlreadyExistsError,
    KnowledgeEntryNotFoundError,
    KnowledgeEntrySourceNotFoundError,
)

_CITATION_PATTERN = re.compile(r"\[S\d+\]")
_SOURCE_SNAPSHOT_FIELDS = (
    "source_id",
    "document_id",
    "document_version_id",
    "chunk_id",
    "document_name",
    "relative_path",
    "version_number",
    "chunk_index",
    "content",
    "content_hash",
    "chunk_type",
    "language",
    "section_title",
    "page_number",
    "start_line",
    "end_line",
    "symbol_kind",
    "symbol_name",
    "symbol_qualified_name",
    "symbol_signature",
)
_GENERATION_METADATA_FIELDS = {
    "finish_reason",
    "grounded",
    "valid_citation_count",
    "invalid_citation_count",
    "source_count",
    "retrieval_mode",
    "retrieval_latency_ms",
    "query_rewrite_mode",
    "query_rewrite_latency_ms",
    "rerank_latency_ms",
    "reranker_fallback",
    "llm_first_token_latency_ms",
    "llm_latency_ms",
    "total_latency_ms",
    "history_turn_count",
    "path_scope_mode",
    "scoped_relative_path",
    "symbol_scope_mode",
    "symbol_scope_reason",
    "scoped_symbol_kind",
    "scoped_symbol_qualified_name",
    "scoped_symbol_signature",
}


class KnowledgeEntryService:
    def __init__(
        self,
        session: AsyncSession,
        repository: KnowledgeEntryRepository | None = None,
        knowledge_bases: KnowledgeBaseRepository | None = None,
    ) -> None:
        self.session = session
        self.repository = repository or KnowledgeEntryRepository(session)
        self.knowledge_bases = knowledge_bases or KnowledgeBaseRepository(session)

    async def _require_knowledge_base(self, knowledge_base_id: UUID) -> None:
        if await self.knowledge_bases.get_by_id(knowledge_base_id) is None:
            raise KnowledgeBaseNotFoundError(knowledge_base_id)

    @staticmethod
    def _paired_user(
        assistant: ConversationMessage, messages: list[ConversationMessage]
    ) -> ConversationMessage:
        earlier: list[ConversationMessage] = []
        for message in messages:
            if message.id == assistant.id:
                break
            earlier.append(message)
        if assistant.trace_id is not None:
            traced = [
                message
                for message in earlier
                if message.role == "user"
                and message.status == "completed"
                and message.trace_id == assistant.trace_id
            ]
            if traced:
                return traced[-1]
        fallback = [
            message
            for message in earlier
            if message.role == "user" and message.status == "completed"
        ]
        if fallback:
            return fallback[-1]
        raise InvalidKnowledgeEntrySourceError("Assistant answer has no paired user message")

    @staticmethod
    def _sources_snapshot(
        knowledge_base_id: UUID, message: ConversationMessage
    ) -> list[dict[str, object]]:
        citation_ids: list[str] = []
        seen: set[str] = set()
        for match in _CITATION_PATTERN.finditer(message.content):
            source_id = match.group(0)[1:-1]
            if source_id not in seen:
                seen.add(source_id)
                citation_ids.append(source_id)
        if not citation_ids:
            return []

        raw_sources = message.sources or []
        by_id = {
            source.get("source_id"): source
            for source in raw_sources
            if isinstance(source, dict) and isinstance(source.get("source_id"), str)
        }
        snapshots: list[dict[str, object]] = []
        for source_id in citation_ids:
            raw_source = by_id.get(source_id)
            if raw_source is None:
                raise InvalidKnowledgeEntrySourceError(
                    f"Cited source {source_id} is missing from the assistant message"
                )
            try:
                validated = RagSource.model_validate(raw_source)
            except ValidationError as exc:
                raise InvalidKnowledgeEntrySourceError(
                    f"Cited source {source_id} is invalid"
                ) from exc
            if validated.knowledge_base_id != knowledge_base_id:
                raise InvalidKnowledgeEntrySourceError(
                    f"Cited source {source_id} belongs to another knowledge base"
                )
            source = validated.model_dump(mode="json")
            snapshots.append({field: source.get(field) for field in _SOURCE_SNAPSHOT_FIELDS})
        return snapshots

    @staticmethod
    def _generation_metadata_snapshot(message: ConversationMessage) -> dict[str, object] | None:
        if not message.generation_metadata:
            return None
        snapshot = {
            key: value
            for key, value in message.generation_metadata.items()
            if key in _GENERATION_METADATA_FIELDS
        }
        return snapshot or None

    async def create(
        self, knowledge_base_id: UUID, payload: KnowledgeEntryCreate
    ) -> KnowledgeEntry:
        try:
            await self._require_knowledge_base(knowledge_base_id)
            if (
                await self.repository.get_by_source_assistant(
                    knowledge_base_id, payload.source_assistant_message_id
                )
                is not None
            ):
                raise KnowledgeEntryAlreadyExistsError(payload.source_assistant_message_id)
            source = await self.repository.source_message(
                knowledge_base_id, payload.source_assistant_message_id
            )
            if source is None:
                raise KnowledgeEntrySourceNotFoundError()
            conversation, assistant = source
            if assistant.role != "assistant" or assistant.status != "completed":
                raise InvalidKnowledgeEntrySourceError(
                    "Knowledge entries require a completed assistant answer"
                )
            messages = await self.repository.conversation_messages(conversation.id)
            user_message = self._paired_user(assistant, messages)
            entry = KnowledgeEntry(
                knowledge_base_id=knowledge_base_id,
                question=payload.question,
                background=payload.background,
                root_cause=payload.root_cause,
                solution=payload.solution,
                failed_attempts=payload.failed_attempts,
                validation_status=payload.validation_status,
                tags=payload.tags,
                source_conversation_id=conversation.id,
                source_user_message_id=user_message.id,
                source_assistant_message_id=assistant.id,
                question_snapshot=user_message.content,
                answer_snapshot=assistant.content,
                sources_snapshot=self._sources_snapshot(knowledge_base_id, assistant),
                generation_metadata_snapshot=self._generation_metadata_snapshot(assistant),
            )
            await self.repository.create(entry)
            await self.session.commit()
            await self.session.refresh(entry)
        except IntegrityError as exc:
            await self.session.rollback()
            raise KnowledgeEntryAlreadyExistsError(payload.source_assistant_message_id) from exc
        except Exception:
            await self.session.rollback()
            raise
        return entry

    async def get(self, knowledge_base_id: UUID, entry_id: UUID) -> KnowledgeEntry:
        entry = await self.repository.get_scoped(knowledge_base_id, entry_id)
        if entry is None:
            raise KnowledgeEntryNotFoundError(entry_id)
        return entry

    async def list(
        self,
        knowledge_base_id: UUID,
        *,
        query: str | None,
        validation_status: str | None,
        tag: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[KnowledgeEntry], int, list[str]]:
        await self._require_knowledge_base(knowledge_base_id)
        normalized_tag = tag.strip().casefold() if tag and tag.strip() else None
        normalized_query = query.strip() if query and query.strip() else None
        items = await self.repository.list(
            knowledge_base_id,
            query=normalized_query,
            validation_status=validation_status,
            tag=normalized_tag,
            offset=offset,
            limit=limit,
        )
        total = await self.repository.count(
            knowledge_base_id,
            query=normalized_query,
            validation_status=validation_status,
            tag=normalized_tag,
        )
        return items, total, await self.repository.available_tags(knowledge_base_id)

    async def update(
        self, knowledge_base_id: UUID, entry_id: UUID, payload: KnowledgeEntryUpdate
    ) -> KnowledgeEntry:
        try:
            entry = await self.get(knowledge_base_id, entry_id)
            await self.repository.update(entry, payload.model_dump(exclude_unset=True))
            await self.session.commit()
            await self.session.refresh(entry)
        except Exception:
            await self.session.rollback()
            raise
        return entry

    async def delete(self, knowledge_base_id: UUID, entry_id: UUID) -> None:
        try:
            entry = await self.get(knowledge_base_id, entry_id)
            await self.repository.delete(entry)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
