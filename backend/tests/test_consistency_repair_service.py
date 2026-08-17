from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.indexing import QdrantGateway
from app.models.consistency_repair import (
    ConsistencyAuditFindingRecord,
    ConsistencyRepairItem,
    ConsistencyRepairOperation,
)
from app.repositories.consistency_audit import ConsistencyAuditRepository, ConsistencyAuditSnapshot
from app.repositories.consistency_repair import ConsistencyRepairRepository
from app.schemas.consistency_audit import (
    ConsistencyAuditFinding,
    ConsistencyAuditResponse,
    ConsistencyAuditSummary,
)
from app.schemas.consistency_repair import ConsistencyRepairRequest
from app.services.consistency_audit import ConsistencyAuditService
from app.services.consistency_repair import (
    REPAIR_ACTIONS,
    ConsistencyRepairExecutor,
    ConsistencyRepairService,
)
from app.services.consistency_repair_dispatcher import ConsistencyRepairDispatcher
from app.storage.archive import LocalArchiveStorage


def record(code: str, kb_id: UUID, entity_id: UUID | None = None) -> ConsistencyAuditFindingRecord:
    return ConsistencyAuditFindingRecord(
        id=uuid4(),
        audit_id=uuid4(),
        code=code,
        severity="ERROR",
        entity_type="document_version",
        entity_id=str(entity_id or uuid4()),
        knowledge_base_id=kb_id,
        safe_message="finding",
        details={},
    )


def report(kb_id: UUID, findings: list[ConsistencyAuditFindingRecord]) -> ConsistencyAuditResponse:
    now = datetime.now(UTC)
    return ConsistencyAuditResponse(
        audit_id=uuid4(),
        scope="knowledge_base",
        status="completed",
        knowledge_base_id=kb_id,
        started_at=now,
        completed_at=now,
        summary=ConsistencyAuditSummary(
            healthy=not findings, warning_count=0, error_count=len(findings), critical_count=0
        ),
        findings=[
            ConsistencyAuditFinding(
                finding_id=item.id,
                code=item.code,
                severity="ERROR",
                entity_type=item.entity_type,
                entity_id=item.entity_id,
                knowledge_base_id=item.knowledge_base_id,
                safe_message=item.safe_message,
                details=item.details,
            )
            for item in findings
        ],
    )


class NoCommitSession:
    async def commit(self) -> None:
        raise AssertionError("dry-run must not commit")


class FakeAuditService:
    def __init__(self, kb_id: UUID, findings: list[ConsistencyAuditFindingRecord]) -> None:
        self.kb_id = kb_id
        self.findings = findings
        self.calls = 0

    async def inspect_knowledge_base(self, knowledge_base_id: UUID) -> ConsistencyAuditResponse:
        assert knowledge_base_id == self.kb_id
        self.calls += 1
        return report(self.kb_id, self.findings)


class DryRunRepository:
    def __init__(self, kb_id: UUID, findings: list[ConsistencyAuditFindingRecord]) -> None:
        self.kb_id = kb_id
        self.findings = findings
        self.create_calls = 0

    async def knowledge_base_exists(self, knowledge_base_id: UUID) -> bool:
        return knowledge_base_id == self.kb_id

    async def selected_findings(
        self, audit_id: UUID, knowledge_base_id: UUID, finding_ids: list[UUID]
    ) -> list[ConsistencyAuditFindingRecord]:
        assert knowledge_base_id == self.kb_id
        by_id = {item.id: item for item in self.findings}
        return [by_id[item_id] for item_id in finding_ids]

    async def create_operation(self, *args: Any, **kwargs: Any) -> Any:
        self.create_calls += 1
        raise AssertionError("dry-run must not create an operation")


class NoEnqueueDispatcher:
    async def enqueue(self, operation_id: UUID, run_generation: UUID) -> None:
        raise AssertionError("dry-run must not enqueue")


