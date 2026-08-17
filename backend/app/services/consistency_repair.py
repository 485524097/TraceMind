import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.indexing import QdrantGateway
from app.models.consistency_repair import (
    ConsistencyAuditFindingRecord,
    ConsistencyRepairOperation,
)
from app.repositories.consistency_audit import ConsistencyAuditRepository
from app.repositories.consistency_repair import ConsistencyRepairRepository
from app.repositories.knowledge_base_restore_lock import RestoreAdvisoryLock
from app.schemas.consistency_audit import ConsistencyAuditFinding
from app.schemas.consistency_repair import (
    ConsistencyRepairItemResponse,
    ConsistencyRepairRequest,
    ConsistencyRepairResponse,
)
from app.services.consistency_audit import ConsistencyAuditService
from app.services.consistency_repair_dispatcher import ConsistencyRepairDispatcher
from app.services.exceptions import (
    ConsistencyAuditSelectionError,
    ConsistencyRepairAlreadyActiveError,
    ConsistencyRepairNotFoundError,
    ConsistencyRepairNotRetryableError,
    KnowledgeBaseNotFoundError,
)
from app.storage.archive import LocalArchiveStorage

logger = logging.getLogger(__name__)

REPAIR_ACTIONS = {
    "parsed_version_missing_chunks": "parse_document_version",
    "chunk_count_mismatch": "parse_document_version",
    "latest_index_generation_missing": "index_latest_document_version",
    "active_index_points_missing": "index_latest_document_version",
    "active_index_point_count_mismatch": "index_latest_document_version",
    "verified_knowledge_index_missing": "index_verified_knowledge_entry",
    "knowledge_index_point_count_mismatch": "index_verified_knowledge_entry",
    "stale_qdrant_generation": "delete_stale_generation",
    "orphan_qdrant_point": "delete_verified_orphan_point",
    "restore_journal_cleanup_pending": "finish_restore_journal_cleanup",
}
SOURCE_DAMAGE_CODES = {
    "document_file_missing",
    "document_file_size_mismatch",
    "document_file_hash_mismatch",
    "document_storage_path_invalid",
    "document_file_not_regular",
}
SOURCE_UNSAFE_CODES = SOURCE_DAMAGE_CODES | {"storage_audit_unavailable"}
DOCUMENT_DERIVED_REPAIR_CODES = {
    "parsed_version_missing_chunks",
    "chunk_count_mismatch",
    "latest_index_generation_missing",
    "active_index_points_missing",
    "active_index_point_count_mismatch",
}


class DocumentParsePipeline(Protocol):
    async def parse_version(
        self, version_id: UUID, *, force: bool = False, enqueue_index: bool = True
    ) -> bool: ...


class DocumentIndexPipeline(Protocol):
    async def index_version(self, version_id: UUID, *, force: bool = False) -> bool: ...


class KnowledgeIndexPipeline(Protocol):
    async def sync_entry(self, entry_id: UUID, *, force: bool = False) -> bool: ...


@dataclass(frozen=True)
class HandlerResult:
    status: str
    message: str


def _matches(record: ConsistencyAuditFindingRecord, current: ConsistencyAuditFinding) -> bool:
    if (
        record.code != current.code
        or record.entity_type != current.entity_type
        or record.entity_id != current.entity_id
    ):
        return False
    generation = record.details.get("index_generation")
    return generation is None or current.details.get("index_generation") == generation


def _source_is_unsafe(findings: list[ConsistencyAuditFinding], entity_id: str) -> bool:
    return any(
        item.code in SOURCE_UNSAFE_CODES and item.entity_id == entity_id for item in findings
    )


def _flags(code: str) -> tuple[bool, bool, bool, bool]:
    return (
        code in {"parsed_version_missing_chunks", "chunk_count_mismatch"},
        code
        in {
            "latest_index_generation_missing",
            "active_index_points_missing",
            "active_index_point_count_mismatch",
            "verified_knowledge_index_missing",
            "knowledge_index_point_count_mismatch",
        },
        code in {"stale_qdrant_generation", "orphan_qdrant_point"},
        code == "restore_journal_cleanup_pending",
    )


