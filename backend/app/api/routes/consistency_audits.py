import logging
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.indexing.factory import build_qdrant_gateway
from app.repositories.consistency_audit import ConsistencyAuditRepository
from app.schemas.consistency_audit import ConsistencyAuditResponse
from app.services.consistency_audit import ConsistencyAuditService
from app.services.exceptions import KnowledgeBaseNotFoundError
from app.storage.archive import LocalArchiveStorage, archive_limits_from_settings
from app.storage.local import LocalFileStorage

logger = logging.getLogger(__name__)
router = APIRouter(tags=["consistency audits"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def get_consistency_audit_service(
    request: Request, session: SessionDependency
) -> ConsistencyAuditService:
    settings = request.app.state.settings
    document_storage = LocalFileStorage(
        settings.document_storage_root,
        max_size=settings.document_max_file_size_bytes,
        chunk_size=settings.document_upload_chunk_size_bytes,
        create_roots=False,
    )
    archive_storage = LocalArchiveStorage(
        settings.document_storage_root,
        archive_limits_from_settings(settings),
        create_roots=False,
    )
    gateway = build_qdrant_gateway(settings, request.app.state.qdrant_client.client)
    return ConsistencyAuditService(
        settings,
        ConsistencyAuditRepository(session),
        document_storage,
        archive_storage,
        gateway,
    )


AuditServiceDependency = Annotated[
    ConsistencyAuditService,
    Depends(get_consistency_audit_service),
]


def raise_audit_http_error(exc: Exception) -> NoReturn:
    if isinstance(exc, KnowledgeBaseNotFoundError):
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if isinstance(exc, SQLAlchemyError):
        logger.exception("Consistency audit database operation failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Consistency audit could not be completed",
        )
    raise exc


@router.post(
    "/knowledge-bases/{knowledge_base_id}/consistency-audit",
    response_model=ConsistencyAuditResponse,
    summary="Run a read-only Knowledge Base consistency audit",
)
async def audit_knowledge_base(
    knowledge_base_id: UUID,
    service: AuditServiceDependency,
) -> ConsistencyAuditResponse:
    try:
        return await service.audit_knowledge_base(knowledge_base_id)
    except (KnowledgeBaseNotFoundError, SQLAlchemyError) as exc:
        raise_audit_http_error(exc)


@router.post(
    "/consistency-audit",
    response_model=ConsistencyAuditResponse,
    summary="Run a read-only consistency audit across all Knowledge Bases",
)
async def audit_all_knowledge_bases(
    service: AuditServiceDependency,
) -> ConsistencyAuditResponse:
    try:
        return await service.audit_all()
    except SQLAlchemyError as exc:
        raise_audit_http_error(exc)
