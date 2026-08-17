import asyncio
from typing import Protocol
from uuid import UUID


class KnowledgeBaseRebuildDispatcher(Protocol):
    async def enqueue(self, operation_id: UUID, run_generation: UUID) -> None: ...


class CeleryKnowledgeBaseRebuildDispatcher:
    async def enqueue(self, operation_id: UUID, run_generation: UUID) -> None:
        from app.tasks.rebuild import rebuild_knowledge_base

        await asyncio.to_thread(
            rebuild_knowledge_base.delay,
            str(operation_id),
            str(run_generation),
        )
