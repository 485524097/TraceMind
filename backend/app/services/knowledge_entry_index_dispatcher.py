import asyncio
from typing import Protocol
from uuid import UUID

from app.services.exceptions import KnowledgeEntryIndexingQueueError


class KnowledgeEntryIndexingDispatcher(Protocol):
    async def enqueue_sync(self, entry_id: UUID, *, force: bool = False) -> None: ...

    async def enqueue_delete(self, entry_id: UUID) -> None: ...


class CeleryKnowledgeEntryIndexingDispatcher:
    async def enqueue_sync(self, entry_id: UUID, *, force: bool = False) -> None:
        from app.tasks.knowledge_entries import sync_knowledge_entry_index

        try:
            await asyncio.to_thread(sync_knowledge_entry_index.delay, str(entry_id), force=force)
        except Exception as exc:
            raise KnowledgeEntryIndexingQueueError(
                "Knowledge entry indexing queue is unavailable"
            ) from exc

    async def enqueue_delete(self, entry_id: UUID) -> None:
        from app.tasks.knowledge_entries import delete_knowledge_entry_index

        try:
            await asyncio.to_thread(delete_knowledge_entry_index.delay, str(entry_id))
        except Exception as exc:
            raise KnowledgeEntryIndexingQueueError(
                "Knowledge entry indexing queue is unavailable"
            ) from exc
