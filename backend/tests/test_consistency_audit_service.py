import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from app.core.config import Settings
from app.indexing import QdrantAuditPage, QdrantAuditPoint, QdrantGateway, VectorIndexError
from app.parsing.chunker import DeterministicChunker
from app.repositories.consistency_audit import (
    AuditDocumentVersion,
    AuditKnowledgeEntry,
    ConsistencyAuditRepository,
    ConsistencyAuditSnapshot,
)
from app.schemas.consistency_audit import ConsistencyAuditResponse
from app.schemas.knowledge_base_archive import (
    KnowledgeBaseRestoreJournal,
    RestoreJournalFile,
)
from app.services.consistency_audit import ConsistencyAuditService
from app.services.knowledge_entry_indexing import KnowledgeIndexSource, build_knowledge_blocks
from app.storage.archive import LocalArchiveStorage, archive_limits_from_settings
from app.storage.local import LocalFileStorage


class FakeAuditRepository:
    def __init__(self, snapshot: ConsistencyAuditSnapshot) -> None:
        self.snapshot = snapshot
        self.saved_reports: list[ConsistencyAuditResponse] = []

    async def knowledge_base_exists(self, knowledge_base_id: UUID) -> bool:
        return knowledge_base_id in self.snapshot.knowledge_base_ids

    async def load_snapshot(self, knowledge_base_id: UUID | None) -> ConsistencyAuditSnapshot:
        if knowledge_base_id is None:
            return self.snapshot
        return ConsistencyAuditSnapshot(
            frozenset({knowledge_base_id}),
            tuple(
                item
                for item in self.snapshot.versions
                if item.knowledge_base_id == knowledge_base_id
            ),
            tuple(
                item
                for item in self.snapshot.knowledge_entries
                if item.knowledge_base_id == knowledge_base_id
            ),
            (),
        )

    async def save_report(self, report: ConsistencyAuditResponse) -> None:
        self.saved_reports.append(report)


class FakeAuditGateway:
    def __init__(self, points: list[QdrantAuditPoint], *, unavailable: bool = False) -> None:
        self.points = points
        self.unavailable = unavailable
        self.calls: list[tuple[UUID | None, object, int]] = []

    async def audit_payload_page(
        self, *, knowledge_base_id: UUID | None, offset: object, limit: int
    ) -> QdrantAuditPage:
        self.calls.append((knowledge_base_id, offset, limit))
        if self.unavailable:
            raise VectorIndexError("unavailable")
        filtered = [
            point
            for point in self.points
            if knowledge_base_id is None
            or point.payload.get("knowledge_base_id") == str(knowledge_base_id)
        ]
        start = int(offset or 0)
        end = min(start + limit, len(filtered))
        return QdrantAuditPage(filtered[start:end], end if end < len(filtered) else None)


def settings(root: Path, *, page_size: int = 2) -> Settings:
    return Settings(
        app_env="test",
        document_storage_root=root,
        consistency_audit_qdrant_page_size=page_size,
    )


