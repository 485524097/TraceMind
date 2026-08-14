import logging
import re
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import ConversationMessage
from app.models.knowledge_entry import KnowledgeEntry
from app.repositories.knowledge_base import KnowledgeBaseRepository
from app.repositories.knowledge_entry import KnowledgeEntryRepository
from app.repositories.knowledge_entry_indexing import KnowledgeEntryIndexingRepository
from app.schemas.knowledge_entry import KnowledgeEntryCreate, KnowledgeEntryUpdate
from app.schemas.rag import RagSource
from app.services.exceptions import (
    InvalidKnowledgeEntrySourceError,
    KnowledgeBaseNotFoundError,
    KnowledgeEntryAlreadyExistsError,
    KnowledgeEntryIndexingQueueError,
    KnowledgeEntryNotFoundError,
    KnowledgeEntryNotReadyForIndexError,
    KnowledgeEntrySourceNotFoundError,
)
from app.services.knowledge_entry_index_dispatcher import KnowledgeEntryIndexingDispatcher

logger = logging.getLogger(__name__)

_CITATION_PATTERN = re.compile(r"\[S\d+\]")
_SOURCE_SNAPSHOT_FIELDS = (
    "source_id",
    "source_type",
    "knowledge_base_id",
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
    "knowledge_entry_id",
    "knowledge_question",
    "knowledge_updated_at",
)
_GENERATION_METADATA_FIELDS = {
    "finish_reason",
    "grounded",
    "valid_citation_count",
    "invalid_citation_count",
    "source_count",
    "route_mode",
    "routing_latency_ms",
    "retrieval_mode",
    "retrieval_latency_ms",
    "query_rewrite_mode",
    "query_rewrite_latency_ms",
    "embedding_latency_ms",
    "qdrant_latency_ms",
    "fusion_latency_ms",
    "dense_candidate_count",
    "sparse_candidate_count",
    "rerank_latency_ms",
    "reranker_fallback",
    "llm_first_token_latency_ms",
    "llm_latency_ms",
    "total_latency_ms",
    "history_turn_count",
    "path_scope_mode",
    "scoped_relative_path",
}


class KnowledgeEntryService:
    def __init__(
        self,
        session: AsyncSession,
        repository: KnowledgeEntryRepository | None = None,
        knowledge_bases: KnowledgeBaseRepository | None = None,
        indexing_repository: KnowledgeEntryIndexingRepository | None = None,
        dispatcher: KnowledgeEntryIndexingDispatcher | None = None,
    ) -> None:
        self.session = session
        self.repository = repository or KnowledgeEntryRepository(session)
        self.knowledge_bases = knowledge_bases or KnowledgeBaseRepository(session)
        self.indexing_repository = indexing_repository or KnowledgeEntryIndexingRepository(session)
        self.dispatcher = dispatcher

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
                index_status=(
                    "pending" if payload.validation_status == "verified" else "not_indexed"
                ),
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
        if entry.index_status == "pending":
            await self._enqueue_sync(entry, force=False, raise_queue_error=False)
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
            await self._mark_sync_required(entry)
            await self.session.commit()
            await self.session.refresh(entry)
        except Exception:
            await self.session.rollback()
            raise
        if entry.index_status == "pending":
            await self._enqueue_sync(entry, force=False, raise_queue_error=False)
        return entry

    async def delete(self, knowledge_base_id: UUID, entry_id: UUID) -> None:
        try:
            entry = await self.get(knowledge_base_id, entry_id)
            await self.repository.delete(entry)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        if self.dispatcher is not None:
            try:
                await self.dispatcher.enqueue_delete(entry_id)
            except KnowledgeEntryIndexingQueueError:
                logger.warning(
                    "Deleted knowledge entry %s has Qdrant points requiring later cleanup",
                    entry_id,
                )

    async def request_index(
        self, knowledge_base_id: UUID, entry_id: UUID, *, force: bool = False
    ) -> KnowledgeEntry:
        entry = await self.get(knowledge_base_id, entry_id)
        if entry.validation_status != "verified":
            raise KnowledgeEntryNotReadyForIndexError(
                "Only verified knowledge entries can be indexed"
            )
        await self.indexing_repository.mark_pending(entry)
        await self.session.commit()
        await self.session.refresh(entry)
        await self._enqueue_sync(entry, force=force, raise_queue_error=True)
        return entry

    async def _mark_sync_required(self, entry: KnowledgeEntry) -> None:
        if entry.validation_status == "verified":
            await self.indexing_repository.mark_pending(entry)
            return
        if entry.active_index_generation is not None or entry.index_attempt_generation is not None:
            await self.indexing_repository.mark_pending(entry)
            return
        await self.indexing_repository.mark_not_indexed(entry)

    async def _enqueue_sync(
        self,
        entry: KnowledgeEntry,
        *,
        force: bool,
        raise_queue_error: bool,
    ) -> None:
        if self.dispatcher is None:
            return
        try:
            await self.dispatcher.enqueue_sync(entry.id, force=force)
        except KnowledgeEntryIndexingQueueError:
            await self.indexing_repository.mark_queue_failed(entry)
            await self.session.commit()
            await self.session.refresh(entry)
            if raise_queue_error:
                raise
            logger.warning("Knowledge entry %s could not be queued for indexing", entry.id)
