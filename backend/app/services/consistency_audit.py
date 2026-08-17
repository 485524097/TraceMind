import asyncio
import hashlib
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.core.config import Settings
from app.indexing import QdrantGateway, VectorIndexError
from app.parsing.chunker import DeterministicChunker
from app.repositories.consistency_audit import (
    AuditDocumentVersion,
    AuditKnowledgeEntry,
    ConsistencyAuditRepository,
    ConsistencyAuditSnapshot,
)
from app.schemas.consistency_audit import (
    AuditDetailValue,
    AuditScope,
    AuditSeverity,
    ConsistencyAuditFinding,
    ConsistencyAuditResponse,
    ConsistencyAuditSummary,
)
from app.services.exceptions import KnowledgeBaseNotFoundError
from app.services.knowledge_entry_indexing import (
    KnowledgeIndexSource,
    build_knowledge_blocks,
)
from app.storage.archive import LocalArchiveStorage
from app.storage.local import LocalFileStorage


class ConsistencyAuditService:
    """Compare source and derived state without modifying either side."""

    def __init__(
        self,
        settings: Settings,
        repository: ConsistencyAuditRepository,
        document_storage: LocalFileStorage,
        archive_storage: LocalArchiveStorage,
        gateway: QdrantGateway,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.document_storage = document_storage
        self.archive_storage = archive_storage
        self.gateway = gateway
        self.knowledge_chunker = DeterministicChunker(
            max_chars=settings.document_chunk_max_chars,
            overlap_chars=settings.document_chunk_overlap_chars,
        )

    async def audit_knowledge_base(self, knowledge_base_id: UUID) -> ConsistencyAuditResponse:
        if not await self.repository.knowledge_base_exists(knowledge_base_id):
            raise KnowledgeBaseNotFoundError(knowledge_base_id)
        return await self._audit("knowledge_base", knowledge_base_id, persist=True)

    async def inspect_knowledge_base(self, knowledge_base_id: UUID) -> ConsistencyAuditResponse:
        """Revalidate current facts without persisting a new audit snapshot."""
        if not await self.repository.knowledge_base_exists(knowledge_base_id):
            raise KnowledgeBaseNotFoundError(knowledge_base_id)
        return await self._audit("knowledge_base", knowledge_base_id, persist=False)

    async def audit_all(self) -> ConsistencyAuditResponse:
        return await self._audit("global", None, persist=True)

    async def _audit(
        self, scope: AuditScope, knowledge_base_id: UUID | None, *, persist: bool
    ) -> ConsistencyAuditResponse:
        audit_id = uuid4()
        started_at = datetime.now(UTC)
        findings: list[ConsistencyAuditFinding] = []
        partial = False
        snapshot = await self.repository.load_snapshot(knowledge_base_id)

        partial |= await self._audit_storage(snapshot, findings)
        self._audit_chunks(snapshot, findings)
        latest_by_document = self._latest_versions(snapshot.versions)
        self._audit_document_index_metadata(snapshot, latest_by_document, findings)
        self._audit_knowledge_metadata(snapshot, findings)
        qdrant_available, point_counts = await self._audit_qdrant(
            snapshot,
            knowledge_base_id,
            latest_by_document,
            findings,
        )
        if qdrant_available:
            self._audit_document_point_counts(latest_by_document.values(), point_counts, findings)
            self._audit_knowledge_point_counts(snapshot.knowledge_entries, point_counts, findings)
        else:
            partial = True
        partial |= await self._audit_journals(snapshot, knowledge_base_id, findings)
        if scope == "global":
            partial |= await self._audit_suspicious_storage(snapshot, findings)

        completed_at = datetime.now(UTC)
        summary = self._summary(findings)
        report = ConsistencyAuditResponse(
            audit_id=audit_id,
            scope=scope,
            status="partial" if partial else "completed",
            knowledge_base_id=knowledge_base_id,
            started_at=started_at,
            completed_at=completed_at,
            summary=summary,
            findings=findings,
        )
        if persist:
            await self.repository.save_report(report)
        return report

    async def _audit_storage(
        self,
        snapshot: ConsistencyAuditSnapshot,
        findings: list[ConsistencyAuditFinding],
    ) -> bool:
        partial = False
        for version in snapshot.versions:
            expected_path = self.document_storage.final_relative_path(
                version.knowledge_base_id,
                version.document_id,
                version.version_id,
                version.extension,
            )
            if version.storage_path != expected_path:
                self._add(
                    findings,
                    "document_storage_path_invalid",
                    "CRITICAL",
                    "document_version",
                    version.version_id,
                    version.knowledge_base_id,
                    "Document storage path does not match the current storage contract.",
                    expected_path=expected_path,
                    stored_path=version.storage_path,
                )
                continue
            try:
                path = self.document_storage.resolve_relative(
                    version.storage_path, must_exist=False
                )
                if path.is_symlink():
                    self._add(
                        findings,
                        "document_file_not_regular",
                        "CRITICAL",
                        "document_version",
                        version.version_id,
                        version.knowledge_base_id,
                        "Document source is a symlink or special file.",
                        file_kind="symlink",
                    )
                    continue
                if not path.exists():
                    self._add(
                        findings,
                        "document_file_missing",
                        "CRITICAL",
                        "document_version",
                        version.version_id,
                        version.knowledge_base_id,
                        "Document source file is missing.",
                    )
                    continue
                if not path.is_file():
                    self._add(
                        findings,
                        "document_file_not_regular",
                        "CRITICAL",
                        "document_version",
                        version.version_id,
                        version.knowledge_base_id,
                        "Document source is not a regular file.",
                        file_kind="special",
                    )
                    continue
                size, digest = await asyncio.to_thread(self._hash_file, path)
                if size != version.file_size:
                    self._add(
                        findings,
                        "document_file_size_mismatch",
                        "CRITICAL",
                        "document_version",
                        version.version_id,
                        version.knowledge_base_id,
                        "Document source size does not match PostgreSQL.",
                        expected_size=version.file_size,
                        actual_size=size,
                    )
                if digest != version.content_hash:
                    self._add(
                        findings,
                        "document_file_hash_mismatch",
                        "CRITICAL",
                        "document_version",
                        version.version_id,
                        version.knowledge_base_id,
                        "Document source hash does not match PostgreSQL.",
                        expected_hash=version.content_hash,
                        actual_hash=digest,
                    )
            except OSError:
                partial = True
                self._add(
                    findings,
                    "storage_audit_unavailable",
                    "WARNING",
                    "document_version",
                    version.version_id,
                    version.knowledge_base_id,
                    "Document storage metadata could not be read.",
                )
        return partial

    def _hash_file(self, path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            while chunk := handle.read(self.settings.document_upload_chunk_size_bytes):
                size += len(chunk)
                digest.update(chunk)
        return size, digest.hexdigest()

    def _audit_chunks(
        self,
        snapshot: ConsistencyAuditSnapshot,
        findings: list[ConsistencyAuditFinding],
    ) -> None:
        for version in snapshot.versions:
            if version.parse_status == "succeeded" and (
                version.declared_chunk_count == 0 or version.actual_chunk_count == 0
            ):
                self._add(
                    findings,
                    "parsed_version_missing_chunks",
                    "ERROR",
                    "document_version",
                    version.version_id,
                    version.knowledge_base_id,
                    "Parsed document version has no declared or persisted chunks.",
                    declared_chunk_count=version.declared_chunk_count,
                    actual_chunk_count=version.actual_chunk_count,
                )
            if version.declared_chunk_count != version.actual_chunk_count:
                self._add(
                    findings,
                    "chunk_count_mismatch",
                    "ERROR",
                    "document_version",
                    version.version_id,
                    version.knowledge_base_id,
                    "Document chunk count does not match PostgreSQL metadata.",
                    declared_chunk_count=version.declared_chunk_count,
                    actual_chunk_count=version.actual_chunk_count,
                )
            if version.parse_status != "succeeded" and version.actual_chunk_count > 0:
                self._add(
                    findings,
                    "unexpected_chunks",
                    "WARNING",
                    "document_version",
                    version.version_id,
                    version.knowledge_base_id,
                    "Non-succeeded parse state still has document chunks.",
                    parse_status=version.parse_status,
                    actual_chunk_count=version.actual_chunk_count,
                )
        for version_id in snapshot.orphan_chunk_version_ids:
            self._add(
                findings,
                "orphan_document_chunk",
                "ERROR",
                "document_version",
                version_id,
                None,
                "Document chunks reference a missing document version.",
            )

    @staticmethod
    def _latest_versions(
        versions: tuple[AuditDocumentVersion, ...],
    ) -> dict[UUID, AuditDocumentVersion]:
        latest: dict[UUID, AuditDocumentVersion] = {}
        for version in versions:
            previous = latest.get(version.document_id)
            if previous is None or version.version_number > previous.version_number:
                latest[version.document_id] = version
        return latest

    def _audit_document_index_metadata(
        self,
        snapshot: ConsistencyAuditSnapshot,
        latest_by_document: dict[UUID, AuditDocumentVersion],
        findings: list[ConsistencyAuditFinding],
    ) -> None:
        latest_ids = {version.version_id for version in latest_by_document.values()}
        for version in snapshot.versions:
            usable = self._document_active_is_usable(version)
            if version.version_id in latest_ids and not usable:
                self._add(
                    findings,
                    "latest_index_generation_missing",
                    "ERROR",
                    "document_version",
                    version.version_id,
                    version.knowledge_base_id,
                    "Latest document version has no usable active index generation.",
                    index_status=version.index_status,
                )
            if version.version_id not in latest_ids and usable:
                self._add(
                    findings,
                    "historical_generation_active",
                    "WARNING",
                    "document_version",
                    version.version_id,
                    version.knowledge_base_id,
                    "Historical document version retains an active generation.",
                    index_generation=str(version.active_generation),
                )

    def _audit_knowledge_metadata(
        self,
        snapshot: ConsistencyAuditSnapshot,
        findings: list[ConsistencyAuditFinding],
    ) -> None:
        for entry in snapshot.knowledge_entries:
            usable = self._knowledge_active_is_usable(entry)
            if entry.validation_status == "verified" and not usable:
                self._add(
                    findings,
                    "verified_knowledge_index_missing",
                    "ERROR",
                    "knowledge_entry",
                    entry.entry_id,
                    entry.knowledge_base_id,
                    "Verified knowledge has no usable active index generation.",
                    index_status=entry.index_status,
                )
            if entry.validation_status != "verified" and entry.active_generation is not None:
                self._add(
                    findings,
                    "non_verified_knowledge_active",
                    "WARNING",
                    "knowledge_entry",
                    entry.entry_id,
                    entry.knowledge_base_id,
                    "Non-verified knowledge retains an active generation.",
                    validation_status=entry.validation_status,
                    index_generation=str(entry.active_generation),
                )

    async def _audit_qdrant(
        self,
        snapshot: ConsistencyAuditSnapshot,
        knowledge_base_id: UUID | None,
        latest_by_document: dict[UUID, AuditDocumentVersion],
        findings: list[ConsistencyAuditFinding],
    ) -> tuple[bool, dict[UUID, int]]:
        versions = {item.version_id: item for item in snapshot.versions}
        documents = {item.document_id: item.knowledge_base_id for item in snapshot.versions}
        entries = {item.entry_id: item for item in snapshot.knowledge_entries}
        active_document_owners = {
            item.active_generation: item.version_id
            for item in latest_by_document.values()
            if self._document_active_is_usable(item) and item.active_generation is not None
        }
        active_knowledge_owners = {
            item.active_generation: item.entry_id
            for item in snapshot.knowledge_entries
            if self._knowledge_active_is_usable(item) and item.active_generation is not None
        }
        valid_attempts = {
            item.attempt_generation
            for item in snapshot.versions
            if item.index_status == "processing" and item.attempt_generation is not None
        } | {
            item.attempt_generation
            for item in snapshot.knowledge_entries
            if item.index_status == "processing" and item.attempt_generation is not None
        }
        point_counts: dict[UUID, int] = defaultdict(int)
        stale_seen: set[tuple[str, UUID, UUID]] = set()
        offset: Any = None
        try:
            while True:
                page = await self.gateway.audit_payload_page(
                    knowledge_base_id=knowledge_base_id,
                    offset=offset,
                    limit=self.settings.consistency_audit_qdrant_page_size,
                )
                for point in page.points:
                    parsed = self._parse_qdrant_payload(point.payload)
                    if parsed is None:
                        self._add(
                            findings,
                            "invalid_qdrant_payload",
                            "WARNING",
                            "qdrant_point",
                            point.point_id,
                            None,
                            "Qdrant point payload is malformed.",
                        )
                        continue
                    source_type, kb_id, entity_id, related_id, generation = parsed
                    if kb_id not in snapshot.knowledge_base_ids:
                        self._add(
                            findings,
                            "orphan_qdrant_point",
                            "WARNING",
                            "qdrant_point",
                            point.point_id,
                            kb_id,
                            "Qdrant point references a missing Knowledge Base.",
                            source_type=source_type,
                            source_entity_id=str(entity_id),
                            related_id=str(related_id),
                            index_generation=str(generation),
                        )
                        continue
                    if source_type == "document":
                        version = versions.get(related_id)
                        if (
                            version is None
                            or entity_id not in documents
                            or version.document_id != entity_id
                            or version.knowledge_base_id != kb_id
                        ):
                            self._add(
                                findings,
                                "orphan_qdrant_point",
                                "WARNING",
                                "qdrant_point",
                                point.point_id,
                                kb_id,
                                "Qdrant document point references a missing or unrelated entity.",
                                source_type=source_type,
                                document_id=str(entity_id),
                                document_version_id=str(related_id),
                                index_generation=str(generation),
                            )
                            continue
                        owner = active_document_owners.get(generation)
                        if owner is not None and owner != version.version_id:
                            self._add(
                                findings,
                                "active_generation_version_mismatch",
                                "ERROR",
                                "qdrant_point",
                                point.point_id,
                                kb_id,
                                "Active document generation payload references another version.",
                                index_generation=str(generation),
                            )
                            continue
                        if owner == version.version_id:
                            point_counts[generation] += 1
                            continue
                        if generation not in valid_attempts:
                            key = (source_type, version.version_id, generation)
                            if key not in stale_seen:
                                stale_seen.add(key)
                                self._add(
                                    findings,
                                    "stale_qdrant_generation",
                                    "WARNING",
                                    "document_version",
                                    version.version_id,
                                    kb_id,
                                    "Qdrant document generation is no longer active.",
                                    index_generation=str(generation),
                                )
                    else:
                        entry = entries.get(entity_id)
                        if entry is None or entry.knowledge_base_id != kb_id:
                            self._add(
                                findings,
                                "orphan_qdrant_point",
                                "WARNING",
                                "qdrant_point",
                                point.point_id,
                                kb_id,
                                "Qdrant knowledge point references a missing entity.",
                                source_type=source_type,
                                knowledge_entry_id=str(entity_id),
                                index_generation=str(generation),
                            )
                            continue
                        owner = active_knowledge_owners.get(generation)
                        if owner == entry.entry_id:
                            point_counts[generation] += 1
                            continue
                        if generation not in valid_attempts:
                            key = (source_type, entry.entry_id, generation)
                            if key not in stale_seen:
                                stale_seen.add(key)
                                self._add(
                                    findings,
                                    "stale_knowledge_generation",
                                    "WARNING",
                                    "knowledge_entry",
                                    entry.entry_id,
                                    kb_id,
                                    "Qdrant knowledge generation is no longer active.",
                                    index_generation=str(generation),
                                )
                if page.next_offset is None:
                    break
                offset = page.next_offset
        except VectorIndexError:
            self._add(
                findings,
                "qdrant_audit_unavailable",
                "WARNING",
                "subsystem",
                "qdrant",
                knowledge_base_id,
                "Qdrant metadata could not be audited; other audit results are available.",
            )
            return False, {}
        return True, dict(point_counts)

    @staticmethod
    def _parse_qdrant_payload(
        payload: dict[str, Any],
    ) -> tuple[str, UUID, UUID, UUID, UUID] | None:
        try:
            source_type = payload["source_type"]
            if source_type == "document":
                return (
                    source_type,
                    UUID(str(payload["knowledge_base_id"])),
                    UUID(str(payload["document_id"])),
                    UUID(str(payload["document_version_id"])),
                    UUID(str(payload["index_generation"])),
                )
            if source_type == "knowledge_entry":
                entry_id = UUID(str(payload["knowledge_entry_id"]))
                return (
                    source_type,
                    UUID(str(payload["knowledge_base_id"])),
                    entry_id,
                    entry_id,
                    UUID(str(payload["index_generation"])),
                )
        except (KeyError, TypeError, ValueError):
            return None
        return None

    def _audit_document_point_counts(
        self,
        latest_versions: Iterable[AuditDocumentVersion],
        point_counts: dict[UUID, int],
        findings: list[ConsistencyAuditFinding],
    ) -> None:
        for version in latest_versions:
            if not self._document_active_is_usable(version):
                continue
            assert version.active_generation is not None
            actual = point_counts.get(version.active_generation, 0)
            expected = version.actual_chunk_count
            if actual == 0:
                self._add(
                    findings,
                    "active_index_points_missing",
                    "ERROR",
                    "document_version",
                    version.version_id,
                    version.knowledge_base_id,
                    "Active document generation has no Qdrant points.",
                    index_generation=str(version.active_generation),
                    expected_point_count=expected,
                )
            if actual != expected or version.indexed_chunk_count != expected:
                self._add(
                    findings,
                    "active_index_point_count_mismatch",
                    "ERROR",
                    "document_version",
                    version.version_id,
                    version.knowledge_base_id,
                    "Active document point count does not match indexable chunks.",
                    expected_point_count=expected,
                    actual_point_count=actual,
                    indexed_chunk_count=version.indexed_chunk_count,
                )

    def _audit_knowledge_point_counts(
        self,
        entries: tuple[AuditKnowledgeEntry, ...],
        point_counts: dict[UUID, int],
        findings: list[ConsistencyAuditFinding],
    ) -> None:
        for entry in entries:
            if not self._knowledge_active_is_usable(entry):
                continue
            assert entry.active_generation is not None
            expected = self._expected_knowledge_point_count(entry)
            actual = point_counts.get(entry.active_generation, 0)
            if actual == 0:
                self._add(
                    findings,
                    "verified_knowledge_index_missing",
                    "ERROR",
                    "knowledge_entry",
                    entry.entry_id,
                    entry.knowledge_base_id,
                    "Verified knowledge active generation has no Qdrant points.",
                    index_generation=str(entry.active_generation),
                    expected_point_count=expected,
                )
            if actual != expected or entry.indexed_chunk_count != expected:
                self._add(
                    findings,
                    "knowledge_index_point_count_mismatch",
                    "ERROR",
                    "knowledge_entry",
                    entry.entry_id,
                    entry.knowledge_base_id,
                    "Knowledge point count does not match maintained retrieval content.",
                    expected_point_count=expected,
                    actual_point_count=actual,
                    indexed_chunk_count=entry.indexed_chunk_count,
                )

    def _expected_knowledge_point_count(self, entry: AuditKnowledgeEntry) -> int:
        source = KnowledgeIndexSource(
            entry.entry_id,
            entry.knowledge_base_id,
            entry.question,
            entry.background,
            entry.root_cause,
            entry.solution,
            entry.failed_attempts,
            entry.tags,
            entry.updated_at,
        )
        return len(self.knowledge_chunker.chunk(build_knowledge_blocks(source)))

    async def _audit_journals(
        self,
        snapshot: ConsistencyAuditSnapshot,
        knowledge_base_id: UUID | None,
        findings: list[ConsistencyAuditFinding],
    ) -> bool:
        try:
            inspection = await self.archive_storage.inspect_restore_journals()
        except OSError:
            self._add(
                findings,
                "restore_journal_audit_unavailable",
                "WARNING",
                "subsystem",
                "restore_journal",
                knowledge_base_id,
                "Restore journal metadata could not be audited.",
            )
            return True
        valid_operation_ids: set[str] = set()
        for path, journal in inspection.valid:
            if knowledge_base_id is not None and journal.knowledge_base_id != knowledge_base_id:
                continue
            valid_operation_ids.add(str(journal.operation_id))
            db_exists = journal.knowledge_base_id in snapshot.knowledge_base_ids
            try:
                final_complete = await self.archive_storage.final_restore_is_complete(journal)
            except OSError:
                final_complete = False
            if not db_exists or final_complete:
                self._add(
                    findings,
                    "restore_journal_cleanup_pending",
                    "WARNING",
                    "restore_journal",
                    journal.operation_id,
                    journal.knowledge_base_id,
                    "Restore journal cleanup is pending.",
                    database_exists=db_exists,
                    final_files_complete=final_complete,
                    promoted=journal.promoted,
                )
            else:
                self._add(
                    findings,
                    "restore_journal_inconsistent",
                    "ERROR",
                    "restore_journal",
                    journal.operation_id,
                    journal.knowledge_base_id,
                    "Restore journal and committed source files are inconsistent.",
                    database_exists=True,
                    final_files_complete=False,
                    promoted=journal.promoted,
                )
            _ = path
        if knowledge_base_id is None:
            for name in inspection.invalid_names:
                self._add(
                    findings,
                    "restore_journal_invalid",
                    "WARNING",
                    "restore_journal",
                    name,
                    None,
                    "Restore journal is invalid and was ignored by recovery validation.",
                )
            for name in inspection.staging_residue_names:
                if name not in valid_operation_ids:
                    self._add(
                        findings,
                        "restore_journal_inconsistent",
                        "WARNING",
                        "restore_staging",
                        name,
                        None,
                        "Restore staging residue has no valid journal.",
                        reason="staging_residue",
                    )
        return False

    async def _audit_suspicious_storage(
        self,
        snapshot: ConsistencyAuditSnapshot,
        findings: list[ConsistencyAuditFinding],
    ) -> bool:
        if not self.document_storage.root.exists():
            return False
        try:
            entries = await asyncio.to_thread(
                lambda: sorted(self.document_storage.root.iterdir(), key=lambda item: item.name)
            )
        except OSError:
            self._add(
                findings,
                "storage_audit_unavailable",
                "WARNING",
                "subsystem",
                "document_storage",
                None,
                "Document storage root could not be scanned.",
            )
            return True
        known = {str(value) for value in snapshot.knowledge_base_ids}
        ignored = {".upload-tmp", ".trash", ".archive-tmp", ".restore-tmp"}
        for entry in entries:
            if entry.name in ignored or entry.name in known:
                continue
            self._add(
                findings,
                "suspicious_storage_entry",
                "WARNING",
                "storage_entry",
                entry.name,
                None,
                "Storage entry has no trusted database or recovery association.",
                entry_kind="directory" if entry.is_dir() else "file",
            )
        return False

    @staticmethod
    def _document_active_is_usable(version: AuditDocumentVersion) -> bool:
        if (
            version.active_generation is None
            or version.indexed_at is None
            or version.parsed_at is None
            or version.index_status not in {"succeeded", "processing"}
        ):
            return False
        indexed_at = ConsistencyAuditService._as_utc(version.indexed_at)
        parsed_at = ConsistencyAuditService._as_utc(version.parsed_at)
        return indexed_at >= parsed_at

    @staticmethod
    def _knowledge_active_is_usable(entry: AuditKnowledgeEntry) -> bool:
        return (
            entry.validation_status == "verified"
            and entry.index_status in {"succeeded", "processing"}
            and entry.active_generation is not None
            and entry.indexed_at is not None
            and entry.indexed_source_updated_at == entry.updated_at
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @staticmethod
    def _summary(findings: list[ConsistencyAuditFinding]) -> ConsistencyAuditSummary:
        warning_count = sum(item.severity == "WARNING" for item in findings)
        error_count = sum(item.severity == "ERROR" for item in findings)
        critical_count = sum(item.severity == "CRITICAL" for item in findings)
        return ConsistencyAuditSummary(
            healthy=error_count == 0 and critical_count == 0,
            warning_count=warning_count,
            error_count=error_count,
            critical_count=critical_count,
        )

    @staticmethod
    def _add(
        findings: list[ConsistencyAuditFinding],
        code: str,
        severity: AuditSeverity,
        entity_type: str,
        entity_id: UUID | str,
        knowledge_base_id: UUID | None,
        safe_message: str,
        **details: AuditDetailValue,
    ) -> None:
        findings.append(
            ConsistencyAuditFinding(
                code=code,
                severity=severity,
                entity_type=entity_type,
                entity_id=str(entity_id),
                knowledge_base_id=knowledge_base_id,
                safe_message=safe_message,
                details=details,
            )
        )
