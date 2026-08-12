import asyncio
from typing import Protocol
from uuid import UUID

from app.services.exceptions import DocumentParsingQueueError


class DocumentParsingDispatcher(Protocol):
    async def enqueue(self, document_version_id: UUID, *, force: bool = False) -> None: ...


class CeleryDocumentParsingDispatcher:
    async def enqueue(self, document_version_id: UUID, *, force: bool = False) -> None:
        from app.tasks.documents import parse_document_version

        try:
            await asyncio.to_thread(
                parse_document_version.delay,
                str(document_version_id),
                force=force,
            )
        except Exception as exc:
            raise DocumentParsingQueueError("Document parsing queue is unavailable") from exc
