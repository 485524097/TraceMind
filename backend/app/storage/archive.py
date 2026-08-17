import asyncio
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo

from fastapi import UploadFile
from pydantic import ValidationError

from app.core.config import Settings
from app.schemas.knowledge_base_archive import (
    KnowledgeBaseRestoreJournal,
    RestoreJournalFile,
)
from app.services.exceptions import (
    ArchiveLimitExceededError,
    ArchiveSourceIntegrityError,
    ArchiveStorageError,
    ArchiveValidationError,
)

MANIFEST_PATH = "manifest.json"
RESTORE_MARKER_NAME = ".tracemind-restore.json"


@dataclass(frozen=True)
class ArchiveLimits:
    max_upload_size: int
    max_single_file_size: int
    max_total_extracted_size: int
    max_entries: int
    max_json_size: int
    max_jsonl_records: int
    max_compression_ratio: float
    io_chunk_size: int


def archive_limits_from_settings(settings: Settings) -> ArchiveLimits:
    return ArchiveLimits(
        max_upload_size=settings.archive_max_upload_size_bytes,
        max_single_file_size=settings.archive_max_extracted_single_file_size_bytes,
        max_total_extracted_size=settings.archive_max_total_extracted_size_bytes,
        max_entries=settings.archive_max_zip_entries,
        max_json_size=settings.archive_max_json_size_bytes,
        max_jsonl_records=settings.archive_max_jsonl_records,
        max_compression_ratio=settings.archive_max_compression_ratio,
        io_chunk_size=settings.archive_io_chunk_size_bytes,
    )


@dataclass(frozen=True)
class ArchiveDataPayload:
    path: str
    content: bytes
    record_count: int


@dataclass(frozen=True)
class ArchiveDocumentSource:
    document_version_id: UUID
    archive_path: str
    source_path: Path
    expected_size: int
    expected_sha256: str


@dataclass(frozen=True)
class StagedArchiveDocument:
    document_version_id: UUID
    archive_path: str
    staged_path: Path
    size: int
    sha256: str


@dataclass(frozen=True)
class StagedArchiveDocuments:
    operation_root: Path
    entries: tuple[StagedArchiveDocument, ...]


@dataclass(frozen=True)
class TemporaryArchiveUpload:
    path: Path
    size: int


@dataclass(frozen=True)
class ArchiveZipEntry:
    path: str
    size: int
    compressed_size: int
    compression_method: int


@dataclass(frozen=True)
class InspectedArchive:
    entries: dict[str, ArchiveZipEntry]


@dataclass(frozen=True)
class RestoreDocumentSource:
    document_version_id: UUID
    archive_path: str
    storage_path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class StagedKnowledgeBaseRestore:
    operation_id: UUID
    knowledge_base_id: UUID
    operation_root: Path
    staging_path: Path
    final_path: Path


@dataclass(frozen=True)
class RestoreJournalInspection:
    valid: tuple[tuple[Path, KnowledgeBaseRestoreJournal], ...]
    invalid_names: tuple[str, ...]
    staging_residue_names: tuple[str, ...]


def validate_archive_path(path: str) -> str:
    if not path or "\x00" in path or "\\" in path:
        raise ArchiveStorageError("Archive entry path is unsafe")
    if re.match(r"^[A-Za-z]:", path) or path.startswith("/"):
        raise ArchiveStorageError("Archive entry path is unsafe")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ArchiveStorageError("Archive entry path is unsafe")
    pure_path = PurePosixPath(path)
    if pure_path.is_absolute() or pure_path.as_posix() != path:
        raise ArchiveStorageError("Archive entry path is unsafe")
    return path


def document_file_archive_path(document_version_id: UUID, extension: str) -> str:
    if not re.fullmatch(r"\.[a-z0-9]{1,31}", extension):
        raise ArchiveStorageError("Document extension cannot be represented safely")
    return validate_archive_path(
        f"files/document_versions/{document_version_id}/content{extension}"
    )


