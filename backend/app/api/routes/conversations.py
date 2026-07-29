import logging
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.conversation import (
    ConversationCreate,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationMessageResponse,
    ConversationResponse,
    ConversationUpdate,
)
from app.services.conversation import ConversationService
from app.services.exceptions import ConversationNotFoundError, KnowledgeBaseNotFoundError

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/knowledge-bases/{knowledge_base_id}/conversations",
    tags=["conversations"],
)
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def get_conversation_service(session: SessionDependency) -> ConversationService:
    return ConversationService(session)


ConversationServiceDependency = Annotated[ConversationService, Depends(get_conversation_service)]


def raise_http_error(exc: Exception) -> NoReturn:
    if isinstance(exc, KnowledgeBaseNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found"
        )
    if isinstance(exc, ConversationNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    if isinstance(exc, SQLAlchemyError):
        logger.exception("Conversation database operation failed (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The conversation operation could not be completed",
        )
    raise exc


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    knowledge_base_id: UUID,
    payload: ConversationCreate,
    service: ConversationServiceDependency,
) -> ConversationResponse:
    try:
        conversation = await service.create(knowledge_base_id, payload)
    except (KnowledgeBaseNotFoundError, SQLAlchemyError) as exc:
        raise_http_error(exc)
    return ConversationResponse.model_validate(conversation)


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    knowledge_base_id: UUID,
    service: ConversationServiceDependency,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> ConversationListResponse:
    try:
        items, total = await service.list(knowledge_base_id, offset=offset, limit=limit)
    except (KnowledgeBaseNotFoundError, SQLAlchemyError) as exc:
        raise_http_error(exc)
    return ConversationListResponse(
        items=[ConversationResponse.model_validate(item) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    knowledge_base_id: UUID,
    conversation_id: UUID,
    service: ConversationServiceDependency,
) -> ConversationDetailResponse:
    try:
        conversation, messages = await service.get_detail(knowledge_base_id, conversation_id)
    except (ConversationNotFoundError, SQLAlchemyError) as exc:
        raise_http_error(exc)
    return ConversationDetailResponse(
        **ConversationResponse.model_validate(conversation).model_dump(),
        messages=[ConversationMessageResponse.model_validate(item) for item in messages],
    )


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    knowledge_base_id: UUID,
    conversation_id: UUID,
    payload: ConversationUpdate,
    service: ConversationServiceDependency,
) -> ConversationResponse:
    try:
        conversation = await service.update(knowledge_base_id, conversation_id, payload)
    except (ConversationNotFoundError, SQLAlchemyError) as exc:
        raise_http_error(exc)
    return ConversationResponse.model_validate(conversation)


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_conversation(
    knowledge_base_id: UUID,
    conversation_id: UUID,
    service: ConversationServiceDependency,
) -> Response:
    try:
        await service.delete(knowledge_base_id, conversation_id)
    except (ConversationNotFoundError, SQLAlchemyError) as exc:
        raise_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