@pytest.mark.asyncio
async def test_dry_run_plans_explicit_allowlist_without_mutation_or_enqueue() -> None:
    kb_id = uuid4()
    findings = [record(code, kb_id) for code in REPAIR_ACTIONS]
    unsafe = record("document_file_hash_mismatch", kb_id)
    findings.append(unsafe)
    repository = DryRunRepository(kb_id, findings)
    service = ConsistencyRepairService(
        cast(AsyncSession, NoCommitSession()),
        cast(ConsistencyAuditService, FakeAuditService(kb_id, findings)),
        cast(ConsistencyRepairDispatcher, NoEnqueueDispatcher()),
        cast(ConsistencyRepairRepository, repository),
    )

    response = await service.start(
        ConsistencyRepairRequest(
            audit_id=findings[0].audit_id,
            knowledge_base_id=kb_id,
            finding_ids=[item.id for item in findings],
        )
    )

    assert response.dry_run is True
    assert response.operation_id is None
    assert repository.create_calls == 0
    assert {item.finding_code for item in response.items if item.status == "planned"} == set(
        REPAIR_ACTIONS
    )
    unsafe_item = next(item for item in response.items if item.finding_id == unsafe.id)
    assert unsafe_item.status == "not_repairable"
    assert unsafe_item.repairable is False


@pytest.mark.asyncio
async def test_dry_run_skips_finding_that_is_no_longer_present() -> None:
    kb_id = uuid4()
    finding = record("parsed_version_missing_chunks", kb_id)
    service = ConsistencyRepairService(
        cast(AsyncSession, NoCommitSession()),
        cast(ConsistencyAuditService, FakeAuditService(kb_id, [])),
        cast(ConsistencyRepairDispatcher, NoEnqueueDispatcher()),
        cast(ConsistencyRepairRepository, DryRunRepository(kb_id, [finding])),
    )
    response = await service.start(
        ConsistencyRepairRequest(
            audit_id=finding.audit_id,
            knowledge_base_id=kb_id,
            finding_ids=[finding.id],
        )
    )
    assert response.items[0].status == "skipped"
    assert response.items[0].safe_message == "Finding is no longer present."


class MutableAuditService(FakeAuditService):
    pass


class FakeParser:
    def __init__(self, audit: MutableAuditService, remove: bool) -> None:
        self.audit = audit
        self.remove = remove
        self.calls: list[tuple[UUID, bool, bool]] = []

    async def parse_version(
        self, version_id: UUID, *, force: bool = False, enqueue_index: bool = True
    ) -> bool:
        self.calls.append((version_id, force, enqueue_index))
        if self.remove:
            self.audit.findings = [
                item for item in self.audit.findings if item.entity_id != str(version_id)
            ]
        return True


class SelectiveParser(FakeParser):
    def __init__(self, audit: MutableAuditService, failing_id: UUID) -> None:
        super().__init__(audit, remove=False)
        self.failing_id = failing_id

    async def parse_version(
        self, version_id: UUID, *, force: bool = False, enqueue_index: bool = True
    ) -> bool:
        self.calls.append((version_id, force, enqueue_index))
        if version_id == self.failing_id:
            return False
        self.audit.findings = [
            item for item in self.audit.findings if item.entity_id != str(version_id)
        ]
        return True


class SnapshotRepository:
    def __init__(self, versions: list[object]) -> None:
        self.versions = versions

    async def load_snapshot(self, _knowledge_base_id: UUID | None) -> object:
        return SimpleNamespace(versions=self.versions)


class SequentialSnapshotRepository:
    def __init__(self, snapshots: list[list[object]]) -> None:
        self.snapshots = snapshots

    async def load_snapshot(self, _knowledge_base_id: UUID | None) -> object:
        return SimpleNamespace(versions=self.snapshots.pop(0))


class FakeDocumentIndexer:
    def __init__(
        self,
        audit: MutableAuditService,
        *,
        result: bool = True,
        replacement_findings: list[ConsistencyAuditFindingRecord] | None = None,
    ) -> None:
        self.audit = audit
        self.result = result
        self.replacement_findings = replacement_findings
        self.calls: list[tuple[UUID, bool]] = []

    async def index_version(self, version_id: UUID, *, force: bool = False) -> bool:
        self.calls.append((version_id, force))
        if self.replacement_findings is not None:
            self.audit.findings = self.replacement_findings
        return self.result


