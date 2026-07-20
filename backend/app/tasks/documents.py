import asyncio
from typing import Protocol, cast
from uuid import UUID

from app.core.config import get_settings
from app.db.session import Database
from app.services.document_parsing import DocumentParsingService
from app.storage.local import LocalFileStorage
from app.worker.celery_app import celery_app


class ParseDocumentTask(Protocol):
    def __call__(self, document_version_id: str, force: bool = False) -> bool: ...

    def delay(self, document_version_id: str, *, force: bool = False) -> object: ...


def _execute_parse_document_version(document_version_id: str, force: bool = False) -> bool:
    return asyncio.run(_parse_document_version(UUID(document_version_id), force=force))


parse_document_version = cast(
    ParseDocumentTask,
    celery_app.task(name="app.tasks.documents.parse_document_version")(
        _execute_parse_document_version
    ),
)


async def _parse_document_version(document_version_id: UUID, *, force: bool) -> bool:
    settings = get_settings()
    database = Database(settings)
    storage = LocalFileStorage(
        settings.document_storage_root,
        max_size=settings.document_max_file_size_bytes,
        chunk_size=settings.document_upload_chunk_size_bytes,
    )
    try:
        async with database.session_factory() as session:
            service = DocumentParsingService(session, storage, settings)
            return await service.parse_version(document_version_id, force=force)
    finally:
        await database.close()
