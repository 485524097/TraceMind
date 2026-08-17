import logging
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.consistency_audits import (
    AuditServiceDependency,
)
from app.db.session import get_db_session
from app.repositories.consistency_repair import ConsistencyRepairRepository
from app.schemas.consistency_repair import ConsistencyRepairRequest, ConsistencyRepairResponse
from app.services.consistency_repair import ConsistencyRepairService
from app.services.consistency_repair_dispatcher import ConsistencyRepairDispatcher
from app.services.exceptions import (
    ConsistencyAuditSelectionError,
    ConsistencyRepairAlreadyActiveError,
    ConsistencyRepairNotFoundError,
    ConsistencyRepairNotRetryableError,
    KnowledgeBaseNotFoundError,
)
from app.tasks.repair import repair_consistency_findings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["consistency repairs"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def get_repair_service(
    request: Request,
    session: SessionDependency,
    audit_service: AuditServiceDependency,
) -> ConsistencyRepairService:
    return ConsistencyRepairService(
        session,
        audit_service,
        ConsistencyRepairDispatcher(repair_consistency_findings),
        ConsistencyRepairRepository(session),
        stale_after_seconds=request.app.state.settings.consistency_repair_stale_after_seconds,
    )


RepairServiceDependency = Annotated[ConsistencyRepairService, Depends(get_repair_service)]


def _raise(exc: Exception) -> NoReturn:
    if isinstance(exc, KnowledgeBaseNotFoundError):
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if isinstance(exc, ConsistencyRepairNotFoundError):
        raise HTTPException(status_code=404, detail="Consistency repair not found")
    if isinstance(exc, ConsistencyRepairAlreadyActiveError):
        raise HTTPException(status_code=409, detail="Knowledge Base consistency repair is active")
    if isinstance(exc, ConsistencyRepairNotRetryableError):
        raise HTTPException(status_code=409, detail="Consistency repair is not retryable")
    if isinstance(exc, ConsistencyAuditSelectionError):
        raise HTTPException(
            status_code=409,
            detail="Audit finding selection is missing or outside this Knowledge Base",
        )
    if isinstance(exc, SQLAlchemyError):
        logger.exception("Consistency repair database operation failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Consistency repair could not be processed",
        )
    raise exc


@router.post(
    "/knowledge-bases/{knowledge_base_id}/consistency-repair",
    response_model=ConsistencyRepairResponse,
    summary="Plan or execute selected safe derived-state repairs",
)
async def start_consistency_repair(
    knowledge_base_id: UUID,
    body: ConsistencyRepairRequest,
    service: RepairServiceDependency,
) -> ConsistencyRepairResponse:
    if body.knowledge_base_id != knowledge_base_id:
        raise HTTPException(status_code=422, detail="knowledge_base_id does not match path")
    try:
        return await service.start(body)
    except (
        KnowledgeBaseNotFoundError,
        ConsistencyAuditSelectionError,
        ConsistencyRepairAlreadyActiveError,
        SQLAlchemyError,
    ) as exc:
        _raise(exc)


@router.get(
    "/knowledge-bases/{knowledge_base_id}/consistency-repair/{operation_id}",
    response_model=ConsistencyRepairResponse,
    summary="Get selected consistency repair status",
)
async def get_consistency_repair(
    knowledge_base_id: UUID,
    operation_id: UUID,
    service: RepairServiceDependency,
) -> ConsistencyRepairResponse:
    try:
        return await service.get_status(knowledge_base_id, operation_id)
    except (
        KnowledgeBaseNotFoundError,
        ConsistencyRepairNotFoundError,
        SQLAlchemyError,
    ) as exc:
        _raise(exc)


@router.post(
    "/knowledge-bases/{knowledge_base_id}/consistency-repair/{operation_id}/retry",
    response_model=ConsistencyRepairResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry stale or incomplete consistency repair work",
)
async def retry_consistency_repair(
    knowledge_base_id: UUID,
    operation_id: UUID,
    service: RepairServiceDependency,
) -> ConsistencyRepairResponse:
    try:
        return await service.retry(knowledge_base_id, operation_id)
    except (
        KnowledgeBaseNotFoundError,
        ConsistencyRepairNotFoundError,
        ConsistencyRepairAlreadyActiveError,
        ConsistencyRepairNotRetryableError,
        SQLAlchemyError,
    ) as exc:
        _raise(exc)
