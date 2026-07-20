import logging
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.document import DocumentVersion
from app.repositories.document import DocumentRecord
from app.schemas.document import (
    DocumentChunkListResponse,
    DocumentChunkResponse,
    DocumentImportResponse,
    DocumentListResponse,
    DocumentParseRequestResponse,
    DocumentParseStatusResponse,
    DocumentResponse,
    DocumentVersionResponse,
)
from app.services.document import DocumentService
from app.services.document_dispatcher import CeleryDocumentParsingDispatcher
from app.services.document_parsing import DocumentParsingService
from app.services.exceptions import (
    DocumentImportConflictError,
    DocumentNotFoundError,
    DocumentParsingQueueError,
    DocumentStorageError,
    DocumentTooLargeError,
    DocumentVersionNotFoundError,
    EmptyDocumentError,
    InvalidDocumentNameError,
    KnowledgeBaseNotFoundError,
    UnsupportedDocumentTypeError,
)
from app.storage.local import LocalFileStorage

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/knowledge-bases/{knowledge_base_id}/documents",
    tags=["documents"],
)
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def get_document_service(request: Request, session: SessionDependency) -> DocumentService:
    settings = request.app.state.settings
    storage = LocalFileStorage(
        settings.document_storage_root,
        max_size=settings.document_max_file_size_bytes,
        chunk_size=settings.document_upload_chunk_size_bytes,
    )
    return DocumentService(
        session,
        storage,
        set(settings.document_allowed_extensions),
        parsing_dispatcher=CeleryDocumentParsingDispatcher(),
    )


ServiceDependency = Annotated[DocumentService, Depends(get_document_service)]


def get_document_parsing_service(
    request: Request, session: SessionDependency
) -> DocumentParsingService:
    settings = request.app.state.settings
    storage = LocalFileStorage(
        settings.document_storage_root,
        max_size=settings.document_max_file_size_bytes,
        chunk_size=settings.document_upload_chunk_size_bytes,
    )
    return DocumentParsingService(
        session,
        storage,
        settings,
        dispatcher=CeleryDocumentParsingDispatcher(),
    )


ParsingServiceDependency = Annotated[DocumentParsingService, Depends(get_document_parsing_service)]


def document_response(record: DocumentRecord) -> DocumentResponse:
    document = record.document
    return DocumentResponse(
        id=document.id,
        knowledge_base_id=document.knowledge_base_id,
        name=document.name,
        source_type=document.source_type,
        created_at=document.created_at,
        updated_at=document.updated_at,
        version_count=record.version_count,
        latest_version=DocumentVersionResponse.model_validate(record.latest_version),
    )


def parse_status_response(version: DocumentVersion) -> DocumentParseStatusResponse:
    return DocumentParseStatusResponse(
        version_id=version.id,
        parse_status=version.parse_status,
        parser_name=version.parser_name,
        parser_version=version.parser_version,
        chunk_count=version.chunk_count,
        parse_started_at=version.parse_started_at,
        parsed_at=version.parsed_at,
        last_parse_attempt_at=version.last_parse_attempt_at,
        parse_error_code=version.parse_error_code,
        parse_error_message=version.parse_error_message,
    )


def raise_document_http_error(exc: Exception) -> NoReturn:
    if isinstance(
        exc, (KnowledgeBaseNotFoundError, DocumentNotFoundError, DocumentVersionNotFoundError)
    ):
        raise HTTPException(status_code=404, detail="Knowledge base or document not found")
    if isinstance(exc, DocumentImportConflictError):
        raise HTTPException(status_code=409, detail="Document import conflict; please retry")
    if isinstance(exc, DocumentTooLargeError):
        raise HTTPException(status_code=413, detail="Document exceeds the configured size limit")
    if isinstance(exc, UnsupportedDocumentTypeError):
        raise HTTPException(status_code=415, detail="Document type is not supported")
    if isinstance(exc, (InvalidDocumentNameError, EmptyDocumentError)):
        raise HTTPException(status_code=422, detail="Document name or content is invalid")
    if isinstance(exc, DocumentStorageError):
        logger.exception("Document storage operation failed")
        raise HTTPException(status_code=500, detail="Document storage operation failed")
    if isinstance(exc, DocumentParsingQueueError):
        raise HTTPException(status_code=503, detail="Document parsing queue is unavailable")
    if isinstance(exc, SQLAlchemyError):
        logger.exception("Document database operation failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Document operation could not be completed")
    raise exc


@router.post(
    "",
    response_model=DocumentImportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Import a document",
    response_description="The created, versioned, or unchanged document",
    responses={
        200: {"description": "The uploaded content is unchanged"},
        404: {"description": "Knowledge base not found"},
        409: {"description": "Concurrent import conflict"},
        413: {"description": "File too large"},
        415: {"description": "Unsupported file extension"},
        422: {"description": "Invalid filename or empty file"},
        500: {"description": "Storage or database operation failed"},
    },
)
async def import_document(
    knowledge_base_id: UUID,
    service: ServiceDependency,
    response: Response,
    file: Annotated[UploadFile, File(description="A supported document file")],
) -> DocumentImportResponse:
    try:
        result = await service.import_document(knowledge_base_id, file)
    except Exception as exc:
        raise_document_http_error(exc)
    if result.action.value == "unchanged":
        response.status_code = status.HTTP_200_OK
    return DocumentImportResponse(
        import_action=result.action,
        parsing_queued=result.parsing_queued,
        document=document_response(result.record),
    )


