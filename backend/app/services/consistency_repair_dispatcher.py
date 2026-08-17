import asyncio
from typing import Protocol
from uuid import UUID


class RepairTask(Protocol):
    def delay(self, operation_id: str, run_generation: str) -> object: ...


class ConsistencyRepairDispatcher:
    def __init__(self, task: RepairTask) -> None:
        self.task = task

    async def enqueue(self, operation_id: UUID, run_generation: UUID) -> None:
        await asyncio.to_thread(self.task.delay, str(operation_id), str(run_generation))
