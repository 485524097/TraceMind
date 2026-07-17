import logging
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.knowledge_base import (
    KnowledgeBaseCreate,
    KnowledgeBaseListResponse,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdate,
)
from app.services.exceptions import (
    KnowledgeBaseNameConflictError,
    KnowledgeBaseNotFoundError,
)
from app.services.knowledge_base import KnowledgeBaseService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/knowledge-bases", tags=["knowledge bases"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def get_knowledge_base_service(session: SessionDependency) -> KnowledgeBaseService:
    return KnowledgeBaseService(session)


ServiceDependency = Annotated[KnowledgeBaseService, Depends(get_knowledge_base_service)]


def raise_http_error(exc: Exception) -> NoReturn:
    if isinstance(exc, KnowledgeBaseNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )
    if isinstance(exc, KnowledgeBaseNameConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A knowledge base with this name already exists",
        )
    if isinstance(exc, SQLAlchemyError):
        logger.exception("Knowledge base database operation failed (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The knowledge base operation could not be completed",
        )
    raise exc


@router.post(
    "",
    response_model=KnowledgeBaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a knowledge base",
    response_description="The newly created knowledge base",
    responses={409: {"description": "A knowledge base with this name already exists"}},
)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    service: ServiceDependency,
) -> KnowledgeBaseResponse:
    try:
        knowledge_base = await service.create(payload)
    except (KnowledgeBaseNameConflictError, SQLAlchemyError) as exc:
        raise_http_error(exc)
    return KnowledgeBaseResponse.model_validate(knowledge_base)


@router.get(
    "",
    response_model=KnowledgeBaseListResponse,
    summary="List knowledge bases",
    response_description="A stable, paginated knowledge base list",
)
async def list_knowledge_bases(
    service: ServiceDependency,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> KnowledgeBaseListResponse:
    try:
        items, total = await service.list(offset=offset, limit=limit)
    except SQLAlchemyError as exc:
        raise_http_error(exc)
    return KnowledgeBaseListResponse(
        items=[KnowledgeBaseResponse.model_validate(item) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/{knowledge_base_id}",
    response_model=KnowledgeBaseResponse,
    summary="Get a knowledge base",
    response_description="The requested knowledge base",
    responses={404: {"description": "Knowledge base not found"}},
)
async def get_knowledge_base(
    knowledge_base_id: UUID,
    service: ServiceDependency,
) -> KnowledgeBaseResponse:
    try:
        knowledge_base = await service.get(knowledge_base_id)
    except (KnowledgeBaseNotFoundError, SQLAlchemyError) as exc:
        raise_http_error(exc)
    return KnowledgeBaseResponse.model_validate(knowledge_base)


@router.patch(
    "/{knowledge_base_id}",
    response_model=KnowledgeBaseResponse,
    summary="Update a knowledge base",
    response_description="The updated knowledge base",
    responses={
        404: {"description": "Knowledge base not found"},
        409: {"description": "A knowledge base with this name already exists"},
    },
)
async def update_knowledge_base(
    knowledge_base_id: UUID,
    payload: KnowledgeBaseUpdate,
    service: ServiceDependency,
) -> KnowledgeBaseResponse:
    try:
        knowledge_base = await service.update(knowledge_base_id, payload)
    except (KnowledgeBaseNotFoundError, KnowledgeBaseNameConflictError, SQLAlchemyError) as exc:
        raise_http_error(exc)
    return KnowledgeBaseResponse.model_validate(knowledge_base)


@router.delete(
    "/{knowledge_base_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a knowledge base",
    response_description="Knowledge base deleted successfully",
    responses={404: {"description": "Knowledge base not found"}},
)
async def delete_knowledge_base(
    knowledge_base_id: UUID,
    service: ServiceDependency,
) -> Response:
    try:
        await service.delete(knowledge_base_id)
    except (KnowledgeBaseNotFoundError, SQLAlchemyError) as exc:
        raise_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