@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List documents",
    response_description="A stable paginated list with latest versions and version counts",
    responses={404: {"description": "Knowledge base not found"}},
)
async def list_documents(
    knowledge_base_id: UUID,
    service: ServiceDependency,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    query: Annotated[str | None, Query(max_length=255)] = None,
) -> DocumentListResponse:
    try:
        records, total = await service.list_documents(
            knowledge_base_id, offset=offset, limit=limit, query=query
        )
    except Exception as exc:
        raise_document_http_error(exc)
    return DocumentListResponse(
        items=[document_response(record) for record in records],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Get document details",
    response_description="Document metadata and its latest version",
    responses={404: {"description": "Document not found in this knowledge base"}},
)
async def get_document(
    knowledge_base_id: UUID, document_id: UUID, service: ServiceDependency
) -> DocumentResponse:
    try:
        record = await service.get_document(knowledge_base_id, document_id)
    except Exception as exc:
        raise_document_http_error(exc)
    return document_response(record)


@router.get(
    "/{document_id}/versions",
    response_model=list[DocumentVersionResponse],
    summary="List document versions",
    response_description="All versions ordered from newest to oldest",
    responses={404: {"description": "Document not found in this knowledge base"}},
)
async def list_document_versions(
    knowledge_base_id: UUID, document_id: UUID, service: ServiceDependency
) -> list[DocumentVersionResponse]:
    try:
        versions = await service.list_versions(knowledge_base_id, document_id)
    except Exception as exc:
        raise_document_http_error(exc)
    return [DocumentVersionResponse.model_validate(version) for version in versions]


@router.post(
    "/{document_id}/versions/{version_id}/parse",
    response_model=DocumentParseRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        404: {"description": "Version not found"},
        503: {"description": "Queue unavailable"},
    },
)
async def request_document_parse(
    knowledge_base_id: UUID,
    document_id: UUID,
    version_id: UUID,
    service: ParsingServiceDependency,
    response: Response,
    force: bool = Query(False),
) -> DocumentParseRequestResponse:
    try:
        result = await service.request_parse(
            knowledge_base_id, document_id, version_id, force=force
        )
    except Exception as exc:
        raise_document_http_error(exc)
    if not result.queued:
        response.status_code = status.HTTP_200_OK
    return DocumentParseRequestResponse(
        queued=result.queued,
        version=parse_status_response(result.version),
    )


@router.get(
    "/{document_id}/versions/{version_id}/parse-status",
    response_model=DocumentParseStatusResponse,
    responses={404: {"description": "Version not found"}},
)
async def get_document_parse_status(
    knowledge_base_id: UUID,
    document_id: UUID,
    version_id: UUID,
    service: ParsingServiceDependency,
) -> DocumentParseStatusResponse:
    try:
        version = await service.get_status(knowledge_base_id, document_id, version_id)
    except Exception as exc:
        raise_document_http_error(exc)
    return parse_status_response(version)


@router.get(
    "/{document_id}/versions/{version_id}/chunks",
    response_model=DocumentChunkListResponse,
    responses={404: {"description": "Version not found"}},
)
async def list_document_chunks(
    knowledge_base_id: UUID,
    document_id: UUID,
    version_id: UUID,
    service: ParsingServiceDependency,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DocumentChunkListResponse:
    try:
        page = await service.list_chunks(
            knowledge_base_id,
            document_id,
            version_id,
            offset=offset,
            limit=limit,
        )
    except Exception as exc:
        raise_document_http_error(exc)
    return DocumentChunkListResponse(
        items=[DocumentChunkResponse.model_validate(item) for item in page.items],
        total=page.total,
        offset=offset,
        limit=limit,
        version=parse_status_response(page.version),
    )


@router.get(
    "/{document_id}/download",
    response_class=FileResponse,
    summary="Download the current document version",
    response_description="The latest stored file",
    responses={
        404: {"description": "Document not found"},
        500: {"description": "Stored file unavailable"},
    },
)
async def download_current_version(
    knowledge_base_id: UUID, document_id: UUID, service: ServiceDependency
) -> FileResponse:
    try:
        download = await service.download_current(knowledge_base_id, document_id)
    except Exception as exc:
        raise_document_http_error(exc)
    return FileResponse(download.path, media_type=download.mime_type, filename=download.filename)


@router.get(
    "/{document_id}/versions/{version_id}/download",
    response_class=FileResponse,
    summary="Download a specific document version",
    response_description="The selected historical file",
    responses={
        404: {"description": "Document version not found"},
        500: {"description": "Stored file unavailable"},
    },
)
async def download_document_version(
    knowledge_base_id: UUID,
    document_id: UUID,
    version_id: UUID,
    service: ServiceDependency,
) -> FileResponse:
    try:
        download = await service.download_version(knowledge_base_id, document_id, version_id)
    except Exception as exc:
        raise_document_http_error(exc)
    return FileResponse(download.path, media_type=download.mime_type, filename=download.filename)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a document",
    response_description="Document metadata, versions, and local files deleted",
    responses={404: {"description": "Document not found"}, 500: {"description": "Delete failed"}},
)
async def delete_document(
    knowledge_base_id: UUID, document_id: UUID, service: ServiceDependency
) -> Response:
    try:
        await service.delete_document(knowledge_base_id, document_id)
    except Exception as exc:
        raise_document_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
