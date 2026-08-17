from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.consistency_repair import (
    ConsistencyAuditFindingRecord,
    ConsistencyAuditSnapshotRecord,
    ConsistencyRepairItem,
    ConsistencyRepairOperation,
)
from app.models.knowledge_base import KnowledgeBase


class ConsistencyRepairRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def knowledge_base_exists(self, knowledge_base_id: UUID) -> bool:
        result = await self.session.execute(
            select(KnowledgeBase.id).where(KnowledgeBase.id == knowledge_base_id)
        )
        return result.scalar_one_or_none() is not None

    async def selected_findings(
        self, audit_id: UUID, knowledge_base_id: UUID, finding_ids: list[UUID]
    ) -> list[ConsistencyAuditFindingRecord]:
        if await self.session.get(ConsistencyAuditSnapshotRecord, audit_id) is None:
            return []
        result = await self.session.execute(
            select(ConsistencyAuditFindingRecord).where(
                ConsistencyAuditFindingRecord.audit_id == audit_id,
                ConsistencyAuditFindingRecord.id.in_(finding_ids),
                ConsistencyAuditFindingRecord.knowledge_base_id == knowledge_base_id,
            )
        )
        by_id = {item.id: item for item in result.scalars()}
        return [by_id[item_id] for item_id in finding_ids if item_id in by_id]

    async def create_operation(
        self,
        audit_id: UUID,
        knowledge_base_id: UUID,
        findings: list[ConsistencyAuditFindingRecord],
        actions: dict[str, str],
    ) -> ConsistencyRepairOperation:
        operation = ConsistencyRepairOperation(
            audit_id=audit_id, knowledge_base_id=knowledge_base_id, status="queued"
        )
        self.session.add(operation)
        await self.session.flush()
        self.session.add_all(
            ConsistencyRepairItem(
                operation_id=operation.id,
                finding_id=finding.id,
                finding_code=finding.code,
                entity_type=finding.entity_type,
                entity_id=finding.entity_id,
                status="pending",
                action=actions.get(finding.code, "manual_review"),
                safe_message="Repair is queued for current-state revalidation.",
            )
            for finding in findings
        )
        await self.session.flush()
        return operation

    async def get_active_operation(
        self, knowledge_base_id: UUID
    ) -> ConsistencyRepairOperation | None:
        result = await self.session.execute(
            select(ConsistencyRepairOperation).where(
                ConsistencyRepairOperation.knowledge_base_id == knowledge_base_id,
                ConsistencyRepairOperation.status.in_(("queued", "running")),
            )
        )
        return result.scalar_one_or_none()

    async def get_operation(
        self, operation_id: UUID, knowledge_base_id: UUID | None = None
    ) -> ConsistencyRepairOperation | None:
        statement = (
            select(ConsistencyRepairOperation)
            .options(selectinload(ConsistencyRepairOperation.items))
            .where(ConsistencyRepairOperation.id == operation_id)
        )
        if knowledge_base_id is not None:
            statement = statement.where(
                ConsistencyRepairOperation.knowledge_base_id == knowledge_base_id
            )
        return (
            await self.session.execute(statement.execution_options(populate_existing=True))
        ).scalar_one_or_none()

    async def get_finding(self, finding_id: UUID) -> ConsistencyAuditFindingRecord | None:
        return await self.session.get(ConsistencyAuditFindingRecord, finding_id)

    async def claim_operation(
        self,
        operation_id: UUID,
        run_generation: UUID,
        *,
        stale_after_seconds: int,
    ) -> UUID | None:
        operation = await self._lock_operation(operation_id)
        if operation is None or operation.run_generation != run_generation:
            await self.session.rollback()
            return None
        now = datetime.now(UTC)
        owned_generation = run_generation
        if operation.status == "queued":
            operation.status = "running"
            operation.started_at = operation.started_at or now
        elif operation.status == "running" and self._heartbeat_is_stale(
            operation, now=now, stale_after_seconds=stale_after_seconds
        ):
            owned_generation = uuid4()
            operation.run_generation = owned_generation
            items = await self.list_items(operation_id)
            for item in items:
                if item.status == "running":
                    item.status = "pending"
                    item.started_at = None
                    item.completed_at = None
                    item.safe_message = "Repair was recovered after a stale worker lease."
        else:
            await self.session.rollback()
            return None
        operation.heartbeat_at = now
        operation.completed_at = None
        await self.session.commit()
        return owned_generation

    async def owns_run(self, operation_id: UUID, run_generation: UUID) -> bool:
        result = await self.session.execute(
            select(ConsistencyRepairOperation.id).where(
                ConsistencyRepairOperation.id == operation_id,
                ConsistencyRepairOperation.run_generation == run_generation,
                ConsistencyRepairOperation.status == "running",
            )
        )
        return result.scalar_one_or_none() is not None

    async def list_items(self, operation_id: UUID) -> list[ConsistencyRepairItem]:
        result = await self.session.execute(
            select(ConsistencyRepairItem)
            .where(ConsistencyRepairItem.operation_id == operation_id)
            .order_by(ConsistencyRepairItem.id)
        )
        return list(result.scalars())

    async def mark_item_running(
        self, item_id: UUID, operation_id: UUID, run_generation: UUID
    ) -> bool:
        operation = await self._lock_operation(operation_id)
        if (
            operation is None
            or operation.run_generation != run_generation
            or operation.status != "running"
        ):
            await self.session.rollback()
            return False
        item = await self._lock_item(item_id)
        if item is None or item.operation_id != operation_id or item.status != "pending":
            await self.session.rollback()
            return False
        now = datetime.now(UTC)
        item.status = "running"
        item.started_at = now
        item.completed_at = None
        operation.heartbeat_at = now
        await self.session.commit()
        return True

    async def finish_item(
        self,
        item_id: UUID,
        operation_id: UUID,
        run_generation: UUID,
        status: str,
        safe_message: str,
    ) -> bool:
        operation = await self._lock_operation(operation_id)
        if (
            operation is None
            or operation.run_generation != run_generation
            or operation.status != "running"
        ):
            await self.session.rollback()
            return False
        item = await self._lock_item(item_id)
        if item is None or item.operation_id != operation_id or item.status != "running":
            await self.session.rollback()
            return False
        now = datetime.now(UTC)
        item.status = status
        item.safe_message = safe_message[:500]
        item.completed_at = now
        operation.heartbeat_at = now
        await self.session.commit()
        return True

    async def fail_queue(self, operation_id: UUID, run_generation: UUID) -> None:
        operation = await self._lock_operation(operation_id)
        if operation is None or operation.run_generation != run_generation:
            await self.session.rollback()
            return
        operation.status = "failed"
        operation.completed_at = datetime.now(UTC)
        operation.heartbeat_at = operation.completed_at
        for item in await self.list_items(operation_id):
            if item.status == "pending":
                item.status = "failed"
                item.safe_message = "Repair could not be queued."
                item.completed_at = datetime.now(UTC)
        await self.session.commit()

    async def prepare_retry(
        self,
        operation_id: UUID,
        *,
        stale_after_seconds: int,
    ) -> tuple[ConsistencyRepairOperation, bool]:
        operation = await self._lock_operation(operation_id)
        if operation is None:
            raise LookupError("Consistency repair operation was not found")
        now = datetime.now(UTC)
        active = operation.status in {"queued", "running"}
        stale = self._heartbeat_is_stale(
            operation, now=now, stale_after_seconds=stale_after_seconds
        )
        if active and not stale:
            return operation, False
        if operation.status == "succeeded":
            return operation, False
        for item in await self.list_items(operation_id):
            if item.status in {"running", "failed", "verification_failed"}:
                item.status = "pending"
                item.started_at = None
                item.completed_at = None
                item.safe_message = "Repair is queued for current-state revalidation."
        operation.status = "queued"
        operation.run_generation = uuid4()
        operation.heartbeat_at = now
        operation.completed_at = None
        await self.session.flush()
        return operation, True

    async def finalize(self, operation_id: UUID, run_generation: UUID) -> bool:
        operation = await self._lock_operation(operation_id)
        if (
            operation is None
            or operation.run_generation != run_generation
            or operation.status != "running"
        ):
            await self.session.rollback()
            return False
        items = await self.list_items(operation_id)
        failures = sum(item.status in {"failed", "verification_failed"} for item in items)
        successes = sum(item.status == "succeeded" for item in items)
        operation.status = (
            "partially_failed" if failures and successes else "failed" if failures else "succeeded"
        )
        operation.completed_at = datetime.now(UTC)
        operation.heartbeat_at = operation.completed_at
        await self.session.commit()
        return operation.status == "succeeded"

    @staticmethod
    def _heartbeat_is_stale(
        operation: ConsistencyRepairOperation,
        *,
        now: datetime,
        stale_after_seconds: int,
    ) -> bool:
        heartbeat = operation.heartbeat_at or operation.started_at or operation.created_at
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=UTC)
        return heartbeat <= now - timedelta(seconds=stale_after_seconds)

    async def _lock_operation(self, operation_id: UUID) -> ConsistencyRepairOperation | None:
        result = await self.session.execute(
            select(ConsistencyRepairOperation)
            .where(ConsistencyRepairOperation.id == operation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def _lock_item(self, item_id: UUID) -> ConsistencyRepairItem | None:
        result = await self.session.execute(
            select(ConsistencyRepairItem)
            .where(ConsistencyRepairItem.id == item_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()