class ExecutorRepository:
    def __init__(
        self,
        operation: ConsistencyRepairOperation,
        items: list[ConsistencyRepairItem],
        findings: list[ConsistencyAuditFindingRecord],
    ) -> None:
        self.operation = operation
        self.operation.items = items
        self.findings = {item.id: item for item in findings}

    async def claim_operation(
        self,
        operation_id: UUID,
        run_generation: UUID,
        *,
        stale_after_seconds: int,
    ) -> UUID | None:
        assert stale_after_seconds > 0
        if operation_id == self.operation.id and run_generation == self.operation.run_generation:
            self.operation.status = "running"
            return run_generation
        return None

    async def get_operation(
        self, operation_id: UUID, knowledge_base_id: UUID | None = None
    ) -> ConsistencyRepairOperation | None:
        return self.operation if operation_id == self.operation.id else None

    async def mark_item_running(
        self, item_id: UUID, operation_id: UUID, run_generation: UUID
    ) -> bool:
        assert operation_id == self.operation.id
        assert run_generation == self.operation.run_generation
        next(item for item in self.operation.items if item.id == item_id).status = "running"
        return True

    async def get_finding(self, finding_id: UUID) -> ConsistencyAuditFindingRecord | None:
        return self.findings.get(finding_id)

    async def finish_item(
        self,
        item_id: UUID,
        operation_id: UUID,
        run_generation: UUID,
        status: str,
        safe_message: str,
    ) -> bool:
        assert operation_id == self.operation.id
        assert run_generation == self.operation.run_generation
        item = next(item for item in self.operation.items if item.id == item_id)
        item.status = status
        item.safe_message = safe_message
        return True

    async def finalize(self, operation_id: UUID, run_generation: UUID) -> bool:
        assert run_generation == self.operation.run_generation
        return all(
            item.status in {"succeeded", "skipped", "not_repairable"}
            for item in self.operation.items
        )


def operation_for(
    kb_id: UUID, findings: list[ConsistencyAuditFindingRecord]
) -> tuple[ConsistencyRepairOperation, list[ConsistencyRepairItem]]:
    operation = ConsistencyRepairOperation(
        id=uuid4(),
        audit_id=uuid4(),
        knowledge_base_id=kb_id,
        status="queued",
        run_generation=uuid4(),
    )
    items = [
        ConsistencyRepairItem(
            id=uuid4(),
            operation_id=operation.id,
            finding_id=finding.id,
            finding_code=finding.code,
            entity_type=finding.entity_type,
            entity_id=finding.entity_id,
            status="pending",
            action=REPAIR_ACTIONS.get(finding.code, "manual_review"),
            safe_message="pending",
        )
        for finding in findings
    ]
    return operation, items


@pytest.mark.asyncio
async def test_executor_uses_parse_pipeline_and_post_repair_audit() -> None:
    kb_id = uuid4()
    finding = record("parsed_version_missing_chunks", kb_id)
    operation, items = operation_for(kb_id, [finding])
    audit = MutableAuditService(kb_id, [finding])
    parser = FakeParser(audit, remove=True)
    repository = ExecutorRepository(operation, items, [finding])
    executor = ConsistencyRepairExecutor(
        cast(AsyncSession, object()),
        cast(ConsistencyAuditService, audit),
        parser,
        cast(Any, object()),
        cast(Any, object()),
        cast(QdrantGateway, object()),
        cast(LocalArchiveStorage, object()),
        cast(ConsistencyAuditRepository, object()),
        cast(ConsistencyRepairRepository, repository),
    )

    assert await executor.run(operation.id, operation.run_generation) is True
    assert parser.calls == [(UUID(finding.entity_id), True, False)]
    assert items[0].status == "succeeded"
    assert audit.calls == 2


@pytest.mark.asyncio
async def test_executor_reports_verification_failed_instead_of_false_success() -> None:
    kb_id = uuid4()
    finding = record("chunk_count_mismatch", kb_id)
    operation, items = operation_for(kb_id, [finding])
    audit = MutableAuditService(kb_id, [finding])
    repository = ExecutorRepository(operation, items, [finding])
    executor = ConsistencyRepairExecutor(
        cast(AsyncSession, object()),
        cast(ConsistencyAuditService, audit),
        FakeParser(audit, remove=False),
        cast(Any, object()),
        cast(Any, object()),
        cast(QdrantGateway, object()),
        cast(LocalArchiveStorage, object()),
        cast(ConsistencyAuditRepository, object()),
        cast(ConsistencyRepairRepository, repository),
    )
    assert await executor.run(operation.id, operation.run_generation) is False
    assert items[0].status == "verification_failed"