def tree_names(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


def knowledge_expected_count(config: Settings, entry_id: UUID, kb_id: UUID) -> int:
    source = KnowledgeIndexSource(
        entry_id,
        kb_id,
        "How?",
        None,
        None,
        "Use the maintained solution.",
        (),
        ("audit",),
        datetime(2026, 8, 17, tzinfo=UTC),
    )
    chunker = DeterministicChunker(
        max_chars=config.document_chunk_max_chars,
        overlap_chars=config.document_chunk_overlap_chars,
    )
    return len(chunker.chunk(build_knowledge_blocks(source)))


def build_fixture(
    root: Path,
) -> tuple[
    Settings,
    ConsistencyAuditSnapshot,
    list[QdrantAuditPoint],
    dict[str, UUID],
]:
    config = settings(root)
    kb_id, document_id = uuid4(), uuid4()
    historical_id, latest_id = uuid4(), uuid4()
    historical_generation, latest_generation = uuid4(), uuid4()
    entry_id, entry_generation = uuid4(), uuid4()
    now = datetime(2026, 8, 17, tzinfo=UTC)
    storage = LocalFileStorage(root, max_size=1024, chunk_size=16)

    versions: list[AuditDocumentVersion] = []
    for version_id, number, generation in (
        (historical_id, 1, None),
        (latest_id, 2, latest_generation),
    ):
        content = f"version-{number}".encode()
        relative = storage.final_relative_path(kb_id, document_id, version_id, ".md")
        path = storage.resolve_relative(relative, must_exist=False)
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        versions.append(
            AuditDocumentVersion(
                kb_id,
                document_id,
                version_id,
                number,
                hashlib.sha256(content).hexdigest(),
                len(content),
                ".md",
                relative,
                "succeeded",
                1,
                "pending" if generation is None else "succeeded",
                generation,
                None,
                0 if generation is None else 1,
                now,
                None if generation is None else now,
                1,
            )
        )
    expected_knowledge = knowledge_expected_count(config, entry_id, kb_id)
    entry = AuditKnowledgeEntry(
        kb_id,
        entry_id,
        "verified",
        "succeeded",
        entry_generation,
        None,
        expected_knowledge,
        now,
        now,
        now,
        "How?",
        None,
        None,
        "Use the maintained solution.",
        (),
        ("audit",),
    )
    points = [
        QdrantAuditPoint(
            str(uuid4()),
            {
                "source_type": "document",
                "knowledge_base_id": str(kb_id),
                "document_id": str(document_id),
                "document_version_id": str(latest_id),
                "index_generation": str(latest_generation),
            },
        ),
        *[
            QdrantAuditPoint(
                str(uuid4()),
                {
                    "source_type": "knowledge_entry",
                    "knowledge_base_id": str(kb_id),
                    "knowledge_entry_id": str(entry_id),
                    "index_generation": str(entry_generation),
                },
            )
            for _ in range(expected_knowledge)
        ],
    ]
    snapshot = ConsistencyAuditSnapshot(frozenset({kb_id}), tuple(versions), (entry,), ())
    ids = {
        "kb": kb_id,
        "document": document_id,
        "historical": historical_id,
        "latest": latest_id,
        "historical_generation": historical_generation,
        "latest_generation": latest_generation,
        "entry": entry_id,
        "entry_generation": entry_generation,
    }
    return config, snapshot, points, ids


def service(
    config: Settings,
    snapshot: ConsistencyAuditSnapshot,
    points: list[QdrantAuditPoint],
    *,
    unavailable: bool = False,
) -> ConsistencyAuditService:
    storage = LocalFileStorage(
        config.document_storage_root,
        max_size=config.document_max_file_size_bytes,
        chunk_size=config.document_upload_chunk_size_bytes,
    )
    archive = LocalArchiveStorage(
        config.document_storage_root, archive_limits_from_settings(config)
    )
    return ConsistencyAuditService(
        config,
        cast(ConsistencyAuditRepository, FakeAuditRepository(snapshot)),
        storage,
        archive,
        cast(QdrantGateway, FakeAuditGateway(points, unavailable=unavailable)),
    )


@pytest.mark.asyncio
async def test_healthy_knowledge_base_is_read_only_and_uses_paged_payload_scan(
    tmp_path: Path,
) -> None:
    config, snapshot, points, ids = build_fixture(tmp_path)
    gateway = FakeAuditGateway(points)
    audit = ConsistencyAuditService(
        config,
        cast(ConsistencyAuditRepository, FakeAuditRepository(snapshot)),
        LocalFileStorage(tmp_path, max_size=1024, chunk_size=16),
        LocalArchiveStorage(tmp_path, archive_limits_from_settings(config)),
        cast(QdrantGateway, gateway),
    )
    before = await asyncio.to_thread(tree_names, tmp_path)

    report = await audit.audit_knowledge_base(ids["kb"])
    after = await asyncio.to_thread(tree_names, tmp_path)

    assert report.status == "completed"
    assert report.summary.healthy is True
    assert report.findings == []
    assert after == before
    assert len(gateway.calls) >= 2
    assert all(call[2] == config.consistency_audit_qdrant_page_size for call in gateway.calls)


@pytest.mark.asyncio
async def test_injected_storage_chunk_index_and_qdrant_faults_are_classified(
    tmp_path: Path,
) -> None:
    config, snapshot, points, ids = build_fixture(tmp_path)
    versions = list(snapshot.versions)
    historical, latest = versions
    historical_path = tmp_path / Path(*Path(historical.storage_path).parts)
    historical_path.unlink()
    latest_path = tmp_path / Path(*Path(latest.storage_path).parts)
    latest_path.write_bytes(b"VERSION-2")
    versions[1] = AuditDocumentVersion(
        latest.knowledge_base_id,
        latest.document_id,
        latest.version_id,
        latest.version_number,
        latest.content_hash,
        latest.file_size,
        latest.extension,
        latest.storage_path,
        latest.parse_status,
        latest.declared_chunk_count,
        latest.index_status,
        latest.active_generation,
        latest.attempt_generation,
        latest.indexed_chunk_count,
        latest.parsed_at,
        latest.indexed_at,
        0,
    )
    historical_generation = ids["historical_generation"]
    versions[0] = AuditDocumentVersion(
        historical.knowledge_base_id,
        historical.document_id,
        historical.version_id,
        historical.version_number,
        historical.content_hash,
        historical.file_size,
        historical.extension,
        historical.storage_path,
        historical.parse_status,
        historical.declared_chunk_count,
        "succeeded",
        historical_generation,
        None,
        1,
        historical.parsed_at,
        historical.parsed_at,
        historical.actual_chunk_count,
    )
    entry = snapshot.knowledge_entries[0]
    unverified_id, unverified_generation = uuid4(), uuid4()
    unverified = AuditKnowledgeEntry(
        entry.knowledge_base_id,
        unverified_id,
        "unverified",
        "succeeded",
        unverified_generation,
        None,
        1,
        entry.indexed_at,
        entry.updated_at,
        entry.updated_at,
        "Unsafe?",
        None,
        None,
        "Do not index.",
        (),
        (),
    )
    orphan_kb = uuid4()
    fault_points = [
        QdrantAuditPoint(
            str(uuid4()),
            {
                "source_type": "document",
                "knowledge_base_id": str(ids["kb"]),
                "document_id": str(ids["document"]),
                "document_version_id": str(ids["historical"]),
                "index_generation": str(historical_generation),
            },
        ),
        QdrantAuditPoint(
            str(uuid4()),
            {
                "source_type": "knowledge_entry",
                "knowledge_base_id": str(ids["kb"]),
                "knowledge_entry_id": str(unverified_id),
                "index_generation": str(unverified_generation),
            },
        ),
        QdrantAuditPoint(
            str(uuid4()),
            {
                "source_type": "document",
                "knowledge_base_id": str(orphan_kb),
                "document_id": str(uuid4()),
                "document_version_id": str(uuid4()),
                "index_generation": str(uuid4()),
            },
        ),
        QdrantAuditPoint(str(uuid4()), {"source_type": "document"}),
    ]
    damaged = ConsistencyAuditSnapshot(
        snapshot.knowledge_base_ids,
        tuple(versions),
        (entry, unverified),
        (),
    )

    report = await service(config, damaged, fault_points).audit_all()
    codes = {item.code for item in report.findings}

    assert {
        "document_file_missing",
        "document_file_hash_mismatch",
        "parsed_version_missing_chunks",
        "chunk_count_mismatch",
        "historical_generation_active",
        "active_index_points_missing",
        "active_index_point_count_mismatch",
        "verified_knowledge_index_missing",
        "knowledge_index_point_count_mismatch",
        "non_verified_knowledge_active",
        "stale_qdrant_generation",
        "stale_knowledge_generation",
        "orphan_qdrant_point",
        "invalid_qdrant_payload",
    } <= codes
    assert report.summary.critical_count >= 2
    assert report.summary.error_count >= 1


@pytest.mark.asyncio
async def test_qdrant_unavailable_returns_partial_storage_and_database_report(
    tmp_path: Path,
) -> None:
    config, snapshot, points, ids = build_fixture(tmp_path)
    latest = snapshot.versions[-1]
    path = tmp_path / Path(*Path(latest.storage_path).parts)
    path.write_bytes(b"corrupted")

    report = await service(config, snapshot, points, unavailable=True).audit_knowledge_base(
        ids["kb"]
    )

    assert report.status == "partial"
    assert {item.code for item in report.findings} >= {
        "document_file_hash_mismatch",
        "qdrant_audit_unavailable",
    }


@pytest.mark.asyncio
async def test_restore_journal_and_cross_kb_residue_are_reported_without_recovery(
    tmp_path: Path,
) -> None:
    config, snapshot, points, _ = build_fixture(tmp_path)
    existing_kb = next(iter(snapshot.knowledge_base_ids))
    missing_kb = uuid4()
    archive = LocalArchiveStorage(tmp_path, archive_limits_from_settings(config))
    complete_version = snapshot.versions[-1]
    complete_journal = KnowledgeBaseRestoreJournal(
        operation_id=uuid4(),
        knowledge_base_id=existing_kb,
        staging_path="",
        final_path=str(existing_kb),
        promoted=True,
        created_at=datetime.now(UTC),
        files=[
            RestoreJournalFile(
                document_version_id=complete_version.version_id,
                path=complete_version.storage_path,
                size=complete_version.file_size,
                sha256=complete_version.content_hash,
            )
        ],
    )
    complete_journal = complete_journal.model_copy(
        update={"staging_path": (f".restore-tmp/{complete_journal.operation_id}/{existing_kb}")}
    )
    absent_journal = KnowledgeBaseRestoreJournal(
        operation_id=uuid4(),
        knowledge_base_id=missing_kb,
        staging_path="",
        final_path=str(missing_kb),
        promoted=False,
        created_at=datetime.now(UTC),
        files=[],
    )
    absent_journal = absent_journal.model_copy(
        update={"staging_path": f".restore-tmp/{absent_journal.operation_id}/{missing_kb}"}
    )
    incomplete_operation = uuid4()
    incomplete_journal = KnowledgeBaseRestoreJournal(
        operation_id=incomplete_operation,
        knowledge_base_id=existing_kb,
        staging_path=f".restore-tmp/{incomplete_operation}/{existing_kb}",
        final_path=str(existing_kb),
        promoted=True,
        created_at=datetime.now(UTC),
        files=[
            RestoreJournalFile(
                document_version_id=uuid4(),
                path=f"{existing_kb}/{uuid4()}/{uuid4()}/content.md",
                size=1,
                sha256="0" * 64,
            )
        ],
    )
    for journal in (complete_journal, absent_journal, incomplete_journal):
        (archive.journal_root / f"{journal.operation_id}.json").write_text(
            journal.model_dump_json(), encoding="utf-8"
        )
    (archive.journal_root / "forged.json").write_text("{}", encoding="utf-8")
    (archive.restore_root / "staging-residue").mkdir()
    (tmp_path / "unknown-storage").mkdir()

    report = await ConsistencyAuditService(
        config,
        cast(ConsistencyAuditRepository, FakeAuditRepository(snapshot)),
        LocalFileStorage(tmp_path, max_size=1024, chunk_size=16),
        archive,
        cast(QdrantGateway, FakeAuditGateway(points)),
    ).audit_all()
    codes = [item.code for item in report.findings]

    assert codes.count("restore_journal_cleanup_pending") == 2
    assert "restore_journal_invalid" in codes
    assert codes.count("restore_journal_inconsistent") >= 2
    assert "suspicious_storage_entry" in codes
    assert (archive.journal_root / "forged.json").exists()
    assert (archive.restore_root / "staging-residue").exists()


@pytest.mark.asyncio
async def test_global_scan_aggregates_multiple_healthy_and_one_damaged_knowledge_base(
    tmp_path: Path,
) -> None:
    fixtures = [build_fixture(tmp_path) for _ in range(3)]
    config = fixtures[0][0]
    snapshots = [item[1] for item in fixtures]
    points = [point for item in fixtures for point in item[2]]
    damaged_kb = fixtures[-1][3]["kb"]
    damaged_version = snapshots[-1].versions[-1]
    damaged_path = tmp_path / Path(*Path(damaged_version.storage_path).parts)
    original = damaged_path.read_bytes()
    damaged_path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    combined = ConsistencyAuditSnapshot(
        frozenset().union(*(item.knowledge_base_ids for item in snapshots)),
        tuple(version for item in snapshots for version in item.versions),
        tuple(entry for item in snapshots for entry in item.knowledge_entries),
        (),
    )

    report = await service(config, combined, points).audit_all()

    critical = [item for item in report.findings if item.severity == "CRITICAL"]
    assert {item.code for item in critical} == {"document_file_hash_mismatch"}
    assert {item.knowledge_base_id for item in critical} == {damaged_kb}
    assert report.summary.critical_count == 1