class ConsistencyRepairService:
    def __init__(
        self,
        session: AsyncSession,
        audit_service: ConsistencyAuditService,
        dispatcher: ConsistencyRepairDispatcher,
        repository: ConsistencyRepairRepository | None = None,
        *,
        stale_after_seconds: int = 3_600,
    ) -> None:
        self.session = session
        self.audit_service = audit_service
        self.dispatcher = dispatcher
        self.repository = repository or ConsistencyRepairRepository(session)
        self.stale_after_seconds = stale_after_seconds

    async def start(self, request: ConsistencyRepairRequest) -> ConsistencyRepairResponse:
        if not await self.repository.knowledge_base_exists(request.knowledge_base_id):
            raise KnowledgeBaseNotFoundError(request.knowledge_base_id)
        findings = await self.repository.selected_findings(
            request.audit_id, request.knowledge_base_id, request.finding_ids
        )
        if len(findings) != len(request.finding_ids):
            raise ConsistencyAuditSelectionError()
        if request.dry_run:
            return await self._dry_run(request, findings)
        if await self.repository.get_active_operation(request.knowledge_base_id) is not None:
            raise ConsistencyRepairAlreadyActiveError()
        try:
            operation = await self.repository.create_operation(
                request.audit_id, request.knowledge_base_id, findings, REPAIR_ACTIONS
            )
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConsistencyRepairAlreadyActiveError() from exc
        await self._dispatch(operation)
        loaded = await self.repository.get_operation(operation.id, request.knowledge_base_id)
        assert loaded is not None
        return self._operation_response(loaded)

    async def retry(self, knowledge_base_id: UUID, operation_id: UUID) -> ConsistencyRepairResponse:
        if not await self.repository.knowledge_base_exists(knowledge_base_id):
            raise KnowledgeBaseNotFoundError(knowledge_base_id)
        operation = await self.repository.get_operation(operation_id, knowledge_base_id)
        if operation is None:
            raise ConsistencyRepairNotFoundError()
        operation, prepared = await self.repository.prepare_retry(
            operation_id,
            stale_after_seconds=self.stale_after_seconds,
        )
        if not prepared:
            await self.session.rollback()
            if operation.status in {"queued", "running"}:
                raise ConsistencyRepairAlreadyActiveError()
            raise ConsistencyRepairNotRetryableError()
        await self.session.commit()
        await self._dispatch(operation)
        loaded = await self.repository.get_operation(operation.id, knowledge_base_id)
        assert loaded is not None
        return self._operation_response(loaded)

    async def _dispatch(self, operation: ConsistencyRepairOperation) -> None:
        try:
            await self.dispatcher.enqueue(operation.id, operation.run_generation)
        except Exception:
            logger.warning("Consistency repair could not be queued operation_id=%s", operation.id)
            await self.repository.fail_queue(operation.id, operation.run_generation)

    async def _dry_run(
        self,
        request: ConsistencyRepairRequest,
        findings: list[ConsistencyAuditFindingRecord],
    ) -> ConsistencyRepairResponse:
        current = await self.audit_service.inspect_knowledge_base(request.knowledge_base_id)
        items: list[ConsistencyRepairItemResponse] = []
        for finding in findings:
            present = any(_matches(finding, item) for item in current.findings)
            source_unsafe = finding.code in DOCUMENT_DERIVED_REPAIR_CODES and _source_is_unsafe(
                current.findings, finding.entity_id
            )
            repairable = finding.code in REPAIR_ACTIONS and not source_unsafe
            status = (
                "planned" if present and repairable else "not_repairable" if present else "skipped"
            )
            message = (
                "Repair would revalidate and execute the listed derived-state action."
                if status == "planned"
                else "Source of Truth or ambiguous state requires manual handling or Restore."
                if status == "not_repairable"
                else "Finding is no longer present."
            )
            items.append(self._planned_item(finding, repairable, status, message))
        return ConsistencyRepairResponse(
            knowledge_base_id=request.knowledge_base_id,
            audit_id=request.audit_id,
            operation_id=None,
            dry_run=True,
            status="planned",
            items=items,
        )

    async def get_status(
        self, knowledge_base_id: UUID, operation_id: UUID
    ) -> ConsistencyRepairResponse:
        if not await self.repository.knowledge_base_exists(knowledge_base_id):
            raise KnowledgeBaseNotFoundError(knowledge_base_id)
        operation = await self.repository.get_operation(operation_id, knowledge_base_id)
        if operation is None:
            raise ConsistencyRepairNotFoundError()
        return self._operation_response(operation)

    @staticmethod
    def _planned_item(
        finding: ConsistencyAuditFindingRecord, repairable: bool, status: str, message: str
    ) -> ConsistencyRepairItemResponse:
        parse, index, qdrant, journal = _flags(finding.code)
        return ConsistencyRepairItemResponse(
            finding_id=finding.id,
            finding_code=finding.code,
            entity_type=finding.entity_type,
            entity_id=finding.entity_id,
            repairable=repairable,
            status=status,
            action=REPAIR_ACTIONS.get(finding.code, "manual_review"),
            requires_parse=parse,
            requires_index=index,
            deletes_qdrant_points=qdrant,
            cleans_journal=journal,
            safe_message=message,
        )

    @staticmethod
    def _operation_response(operation: ConsistencyRepairOperation) -> ConsistencyRepairResponse:
        items = []
        for item in operation.items:
            parse, index, qdrant, journal = _flags(item.finding_code)
            items.append(
                ConsistencyRepairItemResponse(
                    finding_id=item.finding_id,
                    finding_code=item.finding_code,
                    entity_type=item.entity_type,
                    entity_id=item.entity_id,
                    repairable=item.finding_code in REPAIR_ACTIONS,
                    status=item.status,
                    action=item.action,
                    requires_parse=parse,
                    requires_index=index,
                    deletes_qdrant_points=qdrant,
                    cleans_journal=journal,
                    started_at=item.started_at,
                    completed_at=item.completed_at,
                    safe_message=item.safe_message,
                )
            )
        return ConsistencyRepairResponse(
            knowledge_base_id=operation.knowledge_base_id,
            audit_id=operation.audit_id,
            operation_id=operation.id,
            dry_run=False,
            status=operation.status,
            items=items,
            started_at=operation.started_at,
            completed_at=operation.completed_at,
        )