@pytest.mark.asyncio
async def test_executor_stale_finding_skips_without_calling_pipeline() -> None:
    kb_id = uuid4()
    finding = record("parsed_version_missing_chunks", kb_id)
    operation, items = operation_for(kb_id, [finding])
    audit = MutableAuditService(kb_id, [])
    parser = FakeParser(audit, remove=True)
    repository = ExecutorRepository(operation, items, [finding])
    executor = ConsistencyRepairExecutor(
        cast(AsyncSession, object()),
        cast(ConsistencyAuditService, audit),
        parser,
        cast(Any, object()),
        cast(Any, object()),
        cast(QdrantGateway, object()),
        cast(LocalArchiveStorage, object()),
        cast(ConsistencyAuditRepository, object()),
        cast(ConsistencyRepairRepository, repository),
    )
    assert await executor.run(operation.id, operation.run_generation) is True
    assert items[0].status == "skipped"
    assert items[0].safe_message == "finding_no_longer_present"
    assert parser.calls == []


@pytest.mark.asyncio
async def test_multiple_items_keep_success_when_another_fails_and_third_is_stale() -> None:
    kb_id = uuid4()
    success = record("parsed_version_missing_chunks", kb_id)
    failure = record("chunk_count_mismatch", kb_id)
    stale = record("parsed_version_missing_chunks", kb_id)
    operation, items = operation_for(kb_id, [success, failure, stale])
    audit = MutableAuditService(kb_id, [success, failure])
    parser = SelectiveParser(audit, UUID(failure.entity_id))
    repository = ExecutorRepository(operation, items, [success, failure, stale])
    executor = ConsistencyRepairExecutor(
        cast(AsyncSession, object()),
        cast(ConsistencyAuditService, audit),
        parser,
        cast(Any, object()),
        cast(Any, object()),
        cast(QdrantGateway, object()),
        cast(LocalArchiveStorage, object()),
        cast(ConsistencyAuditRepository, object()),
        cast(ConsistencyRepairRepository, repository),
    )
    assert await executor.run(operation.id, operation.run_generation) is False
    assert [item.status for item in items] == ["succeeded", "failed", "skipped"]


@pytest.mark.asyncio
async def test_source_damage_blocks_document_index_repair() -> None:
    kb_id, version_id, document_id = uuid4(), uuid4(), uuid4()
    finding = record("latest_index_generation_missing", kb_id, version_id)
    damage = record("document_file_hash_mismatch", kb_id, version_id)
    operation, items = operation_for(kb_id, [finding])
    audit = MutableAuditService(kb_id, [finding, damage])
    indexer = FakeDocumentIndexer(audit)
    repository = ExecutorRepository(operation, items, [finding])
    executor = ConsistencyRepairExecutor(
        cast(AsyncSession, object()),
        cast(ConsistencyAuditService, audit),
        cast(Any, object()),
        indexer,
        cast(Any, object()),
        cast(QdrantGateway, object()),
        cast(LocalArchiveStorage, object()),
        cast(
            ConsistencyAuditRepository,
            SnapshotRepository(
                [
                    SimpleNamespace(
                        version_id=version_id,
                        document_id=document_id,
                        version_number=1,
                    )
                ]
            ),
        ),
        cast(ConsistencyRepairRepository, repository),
    )

    assert await executor.run(operation.id, operation.run_generation)
    assert items[0].status == "not_repairable"
    assert indexer.calls == []


