import asyncio
from typing import Protocol
from uuid import UUID

from app.services.exceptions import DocumentIndexingQueueError


class DocumentIndexingDispatcher(Protocol):
    async def enqueue(self, document_version_id: UUID, *, force: bool = False) -> None: ...


class CeleryDocumentIndexingDispatcher:
    async def enqueue(self, document_version_id: UUID, *, force: bool = False) -> None:
        from app.tasks.indexing import index_document_version

        try:
            await asyncio.to_thread(
                index_document_version.delay,
                str(document_version_id),
                force=force,
            )
        except Exception as exc:
            raise DocumentIndexingQueueError("Document indexing queue is unavailable") from exc
