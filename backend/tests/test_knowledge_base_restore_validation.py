import hashlib
import json
import stat
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_BZIP2, ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest
from fastapi import UploadFile

from app.services.exceptions import (
    ArchiveLimitExceededError,
    ArchiveValidationError,
)
from app.services.knowledge_base_restore import KnowledgeBaseArchiveValidator
from app.storage.archive import ArchiveLimits, LocalArchiveStorage
from app.storage.local import LocalFileStorage
from tests.archive_restore_fixtures import build_restore_archive, rewrite_archive


def limits(**overrides: int | float) -> ArchiveLimits:
    values: dict[str, int | float] = {
        "max_upload_size": 1_000_000,
        "max_single_file_size": 100_000,
        "max_total_extracted_size": 500_000,
        "max_entries": 100,
        "max_json_size": 100_000,
        "max_jsonl_records": 1_000,
        "max_compression_ratio": 100.0,
        "io_chunk_size": 4,
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


def validator(
    tmp_path: Path, archive_limits: ArchiveLimits | None = None
) -> KnowledgeBaseArchiveValidator:
    document_storage = LocalFileStorage(tmp_path / "uploads", max_size=1_000, chunk_size=4)
    archive_storage = LocalArchiveStorage(document_storage.root, archive_limits or limits())
    return KnowledgeBaseArchiveValidator(
        archive_storage,
        document_storage,
        {".md", ".txt"},
    )


def replace_data_with_valid_manifest(
    source: Path, destination: Path, data_path: str, content: bytes
) -> Path:
    with ZipFile(source) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    descriptor = next(item for item in manifest["data_entries"] if item["path"] == data_path)
    descriptor["size"] = len(content)
    descriptor["sha256"] = hashlib.sha256(content).hexdigest()
    descriptor["record_count"] = len(content.splitlines())
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return rewrite_archive(
        source,
        destination,
        {data_path: content, "manifest.json": manifest_bytes},
    )


async def test_validates_complete_archive_and_rebuilds_paths(tmp_path: Path) -> None:
    fixture = build_restore_archive(tmp_path)

    validated = await validator(tmp_path).validate(fixture.path)

    assert validated.knowledge_base.id == fixture.knowledge_base_id
    assert validated.normalized_documents[fixture.document_id].normalized_name == "guide.md"
    assert validated.normalized_documents[fixture.document_id].normalized_path == "docs/guide.md"
    assert validated.restore_files[0].storage_path == (
        f"{fixture.knowledge_base_id}/{fixture.document_id}/{fixture.version_id}/content.md"
    )
    assert validated.restore_files[0].sha256 == hashlib.sha256(fixture.content).hexdigest()


@pytest.mark.parametrize(
    ("replacement", "expected_error"),
    [
        ({"manifest.json": b"{}"}, ArchiveValidationError),
        ({"data/documents.jsonl": None}, ArchiveValidationError),
        ({"files/document_versions/missing/content.md": b"extra"}, ArchiveValidationError),
    ],
)
async def test_rejects_invalid_manifest_missing_and_undeclared_entries(
    tmp_path: Path,
    replacement: dict[str, bytes | None],
    expected_error: type[Exception],
) -> None:
    fixture = build_restore_archive(tmp_path)
    destination = tmp_path / "invalid.tracemind.zip"
    if "files/document_versions/missing/content.md" in replacement:
        rewrite_archive(
            fixture.path,
            destination,
            {},
            extra_entries=[("files/document_versions/missing/content.md", b"extra")],
        )
    else:
        rewrite_archive(fixture.path, destination, replacement)

    with pytest.raises(expected_error):
        await validator(tmp_path).validate(destination)


async def test_rejects_file_checksum_corruption(tmp_path: Path) -> None:
    fixture = build_restore_archive(tmp_path)
    with ZipFile(fixture.path) as archive:
        file_path = next(name for name in archive.namelist() if name.startswith("files/"))
    corrupted = rewrite_archive(
        fixture.path,
        tmp_path / "checksum.tracemind.zip",
        {file_path: b"corrupted"},
    )

    with pytest.raises(ArchiveValidationError):
        await validator(tmp_path).validate(corrupted)


@pytest.mark.parametrize(
    "content",
    [
        b"{broken json}\n",
        b'{"id":"one","id":"two"}\n',
        b"\xff\n",
    ],
)
async def test_rejects_corrupt_duplicate_key_and_non_utf8_jsonl(
    tmp_path: Path, content: bytes
) -> None:
    fixture = build_restore_archive(tmp_path)
    corrupted = replace_data_with_valid_manifest(
        fixture.path,
        tmp_path / "jsonl.tracemind.zip",
        "data/documents.jsonl",
        content,
    )

    with pytest.raises(ArchiveValidationError):
        await validator(tmp_path).validate(corrupted)


@pytest.mark.parametrize("unsafe_path", ["../evil", "/absolute", "C:/drive", "a\\b"])
async def test_rejects_zip_slip_and_platform_paths(tmp_path: Path, unsafe_path: str) -> None:
    fixture = build_restore_archive(tmp_path)
    unsafe = rewrite_archive(
        fixture.path,
        tmp_path / "unsafe.tracemind.zip",
        {},
        extra_entries=[(unsafe_path, b"unsafe")],
    )

    with pytest.raises(ArchiveValidationError):
        await validator(tmp_path).validate(unsafe)


async def test_rejects_duplicate_zip_entry(tmp_path: Path) -> None:
    fixture = build_restore_archive(tmp_path)
    with ZipFile(fixture.path) as archive:
        manifest = archive.read("manifest.json")
    with pytest.warns(UserWarning, match="Duplicate name"):
        duplicate = rewrite_archive(
            fixture.path,
            tmp_path / "duplicate.tracemind.zip",
            {},
            extra_entries=[("manifest.json", manifest)],
        )

    with pytest.raises(ArchiveValidationError):
        await validator(tmp_path).validate(duplicate)


async def test_rejects_high_compression_ratio(tmp_path: Path) -> None:
    fixture = build_restore_archive(tmp_path)
    bomb = tmp_path / "bomb.tracemind.zip"
    with (
        ZipFile(fixture.path) as source,
        ZipFile(bomb, mode="w", compression=ZIP_STORED) as destination,
    ):
        for info in source.infolist():
            destination.writestr(info.filename, source.read(info.filename))
        destination.writestr(
            "data/bomb.jsonl",
            b"0" * 20_000,
            compress_type=ZIP_DEFLATED,
        )

    with pytest.raises(ArchiveLimitExceededError):
        await validator(tmp_path).validate(bomb)


@pytest.mark.parametrize("file_type", [stat.S_IFLNK, stat.S_IFCHR])
async def test_rejects_symlink_and_device_entries(tmp_path: Path, file_type: int) -> None:
    fixture = build_restore_archive(tmp_path)
    special = tmp_path / "special.tracemind.zip"
    with ZipFile(fixture.path) as source, ZipFile(special, mode="w") as destination:
        for info in source.infolist():
            destination.writestr(info.filename, source.read(info.filename))
        special_info = ZipInfo("files/special")
        special_info.create_system = 3
        special_info.external_attr = (file_type | 0o600) << 16
        destination.writestr(special_info, b"special")

    with pytest.raises(ArchiveValidationError):
        await validator(tmp_path).validate(special)


async def test_rejects_unapproved_compression_method(tmp_path: Path) -> None:
    fixture = build_restore_archive(tmp_path)
    unsupported = tmp_path / "unsupported.tracemind.zip"
    with ZipFile(fixture.path) as source, ZipFile(unsupported, mode="w") as destination:
        for info in source.infolist():
            destination.writestr(info.filename, source.read(info.filename))
        destination.writestr("data/unsupported", b"value", compress_type=ZIP_BZIP2)

    with pytest.raises(ArchiveValidationError):
        await validator(tmp_path).validate(unsupported)


async def test_rejects_encrypted_entry_flag(tmp_path: Path) -> None:
    fixture = build_restore_archive(tmp_path)
    content = bytearray(fixture.path.read_bytes())
    local_offset = content.index(b"PK\x03\x04") + 6
    central_offset = content.index(b"PK\x01\x02") + 8
    for offset in [local_offset, central_offset]:
        flags = int.from_bytes(content[offset : offset + 2], "little") | 0x1
        content[offset : offset + 2] = flags.to_bytes(2, "little")
    encrypted = tmp_path / "encrypted.tracemind.zip"
    encrypted.write_bytes(content)

    with pytest.raises(ArchiveValidationError):
        await validator(tmp_path).validate(encrypted)


async def test_stream_upload_limit_cleans_temporary_file(tmp_path: Path) -> None:
    document_storage = LocalFileStorage(tmp_path / "uploads", max_size=1_000, chunk_size=4)
    archive_storage = LocalArchiveStorage(
        document_storage.root, limits(max_upload_size=4, io_chunk_size=2)
    )
    upload = UploadFile(filename="large.tracemind.zip", file=BytesIO(b"12345"))

    with pytest.raises(ArchiveLimitExceededError):
        await archive_storage.write_restore_upload(upload)

    assert list(archive_storage.upload_root.iterdir()) == []
