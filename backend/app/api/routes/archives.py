import logging
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.db.session import get_db_session
from app.repositories.knowledge_base_restore_lock import RestoreAdvisoryLock
from app.schemas.knowledge_base_archive import KnowledgeBaseArchiveRestoreResponse
from app.services.exceptions import (
    ArchiveConflictError,
    ArchiveLimitExceededError,
    ArchiveSourceIntegrityError,
    ArchiveStorageError,
    ArchiveValidationError,
    KnowledgeBaseNotFoundError,
)
from app.services.knowledge_base_archive import KnowledgeBaseArchiveService
from app.services.knowledge_base_restore import KnowledgeBaseRestoreService
from app.storage.archive import LocalArchiveStorage, archive_limits_from_settings
from app.storage.local import LocalFileStorage

logger = logging.getLogger(__name__)
router = APIRouter(tags=["knowledge base archives"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def get_knowledge_base_archive_service(
    request: Request, session: SessionDependency
) -> KnowledgeBaseArchiveService:
    settings = request.app.state.settings
    document_storage = LocalFileStorage(
        settings.document_storage_root,
        max_size=settings.document_max_file_size_bytes,
        chunk_size=settings.document_upload_chunk_size_bytes,
    )
    archive_storage = LocalArchiveStorage(
        settings.document_storage_root,
        archive_limits_from_settings(settings),
    )
    return KnowledgeBaseArchiveService(
        session,
        document_storage,
        archive_storage,
        settings.app_version,
    )


ArchiveServiceDependency = Annotated[
    KnowledgeBaseArchiveService,
    Depends(get_knowledge_base_archive_service),
]


def get_knowledge_base_restore_service(
    request: Request, session: SessionDependency
) -> KnowledgeBaseRestoreService:
    settings = request.app.state.settings
    document_storage = LocalFileStorage(
        settings.document_storage_root,
        max_size=settings.document_max_file_size_bytes,
        chunk_size=settings.document_upload_chunk_size_bytes,
    )
    archive_storage = LocalArchiveStorage(
        settings.document_storage_root,
        archive_limits_from_settings(settings),
    )
    return KnowledgeBaseRestoreService(
        session,
        document_storage,
        archive_storage,
        set(settings.document_allowed_extensions),
        restore_lock=RestoreAdvisoryLock(request.app.state.database.engine),
    )


RestoreServiceDependency = Annotated[
    KnowledgeBaseRestoreService,
    Depends(get_knowledge_base_restore_service),
]


def raise_archive_http_error(exc: Exception) -> NoReturn:
    if isinstance(exc, KnowledgeBaseNotFoundError):
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if isinstance(exc, ArchiveLimitExceededError):
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Knowledge base exceeds the configured archive limits",
        )
    if isinstance(exc, ArchiveConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Archive conflicts with existing data",
        )
    if isinstance(exc, ArchiveValidationError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Knowledge Base archive is invalid",
        )
    if isinstance(exc, ArchiveSourceIntegrityError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stored document changed while the archive was being created; please retry",
        )
    if isinstance(exc, ArchiveStorageError):
        logger.exception("Knowledge base archive storage operation failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Knowledge base archive could not be created",
        )
    if isinstance(exc, SQLAlchemyError):
        logger.exception(
            "Knowledge base archive database operation failed (%s)", type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Knowledge base archive could not be created",
        )
    raise exc


@router.get(
    "/knowledge-bases/{knowledge_base_id}/archive",
    response_class=FileResponse,
    summary="Export a Knowledge Base archive",
    response_description="A complete source-of-truth TraceMind archive",
    responses={
        404: {"description": "Knowledge base not found"},
        409: {"description": "A source file changed during export"},
        413: {"description": "Archive safety limit exceeded"},
        500: {"description": "Archive could not be created"},
    },
)
async def export_knowledge_base_archive(
    knowledge_base_id: UUID,
    service: ArchiveServiceDependency,
) -> FileResponse:
    try:
        exported = await service.export(knowledge_base_id)
    except Exception as exc:
        raise_archive_http_error(exc)
    return FileResponse(
        exported.path,
        media_type="application/zip",
        filename=exported.filename,
        background=BackgroundTask(service.discard_export, exported.path),
    )


@router.post(
    "/knowledge-base-archives/restore",
    response_model=KnowledgeBaseArchiveRestoreResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Restore a Knowledge Base archive",
    response_description="The restored source-of-truth data; rebuild has not started",
    responses={
        409: {"description": "Archive conflicts with existing data"},
        413: {"description": "Archive safety limit exceeded"},
        422: {"description": "Archive validation failed"},
        500: {"description": "Restore could not be completed"},
    },
)
async def restore_knowledge_base_archive(
    service: RestoreServiceDependency,
    file: Annotated[UploadFile, File(description="A TraceMind .tracemind.zip archive")],
) -> KnowledgeBaseArchiveRestoreResponse:
    try:
        return await service.restore(file)
    except Exception as exc:
        raise_archive_http_error(exc)