@pytest.mark.asyncio
async def test_source_change_during_document_index_repair_fails_verification() -> None:
    kb_id, version_id, document_id = uuid4(), uuid4(), uuid4()
    finding = record("latest_index_generation_missing", kb_id, version_id)
    damage = record("document_file_hash_mismatch", kb_id, version_id)
    operation, items = operation_for(kb_id, [finding])
    audit = MutableAuditService(kb_id, [finding])
    indexer = FakeDocumentIndexer(audit, replacement_findings=[damage])
    repository = ExecutorRepository(operation, items, [finding])
    executor = ConsistencyRepairExecutor(
        cast(AsyncSession, object()),
        cast(ConsistencyAuditService, audit),
        cast(Any, object()),
        indexer,
        cast(Any, object()),
        cast(QdrantGateway, object()),
        cast(LocalArchiveStorage, object()),
        cast(
            ConsistencyAuditRepository,
            SnapshotRepository(
                [
                    SimpleNamespace(
                        version_id=version_id,
                        document_id=document_id,
                        version_number=2,
                    )
                ]
            ),
        ),
        cast(ConsistencyRepairRepository, repository),
    )

    assert not await executor.run(operation.id, operation.run_generation)
    assert items[0].status == "verification_failed"
    assert indexer.calls == [(version_id, True)]


@pytest.mark.asyncio
async def test_repair_reports_skip_when_latest_version_changes_inside_index_pipeline() -> None:
    kb_id, document_id, v2_id, v3_id = uuid4(), uuid4(), uuid4(), uuid4()
    finding = record("latest_index_generation_missing", kb_id, v2_id)
    operation, items = operation_for(kb_id, [finding])
    audit = MutableAuditService(kb_id, [finding])
    indexer = FakeDocumentIndexer(audit, result=False)
    repository = ExecutorRepository(operation, items, [finding])
    v2 = SimpleNamespace(version_id=v2_id, document_id=document_id, version_number=2)
    v3 = SimpleNamespace(version_id=v3_id, document_id=document_id, version_number=3)
    executor = ConsistencyRepairExecutor(
        cast(AsyncSession, object()),
        cast(ConsistencyAuditService, audit),
        cast(Any, object()),
        indexer,
        cast(Any, object()),
        cast(QdrantGateway, object()),
        cast(LocalArchiveStorage, object()),
        cast(
            ConsistencyAuditRepository,
            SequentialSnapshotRepository([[v2], [v2, v3]]),
        ),
        cast(ConsistencyRepairRepository, repository),
    )

    assert await executor.run(operation.id, operation.run_generation)
    assert items[0].status == "skipped"
    assert indexer.calls == [(v2_id, True)]


class JournalAuditRepository:
    def __init__(self, kb_id: UUID) -> None:
        self.kb_id = kb_id

    async def load_snapshot(self, knowledge_base_id: UUID | None) -> ConsistencyAuditSnapshot:
        return ConsistencyAuditSnapshot(frozenset({self.kb_id}), (), (), ())


class JournalArchive:
    def __init__(self, path: Path, journal: object) -> None:
        self.path = path
        self.journal = journal
        self.finished = False

    async def inspect_restore_journals(self) -> object:
        return SimpleNamespace(valid=[(self.path, self.journal)])

    async def final_restore_is_complete(self, journal: object) -> bool:
        return True

    async def finish_recovered_restore(self, path: Path, journal: object) -> None:
        assert path == self.path and journal is self.journal
        self.finished = True


@pytest.mark.asyncio
async def test_journal_cleanup_reuses_validated_recovery_primitive(tmp_path: Path) -> None:
    kb_id, operation_id = uuid4(), uuid4()
    finding = record("restore_journal_cleanup_pending", kb_id, operation_id)
    finding.entity_type = "restore_journal"
    journal = SimpleNamespace(operation_id=operation_id, knowledge_base_id=kb_id)
    archive = JournalArchive(tmp_path / "journal.json", journal)
    executor = ConsistencyRepairExecutor(
        cast(AsyncSession, object()),
        cast(ConsistencyAuditService, object()),
        cast(Any, object()),
        cast(Any, object()),
        cast(Any, object()),
        cast(QdrantGateway, object()),
        cast(LocalArchiveStorage, archive),
        cast(ConsistencyAuditRepository, JournalAuditRepository(kb_id)),
        cast(ConsistencyRepairRepository, object()),
    )
    result = await executor._repair_restore_journal_cleanup_pending(finding)
    assert result.status == "succeeded"
    assert archive.finished is True