class LocalArchiveStorage:
    def __init__(
        self,
        document_storage_root: Path,
        limits: ArchiveLimits,
        *,
        create_roots: bool = True,
    ) -> None:
        self.document_storage_root = document_storage_root.expanduser().resolve()
        self.root = self.document_storage_root / ".archive-tmp"
        self.staging_root = self.root / "staging"
        self.upload_root = self.root / "uploads"
        self.restore_root = self.document_storage_root / ".restore-tmp"
        self.journal_root = self.restore_root / "journals"
        self.limits = limits
        if create_roots:
            self.root.mkdir(parents=True, exist_ok=True)
            self.staging_root.mkdir(exist_ok=True)
            self.upload_root.mkdir(exist_ok=True)
            self.restore_root.mkdir(exist_ok=True)
            self.journal_root.mkdir(exist_ok=True)

    async def write_restore_upload(self, upload: UploadFile) -> TemporaryArchiveUpload:
        descriptor: int | None = None
        path: Path | None = None
        handle: BinaryIO | None = None
        size = 0
        try:
            descriptor, raw_path = await asyncio.to_thread(
                tempfile.mkstemp,
                prefix="restore-upload-",
                suffix=".tracemind.zip",
                dir=self.upload_root,
            )
            path = Path(raw_path)
            assert descriptor is not None
            opened_handle = await asyncio.to_thread(self._open_binary_descriptor, descriptor)
            handle = opened_handle
            descriptor = None
            while chunk := await upload.read(self.limits.io_chunk_size):
                size += len(chunk)
                if size > self.limits.max_upload_size:
                    raise ArchiveLimitExceededError("Archive upload exceeds the size limit")
                await asyncio.to_thread(opened_handle.write, chunk)
            await asyncio.to_thread(opened_handle.flush)
            if size == 0:
                raise ArchiveValidationError("Archive upload is empty")
        except (ArchiveLimitExceededError, ArchiveValidationError):
            if handle is not None:
                with suppress(OSError):
                    await asyncio.to_thread(handle.close)
                handle = None
            if path is not None:
                path.unlink(missing_ok=True)
            raise
        except Exception as exc:
            if handle is not None:
                with suppress(OSError):
                    await asyncio.to_thread(handle.close)
                handle = None
            if path is not None:
                path.unlink(missing_ok=True)
            raise ArchiveStorageError("Archive upload could not be stored") from exc
        finally:
            if handle is not None:
                with suppress(OSError):
                    await asyncio.to_thread(handle.close)
            elif descriptor is not None:
                with suppress(OSError):
                    await asyncio.to_thread(os.close, descriptor)
        if path is None:
            raise ArchiveStorageError("Archive upload could not be stored")
        return TemporaryArchiveUpload(path=path, size=size)

    @staticmethod
    def _open_binary_descriptor(descriptor: int) -> BinaryIO:
        return os.fdopen(descriptor, "wb")

    async def inspect_archive(self, path: Path) -> InspectedArchive:
        try:
            return await asyncio.to_thread(self._inspect_archive, path)
        except (ArchiveLimitExceededError, ArchiveValidationError):
            raise
        except (BadZipFile, OSError, RuntimeError, ValueError) as exc:
            raise ArchiveValidationError("Archive ZIP structure is invalid") from exc

    def _inspect_archive(self, path: Path) -> InspectedArchive:
        entries: dict[str, ArchiveZipEntry] = {}
        total_size = 0
        with ZipFile(path, mode="r") as archive:
            infos = archive.infolist()
            if len(infos) > self.limits.max_entries:
                raise ArchiveLimitExceededError("Archive contains too many ZIP entries")
            for info in infos:
                try:
                    entry_path = validate_archive_path(info.filename)
                except ArchiveStorageError as exc:
                    raise ArchiveValidationError("Archive contains an unsafe entry path") from exc
                if entry_path in entries:
                    raise ArchiveValidationError("Archive contains duplicate ZIP entries")
                if info.flag_bits & 0x1:
                    raise ArchiveValidationError("Encrypted ZIP entries are not supported")
                if info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
                    raise ArchiveValidationError("ZIP compression method is not allowed")
                unix_mode = info.external_attr >> 16
                file_type = stat.S_IFMT(unix_mode)
                if file_type not in {0, stat.S_IFREG}:
                    raise ArchiveValidationError("ZIP special files are not allowed")
                if info.file_size > self.limits.max_single_file_size:
                    raise ArchiveLimitExceededError("A ZIP entry exceeds the single-file limit")
                total_size += info.file_size
                if total_size > self.limits.max_total_extracted_size:
                    raise ArchiveLimitExceededError("Archive exceeds the extracted size limit")
                if info.file_size > 0:
                    if info.compress_size == 0:
                        raise ArchiveLimitExceededError("ZIP entry compression ratio is invalid")
                    ratio = info.file_size / info.compress_size
                    if ratio > self.limits.max_compression_ratio:
                        raise ArchiveLimitExceededError("ZIP entry compression ratio is too high")
                entries[entry_path] = ArchiveZipEntry(
                    path=entry_path,
                    size=info.file_size,
                    compressed_size=info.compress_size,
                    compression_method=info.compress_type,
                )
        if MANIFEST_PATH not in entries:
            raise ArchiveValidationError("Archive manifest is missing")
        return InspectedArchive(entries=entries)

    async def read_archive_entry(self, archive_path: Path, entry_path: str) -> bytes:
        try:
            return await asyncio.to_thread(self._read_archive_entry, archive_path, entry_path)
        except ArchiveValidationError:
            raise
        except (BadZipFile, KeyError, OSError, RuntimeError) as exc:
            raise ArchiveValidationError("Archive entry could not be read") from exc

    def _read_archive_entry(self, archive_path: Path, entry_path: str) -> bytes:
        validate_archive_path(entry_path)
        with ZipFile(archive_path, mode="r") as archive:
            with archive.open(entry_path, mode="r") as source:
                content = source.read(self.limits.max_json_size + 1)
                if len(content) > self.limits.max_json_size:
                    raise ArchiveLimitExceededError("Archive JSON entry exceeds the size limit")
                return content

    async def hash_archive_entry(self, archive_path: Path, entry_path: str) -> tuple[int, str]:
        try:
            return await asyncio.to_thread(self._hash_archive_entry, archive_path, entry_path)
        except (ArchiveLimitExceededError, ArchiveValidationError):
            raise
        except (BadZipFile, KeyError, OSError, RuntimeError) as exc:
            raise ArchiveValidationError("Archive file entry could not be read") from exc

    def _hash_archive_entry(self, archive_path: Path, entry_path: str) -> tuple[int, str]:
        validate_archive_path(entry_path)
        digest = hashlib.sha256()
        size = 0
        with ZipFile(archive_path, mode="r") as archive:
            with archive.open(entry_path, mode="r") as source:
                while chunk := source.read(self.limits.io_chunk_size):
                    size += len(chunk)
                    if size > self.limits.max_single_file_size:
                        raise ArchiveLimitExceededError("Archive file entry exceeds the size limit")
                    digest.update(chunk)
        return size, digest.hexdigest()

    async def stage_restore_files(
        self,
        archive_path: Path,
        operation_id: UUID,
        knowledge_base_id: UUID,
        sources: list[RestoreDocumentSource],
    ) -> StagedKnowledgeBaseRestore:
        try:
            return await asyncio.to_thread(
                self._stage_restore_files,
                archive_path,
                operation_id,
                knowledge_base_id,
                sources,
            )
        except (ArchiveLimitExceededError, ArchiveValidationError, ArchiveStorageError):
            raise
        except (BadZipFile, KeyError, OSError, RuntimeError) as exc:
            raise ArchiveStorageError("Document files could not be staged for restore") from exc

    def _stage_restore_files(
        self,
        archive_path: Path,
        operation_id: UUID,
        knowledge_base_id: UUID,
        sources: list[RestoreDocumentSource],
    ) -> StagedKnowledgeBaseRestore:
        operation_root = self.restore_root / str(operation_id)
        staging_path = operation_root / str(knowledge_base_id)
        final_path = self.document_storage_root / str(knowledge_base_id)
        if final_path.exists():
            raise ArchiveStorageError("Knowledge Base storage directory already exists")
        self._io_path(operation_root).mkdir(exist_ok=False)
        self._io_path(staging_path).mkdir()
        staged = StagedKnowledgeBaseRestore(
            operation_id=operation_id,
            knowledge_base_id=knowledge_base_id,
            operation_root=operation_root,
            staging_path=staging_path,
            final_path=final_path,
        )
        seen_archive_paths: set[str] = set()
        seen_storage_paths: set[str] = set()
        try:
            self._write_restore_marker(self._io_path(staging_path), operation_id, knowledge_base_id)
            with ZipFile(archive_path, mode="r") as archive:
                for source in sources:
                    archive_entry = validate_archive_path(source.archive_path)
                    storage_path = validate_archive_path(source.storage_path)
                    storage_parts = PurePosixPath(storage_path).parts
                    if not storage_parts or storage_parts[0] != str(knowledge_base_id):
                        raise ArchiveStorageError(
                            "Restore storage path is outside the Knowledge Base"
                        )
                    if archive_entry in seen_archive_paths or storage_path in seen_storage_paths:
                        raise ArchiveValidationError("Restore file mappings must be unique")
                    seen_archive_paths.add(archive_entry)
                    seen_storage_paths.add(storage_path)
                    destination = (
                        operation_root / Path(*PurePosixPath(storage_path).parts)
                    ).resolve()
                    if not destination.is_relative_to(operation_root):
                        raise ArchiveStorageError("Restore staging path is unsafe")
                    io_destination = self._io_path(destination)
                    io_destination.parent.mkdir(parents=True, exist_ok=False)
                    digest = hashlib.sha256()
                    copied = 0
                    with archive.open(archive_entry, mode="r") as source_handle:
                        with io_destination.open("xb") as target:
                            while chunk := source_handle.read(self.limits.io_chunk_size):
                                copied += len(chunk)
                                if copied > self.limits.max_single_file_size:
                                    raise ArchiveLimitExceededError(
                                        "Restore file exceeds the single-file limit"
                                    )
                                digest.update(chunk)
                                target.write(chunk)
                    if copied != source.size or digest.hexdigest() != source.sha256:
                        raise ArchiveValidationError(
                            "Restore file no longer matches the validated archive"
                        )
        except Exception:
            shutil.rmtree(self._io_path(operation_root), ignore_errors=True)
            raise
        return staged

    async def create_restore_journal(
        self,
        staged: StagedKnowledgeBaseRestore,
        sources: list[RestoreDocumentSource],
    ) -> tuple[Path, KnowledgeBaseRestoreJournal]:
        journal = KnowledgeBaseRestoreJournal(
            operation_id=staged.operation_id,
            knowledge_base_id=staged.knowledge_base_id,
            staging_path=(
                PurePosixPath(".restore-tmp")
                / str(staged.operation_id)
                / str(staged.knowledge_base_id)
            ).as_posix(),
            final_path=str(staged.knowledge_base_id),
            created_at=datetime.now(UTC),
            files=[
                RestoreJournalFile(
                    document_version_id=source.document_version_id,
                    path=source.storage_path,
                    size=source.size,
                    sha256=source.sha256,
                )
                for source in sources
            ],
        )
        path = self.journal_root / f"{staged.operation_id}.json"
        try:
            await asyncio.to_thread(self._write_journal, path, journal)
        except OSError as exc:
            raise ArchiveStorageError("Restore journal could not be created") from exc
        return path, journal

    async def mark_restore_promoted(
        self, path: Path, journal: KnowledgeBaseRestoreJournal
    ) -> KnowledgeBaseRestoreJournal:
        promoted = journal.model_copy(update={"promoted": True})
        try:
            await asyncio.to_thread(self._write_journal, path, promoted)
        except OSError as exc:
            raise ArchiveStorageError("Restore journal could not be updated") from exc
        return promoted

    async def promote_restore(self, staged: StagedKnowledgeBaseRestore) -> None:
        try:
            await asyncio.to_thread(self._promote_restore, staged)
        except OSError as exc:
            raise ArchiveStorageError("Knowledge Base files could not be promoted") from exc

    def _promote_restore(self, staged: StagedKnowledgeBaseRestore) -> None:
        if self._io_path(staged.final_path).exists():
            raise FileExistsError("Knowledge Base storage directory already exists")
        if not self._restore_marker_matches(
            self._io_path(staged.staging_path),
            staged.operation_id,
            staged.knowledge_base_id,
        ):
            raise OSError("Restore staging marker is missing or invalid")
        os.rename(self._io_path(staged.staging_path), self._io_path(staged.final_path))

    async def compensate_failed_restore(self, staged: StagedKnowledgeBaseRestore) -> None:
        await asyncio.to_thread(self._compensate_failed_restore, staged)

    def _compensate_failed_restore(self, staged: StagedKnowledgeBaseRestore) -> None:
        if self._restore_marker_matches(
            self._io_path(staged.final_path),
            staged.operation_id,
            staged.knowledge_base_id,
        ):
            shutil.rmtree(self._io_path(staged.final_path), ignore_errors=True)
        shutil.rmtree(self._io_path(staged.operation_root), ignore_errors=True)

    async def finish_restore(
        self,
        staged: StagedKnowledgeBaseRestore,
        journal_path: Path,
    ) -> None:
        try:
            await asyncio.to_thread(self._finish_restore, staged, journal_path)
        except OSError as exc:
            raise ArchiveStorageError("Restore journal cleanup could not be completed") from exc

    def _finish_restore(self, staged: StagedKnowledgeBaseRestore, journal_path: Path) -> None:
        marker = self._io_path(staged.final_path / RESTORE_MARKER_NAME)
        marker.unlink(missing_ok=True)
        journal_path.unlink(missing_ok=True)
        shutil.rmtree(self._io_path(staged.operation_root), ignore_errors=True)

    async def discard_restore_journal(self, path: Path | None) -> None:
        if path is not None:
            await asyncio.to_thread(path.unlink, missing_ok=True)

    async def load_restore_journals(
        self,
    ) -> list[tuple[Path, KnowledgeBaseRestoreJournal]]:
        return await asyncio.to_thread(self._load_restore_journals)

    def _load_restore_journals(self) -> list[tuple[Path, KnowledgeBaseRestoreJournal]]:
        return list(self._inspect_restore_journals().valid)

    async def inspect_restore_journals(self) -> RestoreJournalInspection:
        """Return read-only recovery metadata using the startup parser and validation rules."""

        return await asyncio.to_thread(self._inspect_restore_journals)

    def _inspect_restore_journals(self) -> RestoreJournalInspection:
        if not self.restore_root.exists():
            return RestoreJournalInspection((), (), ())
        journals: list[tuple[Path, KnowledgeBaseRestoreJournal]] = []
        invalid_names: list[str] = []
        for path in sorted(self.journal_root.glob("*.json")):
            try:
                journal = self._read_restore_journal(path)
            except (OSError, UnicodeDecodeError, ValidationError, ValueError):
                invalid_names.append(path.name)
                continue
            journals.append((path, journal))
        staging_residue_names = tuple(
            path.name
            for path in sorted(self.restore_root.iterdir(), key=lambda item: item.name)
            if path.name != self.journal_root.name
        )
        return RestoreJournalInspection(
            tuple(journals),
            tuple(invalid_names),
            staging_residue_names,
        )

    def _read_restore_journal(self, path: Path) -> KnowledgeBaseRestoreJournal:
        journal = KnowledgeBaseRestoreJournal.model_validate_json(
            path.read_text(encoding="utf-8"), strict=True
        )
        if path.name != f"{journal.operation_id}.json":
            raise ValueError("Restore journal filename does not match its operation")
        expected_staging = (
            PurePosixPath(".restore-tmp")
            / str(journal.operation_id)
            / str(journal.knowledge_base_id)
        ).as_posix()
        if journal.staging_path != expected_staging:
            raise ValueError("Restore journal staging path is invalid")
        if journal.final_path != str(journal.knowledge_base_id):
            raise ValueError("Restore journal final path is invalid")
        if len({item.path for item in journal.files}) != len(journal.files):
            raise ValueError("Restore journal paths are not unique")
        if any(not self._journal_file_path_is_valid(journal, item.path) for item in journal.files):
            raise ValueError("Restore journal file path is invalid")
        return journal

    async def recover_absent_database_restore(
        self, path: Path, journal: KnowledgeBaseRestoreJournal
    ) -> None:
        await asyncio.to_thread(self._recover_absent_database_restore, path, journal)

    def _recover_absent_database_restore(
        self, path: Path, journal: KnowledgeBaseRestoreJournal
    ) -> None:
        operation_root = self.restore_root / str(journal.operation_id)
        final_path = self.document_storage_root / str(journal.knowledge_base_id)
        if self._restore_marker_matches(
            self._io_path(final_path),
            journal.operation_id,
            journal.knowledge_base_id,
        ):
            shutil.rmtree(self._io_path(final_path), ignore_errors=True)
        shutil.rmtree(self._io_path(operation_root), ignore_errors=True)
        path.unlink(missing_ok=True)

    async def final_restore_is_complete(self, journal: KnowledgeBaseRestoreJournal) -> bool:
        return await asyncio.to_thread(self._final_restore_is_complete, journal)

    def _final_restore_is_complete(self, journal: KnowledgeBaseRestoreJournal) -> bool:
        final_path = self.document_storage_root / str(journal.knowledge_base_id)
        io_final_path = self._io_path(final_path)
        if not io_final_path.is_dir() or io_final_path.is_symlink():
            return False
        for item in journal.files:
            path = self.document_storage_root / Path(*PurePosixPath(item.path).parts)
            io_path = self._io_path(path)
            if not io_path.is_file() or io_path.is_symlink() or io_path.stat().st_size != item.size:
                return False
            digest = hashlib.sha256()
            with io_path.open("rb") as handle:
                while chunk := handle.read(self.limits.io_chunk_size):
                    digest.update(chunk)
            if digest.hexdigest() != item.sha256:
                return False
        return True

    async def finish_recovered_restore(
        self, path: Path, journal: KnowledgeBaseRestoreJournal
    ) -> None:
        final_path = self.document_storage_root / str(journal.knowledge_base_id)
        marker = self._io_path(final_path / RESTORE_MARKER_NAME)
        await asyncio.to_thread(marker.unlink, missing_ok=True)
        await asyncio.to_thread(path.unlink, missing_ok=True)
        operation_root = self.restore_root / str(journal.operation_id)
        await asyncio.to_thread(shutil.rmtree, self._io_path(operation_root), True)

    def _write_journal(self, path: Path, journal: KnowledgeBaseRestoreJournal) -> None:
        descriptor, raw_path = tempfile.mkstemp(
            prefix="journal-", suffix=".tmp", dir=self.journal_root
        )
        temporary = Path(raw_path)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                content = journal.model_dump_json(indent=2).encode("utf-8")
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _write_restore_marker(path: Path, operation_id: UUID, knowledge_base_id: UUID) -> None:
        marker = path / RESTORE_MARKER_NAME
        marker.write_text(
            json.dumps(
                {
                    "knowledge_base_id": str(knowledge_base_id),
                    "operation_id": str(operation_id),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _restore_marker_matches(path: Path, operation_id: UUID, knowledge_base_id: UUID) -> bool:
        marker = path / RESTORE_MARKER_NAME
        try:
            content: object = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        return content == {
            "knowledge_base_id": str(knowledge_base_id),
            "operation_id": str(operation_id),
        }

    @staticmethod
    def _journal_file_path_is_valid(journal: KnowledgeBaseRestoreJournal, path: str) -> bool:
        try:
            parts = PurePosixPath(validate_archive_path(path)).parts
        except ArchiveStorageError:
            return False
        return bool(parts) and parts[0] == str(journal.knowledge_base_id)

    @staticmethod
    def _io_path(path: Path) -> Path:
        if os.name != "nt":
            return path
        raw = str(path.resolve(strict=False))
        if raw.startswith("\\\\?\\"):
            return path
        return Path(f"\\\\?\\{raw}")

    async def stage_document_files(
        self, sources: list[ArchiveDocumentSource]
    ) -> StagedArchiveDocuments:
        try:
            return await asyncio.to_thread(self._stage_document_files, sources)
        except (ArchiveLimitExceededError, ArchiveSourceIntegrityError, ArchiveStorageError):
            raise
        except OSError as exc:
            raise ArchiveStorageError("Document files could not be staged for export") from exc

    def _stage_document_files(self, sources: list[ArchiveDocumentSource]) -> StagedArchiveDocuments:
        if len(sources) > self.limits.max_entries:
            raise ArchiveLimitExceededError("Archive contains too many document files")
        operation_root = Path(tempfile.mkdtemp(prefix="export-", dir=self.staging_root))
        entries: list[StagedArchiveDocument] = []
        total_size = 0
        seen_paths: set[str] = set()
        try:
            for source in sources:
                archive_path = validate_archive_path(source.archive_path)
                if archive_path in seen_paths:
                    raise ArchiveStorageError("Archive entry paths must be unique")
                seen_paths.add(archive_path)
                if source.expected_size > self.limits.max_single_file_size:
                    raise ArchiveLimitExceededError("A document exceeds the archive file limit")
                source_path = source.source_path
                if (
                    not source_path.is_file()
                    or source_path.is_symlink()
                    or not source_path.resolve(strict=True).is_relative_to(
                        self.document_storage_root
                    )
                ):
                    raise ArchiveStorageError("A stored document is unavailable")
                destination = (operation_root / Path(*PurePosixPath(archive_path).parts)).resolve()
                if not destination.is_relative_to(operation_root):
                    raise ArchiveStorageError("Archive staging path is unsafe")
                destination.parent.mkdir(parents=True, exist_ok=False)
                digest = hashlib.sha256()
                copied = 0
                with source_path.open("rb") as source_handle, destination.open("xb") as target:
                    while chunk := source_handle.read(self.limits.io_chunk_size):
                        copied += len(chunk)
                        total_size += len(chunk)
                        if copied > self.limits.max_single_file_size:
                            raise ArchiveLimitExceededError(
                                "A document exceeds the archive file limit"
                            )
                        if total_size > self.limits.max_total_extracted_size:
                            raise ArchiveLimitExceededError(
                                "Archive exceeds the total extracted size limit"
                            )
                        digest.update(chunk)
                        target.write(chunk)
                content_hash = digest.hexdigest()
                if copied != source.expected_size or content_hash != source.expected_sha256:
                    raise ArchiveSourceIntegrityError(
                        "A stored document no longer matches its database metadata"
                    )
                entries.append(
                    StagedArchiveDocument(
                        document_version_id=source.document_version_id,
                        archive_path=archive_path,
                        staged_path=destination,
                        size=copied,
                        sha256=content_hash,
                    )
                )
        except Exception:
            shutil.rmtree(operation_root, ignore_errors=True)
            raise
        return StagedArchiveDocuments(operation_root, tuple(entries))

    async def build_export_archive(
        self,
        manifest: bytes,
        data_payloads: list[ArchiveDataPayload],
        staged_documents: StagedArchiveDocuments,
    ) -> Path:
        try:
            return await asyncio.to_thread(
                self._build_export_archive,
                manifest,
                data_payloads,
                staged_documents,
            )
        except (ArchiveLimitExceededError, ArchiveSourceIntegrityError, ArchiveStorageError):
            raise
        except OSError as exc:
            raise ArchiveStorageError("Knowledge Base archive could not be written") from exc

    def _build_export_archive(
        self,
        manifest: bytes,
        data_payloads: list[ArchiveDataPayload],
        staged_documents: StagedArchiveDocuments,
    ) -> Path:
        all_paths = [MANIFEST_PATH]
        all_paths.extend(payload.path for payload in data_payloads)
        all_paths.extend(entry.archive_path for entry in staged_documents.entries)
        validated_paths = [validate_archive_path(path) for path in all_paths]
        if len(validated_paths) != len(set(validated_paths)):
            raise ArchiveStorageError("Archive entry paths must be unique")
        if len(validated_paths) > self.limits.max_entries:
            raise ArchiveLimitExceededError("Archive contains too many entries")

        total_size = len(manifest)
        if len(manifest) > self.limits.max_json_size:
            raise ArchiveLimitExceededError("Archive manifest exceeds the JSON size limit")
        for payload in data_payloads:
            if len(payload.content) > self.limits.max_json_size:
                raise ArchiveLimitExceededError("Archive data entry exceeds the JSON size limit")
            if payload.record_count > self.limits.max_jsonl_records:
                raise ArchiveLimitExceededError("Archive data entry contains too many records")
            total_size += len(payload.content)
        total_size += sum(entry.size for entry in staged_documents.entries)
        if total_size > self.limits.max_total_extracted_size:
            raise ArchiveLimitExceededError("Archive exceeds the total extracted size limit")

        descriptor, raw_path = tempfile.mkstemp(
            prefix="knowledge-base-", suffix=".tracemind.zip", dir=self.root
        )
        os.close(descriptor)
        archive_path = Path(raw_path)
        try:
            with ZipFile(
                archive_path, mode="w", compression=ZIP_STORED, allowZip64=True
            ) as archive:
                archive.writestr(self._regular_file_info(MANIFEST_PATH), manifest)
                for payload in data_payloads:
                    archive.writestr(self._regular_file_info(payload.path), payload.content)
                for entry in staged_documents.entries:
                    with (
                        entry.staged_path.open("rb") as source,
                        archive.open(
                            self._regular_file_info(entry.archive_path),
                            mode="w",
                            force_zip64=True,
                        ) as target,
                    ):
                        shutil.copyfileobj(source, target, length=self.limits.io_chunk_size)
            if archive_path.stat().st_size > self.limits.max_upload_size:
                raise ArchiveLimitExceededError("Archive exceeds the upload size limit")
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise
        return archive_path

    async def discard_staged(self, staged: StagedArchiveDocuments) -> None:
        await asyncio.to_thread(shutil.rmtree, staged.operation_root, True)

    async def discard_archive(self, path: Path) -> None:
        await asyncio.to_thread(path.unlink, missing_ok=True)

    @staticmethod
    def _regular_file_info(path: str) -> ZipInfo:
        info = ZipInfo(validate_archive_path(path))
        info.compress_type = ZIP_STORED
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o600) << 16
        return info
