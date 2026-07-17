from uuid import UUID

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_base import KnowledgeBase
from app.repositories.knowledge_base import KnowledgeBaseRepository
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseUpdate
from app.services.exceptions import (
    KnowledgeBaseNameConflictError,
    KnowledgeBaseNotFoundError,
)


class KnowledgeBaseService:
    def __init__(
        self,
        session: AsyncSession,
        repository: KnowledgeBaseRepository | None = None,
    ) -> None:
        self.session = session
        self.repository = repository or KnowledgeBaseRepository(session)

    async def create(self, payload: KnowledgeBaseCreate) -> KnowledgeBase:
        if await self.repository.get_by_name(payload.name) is not None:
            raise KnowledgeBaseNameConflictError(payload.name)

        knowledge_base = KnowledgeBase(name=payload.name, description=payload.description)
        try:
            await self.repository.create(knowledge_base)
            await self.session.commit()
            await self.session.refresh(knowledge_base)
        except IntegrityError as exc:
            await self.session.rollback()
            raise KnowledgeBaseNameConflictError(payload.name) from exc
        except SQLAlchemyError:
            await self.session.rollback()
            raise
        return knowledge_base

    async def get(self, knowledge_base_id: UUID) -> KnowledgeBase:
        knowledge_base = await self.repository.get_by_id(knowledge_base_id)
        if knowledge_base is None:
            raise KnowledgeBaseNotFoundError(knowledge_base_id)
        return knowledge_base

    async def list(
        self,
        *,
        offset: int,
        limit: int,
    ) -> tuple[list[KnowledgeBase], int]:
        items = await self.repository.list(offset=offset, limit=limit)
        total = await self.repository.count()
        return items, total

    async def update(
        self,
        knowledge_base_id: UUID,
        payload: KnowledgeBaseUpdate,
    ) -> KnowledgeBase:
        knowledge_base = await self.get(knowledge_base_id)
        changes = payload.model_dump(exclude_unset=True)
        new_name = changes.get("name")
        if isinstance(new_name, str) and new_name != knowledge_base.name:
            existing = await self.repository.get_by_name(new_name)
            if existing is not None and existing.id != knowledge_base.id:
                raise KnowledgeBaseNameConflictError(new_name)

        try:
            await self.repository.update(knowledge_base, changes)
            await self.session.commit()
            await self.session.refresh(knowledge_base)
        except IntegrityError as exc:
            await self.session.rollback()
            conflict_name = new_name if isinstance(new_name, str) else knowledge_base.name
            raise KnowledgeBaseNameConflictError(conflict_name) from exc
        except SQLAlchemyError:
            await self.session.rollback()
            raise
        return knowledge_base

    async def delete(self, knowledge_base_id: UUID) -> None:
        knowledge_base = await self.get(knowledge_base_id)
        try:
            await self.repository.delete(knowledge_base)
            await self.session.commit()
        except SQLAlchemyError:
            await self.session.rollback()
            raise
