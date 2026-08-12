import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.knowledge_map import KnowledgeMapResponse
from app.services.exceptions import KnowledgeBaseNotFoundError
from app.services.knowledge_map import KnowledgeMapService

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/knowledge-bases/{knowledge_base_id}/knowledge-map",
    tags=["knowledge map"],
)
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def get_knowledge_map_service(session: SessionDependency) -> KnowledgeMapService:
    return KnowledgeMapService(session)


ServiceDependency = Annotated[KnowledgeMapService, Depends(get_knowledge_map_service)]


@router.get("", response_model=KnowledgeMapResponse)
async def get_knowledge_map(
    knowledge_base_id: UUID, service: ServiceDependency
) -> KnowledgeMapResponse:
    try:
        return await service.get(knowledge_base_id)
    except KnowledgeBaseNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found"
        ) from exc
    except SQLAlchemyError as exc:
        logger.exception("Knowledge map database operation failed (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Knowledge map could not be generated",
        ) from exc
