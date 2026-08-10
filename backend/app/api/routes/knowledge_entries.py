import logging
from typing import Annotated, Literal, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.knowledge_entry import (
    KnowledgeEntryCreate,
    KnowledgeEntryListResponse,
    KnowledgeEntryResponse,
    KnowledgeEntryUpdate,
)
from app.services.exceptions import (
    InvalidKnowledgeEntrySourceError,
    KnowledgeBaseNotFoundError,
    KnowledgeEntryAlreadyExistsError,
    KnowledgeEntryNotFoundError,
    KnowledgeEntrySourceNotFoundError,
)
from app.services.knowledge_entry import KnowledgeEntryService

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/knowledge-bases/{knowledge_base_id}/knowledge-entries",
    tags=["knowledge entries"],
)
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def get_knowledge_entry_service(session: SessionDependency) -> KnowledgeEntryService:
    return KnowledgeEntryService(session)


ServiceDependency = Annotated[KnowledgeEntryService, Depends(get_knowledge_entry_service)]


def raise_http_error(exc: Exception) -> NoReturn:
    if isinstance(
        exc,
        (
            KnowledgeBaseNotFoundError,
            KnowledgeEntryNotFoundError,
            KnowledgeEntrySourceNotFoundError,
        ),
    ):
        raise HTTPException(status_code=404, detail="Knowledge entry source or entry not found")
    if isinstance(exc, KnowledgeEntryAlreadyExistsError):
        raise HTTPException(status_code=409, detail="This answer is already saved as knowledge")
    if isinstance(exc, InvalidKnowledgeEntrySourceError):
        raise HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, SQLAlchemyError):
        logger.exception("Knowledge entry database operation failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Knowledge entry operation failed")
    raise exc


@router.post("", response_model=KnowledgeEntryResponse, status_code=status.HTTP_201_CREATED)
async def create_knowledge_entry(
    knowledge_base_id: UUID,
    payload: KnowledgeEntryCreate,
    service: ServiceDependency,
) -> KnowledgeEntryResponse:
    try:
        entry = await service.create(knowledge_base_id, payload)
    except (
        KnowledgeBaseNotFoundError,
        KnowledgeEntrySourceNotFoundError,
        KnowledgeEntryAlreadyExistsError,
        InvalidKnowledgeEntrySourceError,
        SQLAlchemyError,
    ) as exc:
        raise_http_error(exc)
    return KnowledgeEntryResponse.model_validate(entry)


@router.get("", response_model=KnowledgeEntryListResponse)
async def list_knowledge_entries(
    knowledge_base_id: UUID,
    service: ServiceDependency,
    query: Annotated[str | None, Query(max_length=200)] = None,
    validation_status: Annotated[
        Literal["unverified", "verified", "outdated"] | None, Query()
    ] = None,
    tag: Annotated[str | None, Query(max_length=50)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> KnowledgeEntryListResponse:
    try:
        items, total, tags = await service.list(
            knowledge_base_id,
            query=query,
            validation_status=validation_status,
            tag=tag,
            offset=offset,
            limit=limit,
        )
    except (KnowledgeBaseNotFoundError, SQLAlchemyError) as exc:
        raise_http_error(exc)
    return KnowledgeEntryListResponse(
        items=[KnowledgeEntryResponse.model_validate(item) for item in items],
        total=total,
        offset=offset,
        limit=limit,
        available_tags=tags,
    )


@router.get("/{entry_id}", response_model=KnowledgeEntryResponse)
async def get_knowledge_entry(
    knowledge_base_id: UUID, entry_id: UUID, service: ServiceDependency
) -> KnowledgeEntryResponse:
    try:
        entry = await service.get(knowledge_base_id, entry_id)
    except (KnowledgeEntryNotFoundError, SQLAlchemyError) as exc:
        raise_http_error(exc)
    return KnowledgeEntryResponse.model_validate(entry)


@router.patch("/{entry_id}", response_model=KnowledgeEntryResponse)
async def update_knowledge_entry(
    knowledge_base_id: UUID,
    entry_id: UUID,
    payload: KnowledgeEntryUpdate,
    service: ServiceDependency,
) -> KnowledgeEntryResponse:
    try:
        entry = await service.update(knowledge_base_id, entry_id, payload)
    except (KnowledgeEntryNotFoundError, SQLAlchemyError) as exc:
        raise_http_error(exc)
    return KnowledgeEntryResponse.model_validate(entry)


@router.delete("/{entry_id}", status_code=204, response_class=Response)
async def delete_knowledge_entry(
    knowledge_base_id: UUID, entry_id: UUID, service: ServiceDependency
) -> Response:
    try:
        await service.delete(knowledge_base_id, entry_id)
    except (KnowledgeEntryNotFoundError, SQLAlchemyError) as exc:
        raise_http_error(exc)
    return Response(status_code=204)
