import logging
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.knowledge_base_rebuild import KnowledgeBaseRebuildResponse
from app.services.exceptions import (
    KnowledgeBaseNotFoundError,
    KnowledgeBaseRebuildAlreadyActiveError,
    KnowledgeBaseRebuildNotFoundError,
    KnowledgeBaseRebuildNotRetryableError,
)
from app.services.knowledge_base_rebuild import KnowledgeBaseRebuildService
from app.services.knowledge_base_rebuild_dispatcher import (
    CeleryKnowledgeBaseRebuildDispatcher,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/knowledge-bases/{knowledge_base_id}/rebuild", tags=["rebuild"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def get_rebuild_service(
    request: Request, session: SessionDependency
) -> KnowledgeBaseRebuildService:
    return KnowledgeBaseRebuildService(
        session,
        request.app.state.settings,
        CeleryKnowledgeBaseRebuildDispatcher(),
    )


ServiceDependency = Annotated[KnowledgeBaseRebuildService, Depends(get_rebuild_service)]


def raise_rebuild_http_error(exc: Exception) -> NoReturn:
    if isinstance(exc, KnowledgeBaseNotFoundError):
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if isinstance(exc, KnowledgeBaseRebuildNotFoundError):
        raise HTTPException(status_code=404, detail="Rebuild operation not found")
    if isinstance(exc, KnowledgeBaseRebuildAlreadyActiveError):
        raise HTTPException(status_code=409, detail="Knowledge base rebuild is already active")
    if isinstance(exc, KnowledgeBaseRebuildNotRetryableError):
        raise HTTPException(status_code=409, detail="Knowledge base rebuild is not retryable")
    if isinstance(exc, SQLAlchemyError):
        logger.exception("Knowledge Base rebuild database operation failed")
        raise HTTPException(status_code=500, detail="Rebuild operation could not be completed")
    raise exc


@router.post(
    "",
    response_model=KnowledgeBaseRebuildResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start rebuilding Knowledge Base derived state",
)
async def start_rebuild(
    knowledge_base_id: UUID,
    service: ServiceDependency,
) -> KnowledgeBaseRebuildResponse:
    try:
        return await service.start(knowledge_base_id)
    except (
        KnowledgeBaseNotFoundError,
        KnowledgeBaseRebuildAlreadyActiveError,
        SQLAlchemyError,
    ) as exc:
        raise_rebuild_http_error(exc)


@router.get(
    "",
    response_model=KnowledgeBaseRebuildResponse,
    summary="Get the latest Knowledge Base rebuild status",
)
async def get_rebuild_status(
    knowledge_base_id: UUID,
    service: ServiceDependency,
) -> KnowledgeBaseRebuildResponse:
    try:
        return await service.get_status(knowledge_base_id)
    except (KnowledgeBaseNotFoundError, SQLAlchemyError) as exc:
        raise_rebuild_http_error(exc)


@router.post(
    "/retry",
    response_model=KnowledgeBaseRebuildResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry incomplete Knowledge Base rebuild work",
)
async def retry_rebuild(
    knowledge_base_id: UUID,
    service: ServiceDependency,
) -> KnowledgeBaseRebuildResponse:
    try:
        return await service.retry(knowledge_base_id)
    except (
        KnowledgeBaseNotFoundError,
        KnowledgeBaseRebuildNotFoundError,
        KnowledgeBaseRebuildAlreadyActiveError,
        KnowledgeBaseRebuildNotRetryableError,
        SQLAlchemyError,
    ) as exc:
        raise_rebuild_http_error(exc)
