from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_base import KnowledgeBase


class KnowledgeBaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, knowledge_base: KnowledgeBase) -> KnowledgeBase:
        self.session.add(knowledge_base)
        await self.session.flush()
        return knowledge_base

    async def get_by_id(self, knowledge_base_id: UUID) -> KnowledgeBase | None:
        return await self.session.get(KnowledgeBase, knowledge_base_id)

    async def get_by_name(self, name: str) -> KnowledgeBase | None:
        result = await self.session.execute(select(KnowledgeBase).where(KnowledgeBase.name == name))
        return result.scalar_one_or_none()

    async def list(self, *, offset: int, limit: int) -> list[KnowledgeBase]:
        result = await self.session.execute(
            select(KnowledgeBase)
            .order_by(KnowledgeBase.created_at.desc(), KnowledgeBase.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count(self) -> int:
        result = await self.session.execute(select(func.count()).select_from(KnowledgeBase))
        return int(result.scalar_one())

    async def update(
        self,
        knowledge_base: KnowledgeBase,
        changes: dict[str, str | None],
    ) -> KnowledgeBase:
        for field, value in changes.items():
            setattr(knowledge_base, field, value)
        await self.session.flush()
        return knowledge_base

    async def delete(self, knowledge_base: KnowledgeBase) -> None:
        await self.session.delete(knowledge_base)
        await self.session.flush()
