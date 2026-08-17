import hashlib
import stat
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_STORED, ZipFile

import pytest

from app.services.exceptions import (
    ArchiveLimitExceededError,
    ArchiveSourceIntegrityError,
    ArchiveStorageError,
)
from app.storage.archive import (
    ArchiveDataPayload,
    ArchiveDocumentSource,
    ArchiveLimits,
    LocalArchiveStorage,
    StagedArchiveDocuments,
    document_file_archive_path,
    validate_archive_path,
)


def limits(**overrides: int | float) -> ArchiveLimits:
    values: dict[str, int | float] = {
        "max_upload_size": 10_000,
        "max_single_file_size": 1_000,
        "max_total_extracted_size": 5_000,
        "max_entries": 20,
        "max_json_size": 1_000,
        "max_jsonl_records": 100,
        "max_compression_ratio": 100.0,
        "io_chunk_size": 3,
    }
    values.update(overrides)
    return ArchiveLimits(
        max_upload_size=int(values["max_upload_size"]),
        max_single_file_size=int(values["max_single_file_size"]),
        max_total_extracted_size=int(values["max_total_extracted_size"]),
        max_entries=int(values["max_entries"]),
        max_json_size=int(values["max_json_size"]),
        max_jsonl_records=int(values["max_jsonl_records"]),
        max_compression_ratio=float(values["max_compression_ratio"]),
        io_chunk_size=int(values["io_chunk_size"]),
    )


def source_file(root: Path, content: bytes = b"archive-content") -> ArchiveDocumentSource:
    version_id = uuid4()
    path = root / str(uuid4()) / str(uuid4()) / str(version_id) / "content.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    return ArchiveDocumentSource(
        document_version_id=version_id,
        archive_path=document_file_archive_path(version_id, ".md"),
        source_path=path,
        expected_size=len(content),
        expected_sha256=hashlib.sha256(content).hexdigest(),
    )


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/absolute/file",
        "../outside",
        "data/../outside",
        "data//file.json",
        "data/./file.json",
        "C:/windows/file",
        "data\\file.json",
        "data/bad\x00name",
    ],
)
def test_rejects_unsafe_archive_paths(path: str) -> None:
    with pytest.raises(ArchiveStorageError):
        validate_archive_path(path)


@pytest.mark.parametrize("extension", ["md", ".MD", ".tar.gz", ".m/d", "../md"])
def test_document_archive_path_accepts_only_safe_extension(extension: str) -> None:
    with pytest.raises(ArchiveStorageError):
        document_file_archive_path(uuid4(), extension)


async def test_stages_verified_files_and_builds_regular_stored_zip(tmp_path: Path) -> None:
    root = tmp_path / "uploads"
    storage = LocalArchiveStorage(root, limits())
    source = source_file(root)

    staged = await storage.stage_document_files([source])
    archive_path = await storage.build_export_archive(
        b'{"archive_version":1}',
        [ArchiveDataPayload("data/documents.jsonl", b'{"id":"one"}\n', 1)],
        staged,
    )

    with ZipFile(archive_path) as archive:
        assert archive.namelist() == [
            "manifest.json",
            "data/documents.jsonl",
            source.archive_path,
        ]
        assert archive.read(source.archive_path) == b"archive-content"
        for info in archive.infolist():
            assert info.compress_type == ZIP_STORED
            assert stat.S_ISREG(info.external_attr >> 16)

    await storage.discard_staged(staged)
    await storage.discard_archive(archive_path)
    assert not staged.operation_root.exists()
    assert not archive_path.exists()


async def test_hash_mismatch_rejects_source_and_cleans_staging(tmp_path: Path) -> None:
    root = tmp_path / "uploads"
    storage = LocalArchiveStorage(root, limits())
    source = source_file(root)
    changed = ArchiveDocumentSource(
        document_version_id=source.document_version_id,
        archive_path=source.archive_path,
        source_path=source.source_path,
        expected_size=source.expected_size,
        expected_sha256="0" * 64,
    )

    with pytest.raises(ArchiveSourceIntegrityError):
        await storage.stage_document_files([changed])

    assert list(storage.staging_root.iterdir()) == []


async def test_symlink_source_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "uploads"
    storage = LocalArchiveStorage(root, limits())
    target = root / "target.md"
    target.write_bytes(b"target")
    link = root / "link.md"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Creating symlinks is not permitted on this host")
    version_id = uuid4()
    source = ArchiveDocumentSource(
        document_version_id=version_id,
        archive_path=document_file_archive_path(version_id, ".md"),
        source_path=link,
        expected_size=6,
        expected_sha256=hashlib.sha256(b"target").hexdigest(),
    )

    with pytest.raises(ArchiveStorageError):
        await storage.stage_document_files([source])


async def test_limits_and_duplicate_entries_remove_partial_archive(tmp_path: Path) -> None:
    root = tmp_path / "uploads"
    storage = LocalArchiveStorage(root, limits(max_json_size=5))
    empty_staging = StagedArchiveDocuments(tmp_path / "unused", ())

    with pytest.raises(ArchiveLimitExceededError):
        await storage.build_export_archive(b"123456", [], empty_staging)
    with pytest.raises(ArchiveStorageError):
        await storage.build_export_archive(
            b"{}",
            [ArchiveDataPayload("manifest.json", b"{}", 1)],
            empty_staging,
        )

    assert list(storage.root.glob("*.tracemind.zip")) == []
