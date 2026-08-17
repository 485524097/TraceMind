from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentVersion
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_base_rebuild import (
    KnowledgeBaseRebuildItem,
    KnowledgeBaseRebuildOperation,
)
from app.models.knowledge_entry import KnowledgeEntry


@dataclass(frozen=True)
class RebuildCounts:
    document_versions_total: int = 0
    document_versions_parsed: int = 0
    document_versions_failed: int = 0
    documents_total: int = 0
    documents_indexed: int = 0
    documents_failed: int = 0
    knowledge_entries_total: int = 0
    knowledge_entries_indexed: int = 0
    knowledge_entries_failed: int = 0


class KnowledgeBaseRebuildRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def knowledge_base_exists(self, knowledge_base_id: UUID) -> bool:
        result = await self.session.execute(
            select(KnowledgeBase.id).where(KnowledgeBase.id == knowledge_base_id)
        )
        return result.scalar_one_or_none() is not None

    async def get_active_operation(
        self, knowledge_base_id: UUID
    ) -> KnowledgeBaseRebuildOperation | None:
        result = await self.session.execute(
            select(KnowledgeBaseRebuildOperation)
            .where(
                KnowledgeBaseRebuildOperation.knowledge_base_id == knowledge_base_id,
                KnowledgeBaseRebuildOperation.status.in_(("queued", "running")),
            )
            .order_by(KnowledgeBaseRebuildOperation.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_operation(
        self, knowledge_base_id: UUID
    ) -> KnowledgeBaseRebuildOperation | None:
        result = await self.session.execute(
            select(KnowledgeBaseRebuildOperation)
            .where(KnowledgeBaseRebuildOperation.knowledge_base_id == knowledge_base_id)
            .order_by(
                KnowledgeBaseRebuildOperation.created_at.desc(),
                KnowledgeBaseRebuildOperation.id.desc(),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create_operation(self, knowledge_base_id: UUID) -> KnowledgeBaseRebuildOperation:
        operation = KnowledgeBaseRebuildOperation(
            knowledge_base_id=knowledge_base_id,
            status="queued",
            run_generation=uuid4(),
        )
        self.session.add(operation)
        await self.session.flush()

        version_rows = (
            await self.session.execute(
                select(Document.id, DocumentVersion.id, DocumentVersion.version_number)
                .join(DocumentVersion, DocumentVersion.document_id == Document.id)
                .where(Document.knowledge_base_id == knowledge_base_id)
                .order_by(Document.id, DocumentVersion.version_number)
            )
        ).all()
        latest_by_document: dict[UUID, UUID] = {}
        items: list[KnowledgeBaseRebuildItem] = []
        for document_id, version_id, _version_number in version_rows:
            latest_by_document[document_id] = version_id
            items.append(
                KnowledgeBaseRebuildItem(
                    operation_id=operation.id,
                    work_type="document_parse",
                    target_id=version_id,
                    document_id=document_id,
                )
            )
        items.extend(
            KnowledgeBaseRebuildItem(
                operation_id=operation.id,
                work_type="document_index",
                target_id=version_id,
                document_id=document_id,
            )
            for document_id, version_id in latest_by_document.items()
        )
        entry_ids = (
            await self.session.execute(
                select(KnowledgeEntry.id)
                .where(
                    KnowledgeEntry.knowledge_base_id == knowledge_base_id,
                    KnowledgeEntry.validation_status == "verified",
                )
                .order_by(KnowledgeEntry.id)
            )
        ).scalars()
        items.extend(
            KnowledgeBaseRebuildItem(
                operation_id=operation.id,
                work_type="knowledge_entry_index",
                target_id=entry_id,
            )
            for entry_id in entry_ids
        )
        self.session.add_all(items)
        await self.session.flush()
        return operation

    async def get_operation(
        self, operation_id: UUID, knowledge_base_id: UUID
    ) -> KnowledgeBaseRebuildOperation | None:
        result = await self.session.execute(
            select(KnowledgeBaseRebuildOperation).where(
                KnowledgeBaseRebuildOperation.id == operation_id,
                KnowledgeBaseRebuildOperation.knowledge_base_id == knowledge_base_id,
            )
        )
        return result.scalar_one_or_none()

    async def claim_operation(self, operation_id: UUID, run_generation: UUID) -> bool:
        operation = await self._lock_operation(operation_id)
        if (
            operation is None
            or operation.run_generation != run_generation
            or operation.status != "queued"
        ):
            await self.session.rollback()
            return False
        now = datetime.now(UTC)
        operation.status = "running"
        operation.started_at = operation.started_at or now
        operation.completed_at = None
        operation.heartbeat_at = now
        operation.error_code = None
        operation.error_message = None
        await self.session.commit()
        return True

    async def owns_run(self, operation_id: UUID, run_generation: UUID) -> bool:
        result = await self.session.execute(
            select(KnowledgeBaseRebuildOperation.id).where(
                KnowledgeBaseRebuildOperation.id == operation_id,
                KnowledgeBaseRebuildOperation.run_generation == run_generation,
                KnowledgeBaseRebuildOperation.status == "running",
            )
        )
        return result.scalar_one_or_none() is not None

    async def list_retryable_items(self, operation_id: UUID) -> list[KnowledgeBaseRebuildItem]:
        priority = case(
            (KnowledgeBaseRebuildItem.work_type == "document_parse", 1),
            (KnowledgeBaseRebuildItem.work_type == "document_index", 2),
            else_=3,
        )
        result = await self.session.execute(
            select(KnowledgeBaseRebuildItem)
            .where(
                KnowledgeBaseRebuildItem.operation_id == operation_id,
                KnowledgeBaseRebuildItem.status.in_(("pending", "failed")),
            )
            .order_by(priority, KnowledgeBaseRebuildItem.document_id, KnowledgeBaseRebuildItem.id)
        )
        return list(result.scalars().all())

    async def mark_item_running(
        self, item_id: UUID, operation_id: UUID, run_generation: UUID
    ) -> bool:
        if not await self.owns_run(operation_id, run_generation):
            await self.session.rollback()
            return False
        item = await self._lock_item(item_id)
        if item is None or item.status == "succeeded":
            await self.session.rollback()
            return False
        now = datetime.now(UTC)
        item.status = "running"
        item.attempt_count += 1
        item.started_at = now
        item.completed_at = None
        item.error_code = None
        item.error_message = None
        operation = await self._lock_operation(operation_id)
        if operation is None or operation.run_generation != run_generation:
            await self.session.rollback()
            return False
        operation.heartbeat_at = now
        await self.session.commit()
        return True

    async def mark_item_succeeded(
        self, item_id: UUID, operation_id: UUID, run_generation: UUID
    ) -> bool:
        return await self._mark_item_complete(
            item_id,
            operation_id,
            run_generation,
            status="succeeded",
            error_code=None,
            error_message=None,
        )

    async def mark_item_failed(
        self,
        item_id: UUID,
        operation_id: UUID,
        run_generation: UUID,
        *,
        error_code: str,
        error_message: str,
    ) -> bool:
        return await self._mark_item_complete(
            item_id,
            operation_id,
            run_generation,
            status="failed",
            error_code=error_code,
            error_message=error_message,
        )

    async def _mark_item_complete(
        self,
        item_id: UUID,
        operation_id: UUID,
        run_generation: UUID,
        *,
        status: str,
        error_code: str | None,
        error_message: str | None,
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
        if item is None or item.operation_id != operation_id:
            await self.session.rollback()
            return False
        now = datetime.now(UTC)
        item.status = status
        item.completed_at = now
        item.error_code = error_code
        item.error_message = error_message[:500] if error_message else None
        operation.heartbeat_at = now
        await self.session.commit()
        return True

    async def mark_queue_failed(
        self, operation_id: UUID, run_generation: UUID, message: str
    ) -> None:
        operation = await self._lock_operation(operation_id)
        if operation is None or operation.run_generation != run_generation:
            await self.session.rollback()
            return
        operation.status = "failed"
        operation.completed_at = datetime.now(UTC)
        operation.error_code = "queue_unavailable"
        operation.error_message = message[:500]
        await self.session.commit()

    async def prepare_retry(
        self,
        operation_id: UUID,
        *,
        stale_after_seconds: int,
    ) -> tuple[KnowledgeBaseRebuildOperation, bool]:
        operation = await self._lock_operation(operation_id)
        if operation is None:
            raise LookupError("Rebuild operation was not found")
        now = datetime.now(UTC)
        active = operation.status in {"queued", "running"}
        heartbeat = operation.heartbeat_at or operation.created_at
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=UTC)
        stale = heartbeat <= now - timedelta(seconds=stale_after_seconds)
        if active and not stale:
            return operation, False
        if operation.status == "succeeded":
            return operation, False
        items = (
            await self.session.execute(
                select(KnowledgeBaseRebuildItem)
                .where(
                    KnowledgeBaseRebuildItem.operation_id == operation_id,
                    KnowledgeBaseRebuildItem.status.in_(("pending", "running", "failed")),
                )
                .with_for_update()
            )
        ).scalars()
        for item in items:
            item.status = "pending"
            item.started_at = None
            item.completed_at = None
            item.error_code = None
            item.error_message = None
        operation.status = "queued"
        operation.run_generation = uuid4()
        operation.heartbeat_at = now
        operation.completed_at = None
        operation.error_code = None
        operation.error_message = None
        await self.session.flush()
        return operation, True

    async def finalize_operation(self, operation_id: UUID, run_generation: UUID) -> bool:
        operation = await self._lock_operation(operation_id)
        if (
            operation is None
            or operation.run_generation != run_generation
            or operation.status != "running"
        ):
            await self.session.rollback()
            return False
        status_rows = (
            await self.session.execute(
                select(KnowledgeBaseRebuildItem.status, func.count())
                .where(KnowledgeBaseRebuildItem.operation_id == operation_id)
                .group_by(KnowledgeBaseRebuildItem.status)
            )
        ).all()
        status_counts = {status: int(count) for status, count in status_rows}
        failed = status_counts.get("failed", 0)
        succeeded = status_counts.get("succeeded", 0)
        unfinished = status_counts.get("pending", 0) + status_counts.get("running", 0)
        if unfinished:
            operation.status = "partially_failed" if succeeded else "failed"
            operation.error_code = "incomplete_rebuild"
            operation.error_message = "Some rebuild work did not complete"
        elif failed:
            operation.status = "partially_failed" if succeeded else "failed"
            operation.error_code = "derived_state_failure"
            operation.error_message = "Some derived state could not be rebuilt"
        else:
            operation.status = "succeeded"
            operation.error_code = None
            operation.error_message = None
        operation.completed_at = datetime.now(UTC)
        operation.heartbeat_at = operation.completed_at
        await self.session.commit()
        return operation.status == "succeeded"

    async def counts(self, operation_id: UUID) -> RebuildCounts:
        rows = (
            await self.session.execute(
                select(
                    KnowledgeBaseRebuildItem.work_type,
                    KnowledgeBaseRebuildItem.status,
                    func.count(),
                )
                .where(KnowledgeBaseRebuildItem.operation_id == operation_id)
                .group_by(
                    KnowledgeBaseRebuildItem.work_type,
                    KnowledgeBaseRebuildItem.status,
                )
            )
        ).all()
        values = {(work_type, status): int(count) for work_type, status, count in rows}

        def total(work_type: str) -> int:
            return sum(
                count for (item_type, _status), count in values.items() if item_type == work_type
            )

        return RebuildCounts(
            document_versions_total=total("document_parse"),
            document_versions_parsed=values.get(("document_parse", "succeeded"), 0),
            document_versions_failed=values.get(("document_parse", "failed"), 0),
            documents_total=total("document_index"),
            documents_indexed=values.get(("document_index", "succeeded"), 0),
            documents_failed=values.get(("document_index", "failed"), 0),
            knowledge_entries_total=total("knowledge_entry_index"),
            knowledge_entries_indexed=values.get(("knowledge_entry_index", "succeeded"), 0),
            knowledge_entries_failed=values.get(("knowledge_entry_index", "failed"), 0),
        )

    async def document_parse_succeeded(self, version_id: UUID) -> bool:
        result = await self.session.execute(
            select(DocumentVersion.parse_status, DocumentVersion.chunk_count).where(
                DocumentVersion.id == version_id
            )
        )
        row = result.one_or_none()
        return row is not None and row.parse_status == "succeeded" and row.chunk_count > 0

    async def document_version_is_latest(self, version_id: UUID) -> bool:
        target = (
            await self.session.execute(
                select(DocumentVersion.document_id, DocumentVersion.version_number).where(
                    DocumentVersion.id == version_id
                )
            )
        ).one_or_none()
        if target is None:
            return False
        latest_number = (
            await self.session.execute(
                select(func.max(DocumentVersion.version_number)).where(
                    DocumentVersion.document_id == target.document_id
                )
            )
        ).scalar_one()
        return bool(latest_number == target.version_number)

    async def document_index_succeeded(self, version_id: UUID) -> bool:
        result = await self.session.execute(
            select(
                DocumentVersion.index_status,
                DocumentVersion.active_index_generation,
                DocumentVersion.indexed_at,
                DocumentVersion.parsed_at,
            ).where(DocumentVersion.id == version_id)
        )
        row = result.one_or_none()
        if (
            row is None
            or row.index_status != "succeeded"
            or row.active_index_generation is None
            or row.indexed_at is None
            or row.parsed_at is None
        ):
            return False
        indexed_at = (
            row.indexed_at.replace(tzinfo=UTC) if row.indexed_at.tzinfo is None else row.indexed_at
        )
        parsed_at = (
            row.parsed_at.replace(tzinfo=UTC) if row.parsed_at.tzinfo is None else row.parsed_at
        )
        return bool(indexed_at >= parsed_at)

    async def knowledge_entry_index_succeeded(self, entry_id: UUID) -> bool:
        result = await self.session.execute(
            select(
                KnowledgeEntry.validation_status,
                KnowledgeEntry.index_status,
                KnowledgeEntry.active_index_generation,
                KnowledgeEntry.indexed_source_updated_at,
                KnowledgeEntry.updated_at,
            ).where(KnowledgeEntry.id == entry_id)
        )
        row = result.one_or_none()
        return bool(
            row is not None
            and row.validation_status == "verified"
            and row.index_status == "succeeded"
            and row.active_index_generation is not None
            and row.indexed_source_updated_at == row.updated_at
        )

    async def _lock_operation(self, operation_id: UUID) -> KnowledgeBaseRebuildOperation | None:
        result = await self.session.execute(
            select(KnowledgeBaseRebuildOperation)
            .where(KnowledgeBaseRebuildOperation.id == operation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def _lock_item(self, item_id: UUID) -> KnowledgeBaseRebuildItem | None:
        result = await self.session.execute(
            select(KnowledgeBaseRebuildItem)
            .where(KnowledgeBaseRebuildItem.id == item_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()