class ConsistencyRepairExecutor:
    def __init__(
        self,
        session: AsyncSession,
        audit_service: ConsistencyAuditService,
        parser: DocumentParsePipeline,
        document_indexer: DocumentIndexPipeline,
        knowledge_indexer: KnowledgeIndexPipeline,
        gateway: QdrantGateway,
        archive_storage: LocalArchiveStorage,
        audit_repository: ConsistencyAuditRepository,
        repository: ConsistencyRepairRepository | None = None,
        *,
        stale_after_seconds: int = 3_600,
        restore_lock: RestoreAdvisoryLock | None = None,
    ) -> None:
        self.session = session
        self.audit_service = audit_service
        self.parser = parser
        self.document_indexer = document_indexer
        self.knowledge_indexer = knowledge_indexer
        self.gateway = gateway
        self.archive_storage = archive_storage
        self.audit_repository = audit_repository
        self.repository = repository or ConsistencyRepairRepository(session)
        self.stale_after_seconds = stale_after_seconds
        self.restore_lock = restore_lock or RestoreAdvisoryLock.from_session(session)
        self.handlers: dict[
            str, Callable[[ConsistencyAuditFindingRecord], Awaitable[HandlerResult]]
        ] = {
            "parsed_version_missing_chunks": self._repair_parsed_version_missing_chunks,
            "chunk_count_mismatch": self._repair_chunk_count_mismatch,
            "latest_index_generation_missing": self._repair_latest_index_generation_missing,
            "active_index_points_missing": self._repair_active_index_points_missing,
            "active_index_point_count_mismatch": self._repair_active_index_point_count_mismatch,
            "verified_knowledge_index_missing": self._repair_verified_knowledge_index_missing,
            "knowledge_index_point_count_mismatch": (
                self._repair_knowledge_index_point_count_mismatch
            ),
            "stale_qdrant_generation": self._repair_stale_qdrant_generation,
            "orphan_qdrant_point": self._repair_orphan_qdrant_point,
            "restore_journal_cleanup_pending": self._repair_restore_journal_cleanup_pending,
        }

    async def run(self, operation_id: UUID, run_generation: UUID) -> bool:
        owned_generation = await self.repository.claim_operation(
            operation_id,
            run_generation,
            stale_after_seconds=self.stale_after_seconds,
        )
        if owned_generation is None:
            return False
        operation = await self.repository.get_operation(operation_id)
        if operation is None:
            return False
        order = {
            "parse_document_version": 0,
            "index_latest_document_version": 1,
            "index_verified_knowledge_entry": 2,
            "delete_stale_generation": 3,
            "delete_verified_orphan_point": 4,
            "finish_restore_journal_cleanup": 5,
            "manual_review": 6,
        }
        items = sorted(
            (item for item in operation.items if item.status == "pending"),
            key=lambda item: (order.get(item.action, 99), str(item.id)),
        )
        for item in items:
            if not await self.repository.mark_item_running(item.id, operation_id, owned_generation):
                return False
            finding = await self.repository.get_finding(item.finding_id)
            if finding is None:
                if not await self.repository.finish_item(
                    item.id,
                    operation_id,
                    owned_generation,
                    "failed",
                    "Audit finding metadata is unavailable.",
                ):
                    return False
                continue
            handler = self.handlers.get(finding.code)
            if handler is None:
                if not await self.repository.finish_item(
                    item.id,
                    operation_id,
                    owned_generation,
                    "not_repairable",
                    "Source of Truth or unknown state requires manual handling or Restore.",
                ):
                    return False
                continue
            try:
                current = await self.audit_service.inspect_knowledge_base(
                    operation.knowledge_base_id
                )
                matching = next(
                    (entry for entry in current.findings if _matches(finding, entry)), None
                )
                if matching is None:
                    result = HandlerResult("skipped", "finding_no_longer_present")
                elif finding.code in DOCUMENT_DERIVED_REPAIR_CODES and _source_is_unsafe(
                    current.findings, finding.entity_id
                ):
                    result = HandlerResult(
                        "not_repairable",
                        "Document Source of Truth requires manual handling or Restore.",
                    )
                else:
                    result = await handler(finding)
                    if result.status == "succeeded":
                        verified = await self.audit_service.inspect_knowledge_base(
                            operation.knowledge_base_id
                        )
                        if _source_is_unsafe(verified.findings, finding.entity_id):
                            result = HandlerResult(
                                "verification_failed",
                                "Document Source of Truth changed during repair.",
                            )
                        elif any(_matches(finding, entry) for entry in verified.findings):
                            result = HandlerResult(
                                "verification_failed",
                                "Repair completed but targeted Audit still reports the finding.",
                            )
            except Exception as exc:
                await self.session.rollback()
                logger.error("Repair item failed item_id=%s (%s)", item.id, type(exc).__name__)
                result = HandlerResult("failed", "Derived state could not be safely repaired.")
            if not await self.repository.finish_item(
                item.id,
                operation_id,
                owned_generation,
                result.status,
                result.message,
            ):
                return False
        return await self.repository.finalize(operation_id, owned_generation)

    async def _parse(self, finding: ConsistencyAuditFindingRecord) -> HandlerResult:
        succeeded = await self.parser.parse_version(
            UUID(finding.entity_id), force=True, enqueue_index=False
        )
        return (
            HandlerResult("succeeded", "Document chunks were regenerated.")
            if succeeded
            else HandlerResult("failed", "Document version could not be parsed.")
        )

    async def _repair_parsed_version_missing_chunks(
        self, finding: ConsistencyAuditFindingRecord
    ) -> HandlerResult:
        return await self._parse(finding)

    async def _repair_chunk_count_mismatch(
        self, finding: ConsistencyAuditFindingRecord
    ) -> HandlerResult:
        return await self._parse(finding)

    async def _index_document(self, finding: ConsistencyAuditFindingRecord) -> HandlerResult:
        snapshot = await self.audit_repository.load_snapshot(finding.knowledge_base_id)
        target = next(
            (item for item in snapshot.versions if item.version_id == UUID(finding.entity_id)), None
        )
        if target is None:
            return HandlerResult("skipped", "finding_no_longer_present")
        latest = max(
            (item for item in snapshot.versions if item.document_id == target.document_id),
            key=lambda item: item.version_number,
        )
        if latest.version_id != target.version_id:
            return HandlerResult("skipped", "Document version is no longer latest.")
        succeeded = await self.document_indexer.index_version(target.version_id, force=True)
        if not succeeded:
            refreshed = await self.audit_repository.load_snapshot(finding.knowledge_base_id)
            refreshed_target = next(
                (item for item in refreshed.versions if item.version_id == target.version_id), None
            )
            if refreshed_target is None:
                return HandlerResult("skipped", "finding_no_longer_present")
            refreshed_latest = max(
                (
                    item
                    for item in refreshed.versions
                    if item.document_id == refreshed_target.document_id
                ),
                key=lambda item: item.version_number,
            )
            if refreshed_latest.version_id != refreshed_target.version_id:
                return HandlerResult("skipped", "Document version is no longer latest.")
        return (
            HandlerResult("succeeded", "Latest document index was regenerated.")
            if succeeded
            else HandlerResult("failed", "Latest document index could not be regenerated.")
        )

    async def _repair_latest_index_generation_missing(
        self, finding: ConsistencyAuditFindingRecord
    ) -> HandlerResult:
        return await self._index_document(finding)

    async def _repair_active_index_points_missing(
        self, finding: ConsistencyAuditFindingRecord
    ) -> HandlerResult:
        return await self._index_document(finding)

    async def _repair_active_index_point_count_mismatch(
        self, finding: ConsistencyAuditFindingRecord
    ) -> HandlerResult:
        return await self._index_document(finding)

    async def _index_knowledge(self, finding: ConsistencyAuditFindingRecord) -> HandlerResult:
        snapshot = await self.audit_repository.load_snapshot(finding.knowledge_base_id)
        entry = next(
            (
                item
                for item in snapshot.knowledge_entries
                if item.entry_id == UUID(finding.entity_id)
            ),
            None,
        )
        if entry is None or entry.validation_status != "verified":
            return HandlerResult("skipped", "Knowledge entry is no longer verified/current.")
        succeeded = await self.knowledge_indexer.sync_entry(entry.entry_id, force=True)
        return (
            HandlerResult("succeeded", "Verified knowledge index was regenerated.")
            if succeeded
            else HandlerResult("failed", "Verified knowledge index could not be regenerated.")
        )

    async def _repair_verified_knowledge_index_missing(
        self, finding: ConsistencyAuditFindingRecord
    ) -> HandlerResult:
        return await self._index_knowledge(finding)

    async def _repair_knowledge_index_point_count_mismatch(
        self, finding: ConsistencyAuditFindingRecord
    ) -> HandlerResult:
        return await self._index_knowledge(finding)

    async def _repair_stale_qdrant_generation(
        self, finding: ConsistencyAuditFindingRecord
    ) -> HandlerResult:
        generation = UUID(str(finding.details["index_generation"]))
        snapshot = await self.audit_repository.load_snapshot(finding.knowledge_base_id)
        version = next(
            (item for item in snapshot.versions if item.version_id == UUID(finding.entity_id)), None
        )
        protected_generations = (
            {item.active_generation for item in snapshot.versions}
            | {item.attempt_generation for item in snapshot.versions}
            | {item.active_generation for item in snapshot.knowledge_entries}
            | {item.attempt_generation for item in snapshot.knowledge_entries}
        )
        if version is None or generation in protected_generations:
            return HandlerResult("skipped", "Generation is no longer proven stale.")
        points = []
        offset = None
        while True:
            page = await self.gateway.audit_generation_payload_page(
                generation, offset=offset, limit=100
            )
            points.extend(page.points)
            if page.next_offset is None:
                break
            offset = page.next_offset
        for point in points:
            parsed = self.audit_service._parse_qdrant_payload(point.payload)
            if parsed is None:
                return HandlerResult("not_repairable", "Generation contains ambiguous payload.")
            source_type, kb_id, document_id, version_id, payload_generation = parsed
            if (
                source_type != "document"
                or kb_id != finding.knowledge_base_id
                or document_id != version.document_id
                or version_id != version.version_id
                or payload_generation != generation
            ):
                return HandlerResult("not_repairable", "Generation ownership is ambiguous.")
        if not points:
            return HandlerResult("skipped", "finding_no_longer_present")
        await self.gateway.delete_generation(generation)
        return HandlerResult("succeeded", "Verified stale Qdrant generation was deleted.")

    async def _repair_orphan_qdrant_point(
        self, finding: ConsistencyAuditFindingRecord
    ) -> HandlerResult:
        try:
            point_id = UUID(finding.entity_id)
        except ValueError:
            return HandlerResult("not_repairable", "Orphan point ID is not safely addressable.")
        points = await self.gateway.audit_points([point_id])
        if not points:
            return HandlerResult("skipped", "finding_no_longer_present")
        parsed = self.audit_service._parse_qdrant_payload(points[0].payload)
        if parsed is None:
            return HandlerResult("not_repairable", "Orphan payload is ambiguous.")
        source_type, kb_id, entity_id, related_id, generation = parsed
        expected_entity = (
            finding.details.get("document_id")
            or finding.details.get("knowledge_entry_id")
            or finding.details.get("source_entity_id")
        )
        if (
            kb_id != finding.knowledge_base_id
            or source_type != finding.details.get("source_type")
            or str(entity_id) != str(expected_entity)
            or str(generation) != str(finding.details.get("index_generation"))
        ):
            return HandlerResult(
                "not_repairable", "Orphan point ownership changed or is ambiguous."
            )
        if source_type == "document" and str(related_id) != str(
            finding.details.get("document_version_id") or finding.details.get("related_id")
        ):
            return HandlerResult("not_repairable", "Orphan point version ownership is ambiguous.")
        await self.gateway.delete_points([point_id])
        return HandlerResult("succeeded", "Verified orphan Qdrant point was deleted.")

    async def _repair_restore_journal_cleanup_pending(
        self, finding: ConsistencyAuditFindingRecord
    ) -> HandlerResult:
        inspection = await self.archive_storage.inspect_restore_journals()
        operation_id = UUID(finding.entity_id)
        match = next(
            (
                (path, journal)
                for path, journal in inspection.valid
                if journal.operation_id == operation_id
            ),
            None,
        )
        if match is None:
            return HandlerResult("skipped", "finding_no_longer_present")
        path, journal = match
        async with self.restore_lock.try_hold(journal.knowledge_base_id) as acquired:
            if not acquired:
                return HandlerResult("skipped", "Active Restore still owns this journal.")
            snapshot = await self.audit_repository.load_snapshot(journal.knowledge_base_id)
            database_exists = journal.knowledge_base_id in snapshot.knowledge_base_ids
            complete = await self.archive_storage.final_restore_is_complete(journal)
            if database_exists and complete:
                await self.archive_storage.finish_recovered_restore(path, journal)
            elif not database_exists:
                await self.archive_storage.recover_absent_database_restore(path, journal)
            else:
                return HandlerResult(
                    "not_repairable", "Restore journal is inconsistent and needs manual review."
                )
        return HandlerResult("succeeded", "Validated restore journal cleanup completed.")
