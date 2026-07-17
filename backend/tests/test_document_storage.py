import hashlib
import os
import unicodedata
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.services.exceptions import (
    DocumentStorageError,
    DocumentTooLargeError,
    EmptyDocumentError,
    InvalidDocumentNameError,
    UnsupportedDocumentTypeError,
)
from app.storage.local import LocalFileStorage
from app.storage.names import normalize_document_name

ALLOWED = {".md", ".txt", ".pdf"}


def upload(filename: str, content: bytes, content_type: str = "text/plain") -> UploadFile:
    return UploadFile(
        file=BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


def storage(tmp_path: Path, *, max_size: int = 1024, chunk_size: int = 3) -> LocalFileStorage:
    return LocalFileStorage(tmp_path / "uploads", max_size=max_size, chunk_size=chunk_size)


def test_normalizes_chinese_uppercase_and_windows_paths() -> None:
    result = normalize_document_name(r"C:\资料\设计文档.MD", ALLOWED)

    assert result.display_name == "设计文档.MD"
    assert result.normalized_name == "设计文档.md"
    assert result.extension == ".md"


def test_normalizes_unicode_nfc_and_discards_parent_segments() -> None:
    decomposed = "cafe\u0301.md"
    result = normalize_document_name(f"../../folder/{decomposed}", ALLOWED)

    assert result.display_name == unicodedata.normalize("NFC", decomposed)
    assert result.normalized_name == "café.md"


@pytest.mark.parametrize("filename", ["", "   ", ".", "..", "bad\x00.md", "a" * 256])
def test_rejects_invalid_names(filename: str) -> None:
    with pytest.raises(InvalidDocumentNameError):
        normalize_document_name(filename, ALLOWED)


def test_rejects_unsupported_extension() -> None:
    with pytest.raises(UnsupportedDocumentTypeError):
        normalize_document_name("archive.exe", ALLOWED)


async def test_streams_upload_and_calculates_hash_and_size(tmp_path: Path) -> None:
    content = b"abcdefghi"
    result = await storage(tmp_path).write_upload(upload("sample.md", content))

    assert result.path.read_bytes() == content
    assert result.content_hash == hashlib.sha256(content).hexdigest()
    assert result.file_size == len(content)


async def test_rejects_empty_file_and_cleans_temporary_file(tmp_path: Path) -> None:
    local = storage(tmp_path)

    with pytest.raises(EmptyDocumentError):
        await local.write_upload(upload("empty.md", b""))

    assert list(local.temp_root.iterdir()) == []


async def test_rejects_large_file_and_cleans_temporary_file(tmp_path: Path) -> None:
    local = storage(tmp_path, max_size=4, chunk_size=2)

    with pytest.raises(DocumentTooLargeError):
        await local.write_upload(upload("large.md", b"12345"))

    assert list(local.temp_root.iterdir()) == []


async def test_interrupted_read_closes_handle_and_cleans_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = storage(tmp_path)
    interrupted_upload = AsyncMock(spec=UploadFile)
    interrupted_upload.read.side_effect = [b"first", RuntimeError("connection interrupted")]
    original_fdopen = os.fdopen
    handles: list[object] = []

    def tracked_fdopen(descriptor: int, mode: str) -> object:
        handle = original_fdopen(descriptor, mode)
        handles.append(handle)
        return handle

    monkeypatch.setattr(os, "fdopen", tracked_fdopen)

    with pytest.raises(DocumentStorageError) as exc_info:
        await local.write_upload(interrupted_upload)

    assert str(exc_info.value) == "Document upload could not be stored"
    assert list(local.temp_root.iterdir()) == []
    assert len(handles) == 1
    assert handles[0].closed
    assert interrupted_upload.read.await_count == 2


async def test_final_path_uses_only_uuids_and_stays_under_root(tmp_path: Path) -> None:
    local = storage(tmp_path)
    temporary = await local.write_upload(upload("中文.md", b"safe"))
    knowledge_base_id, document_id, version_id = uuid4(), uuid4(), uuid4()
    relative = local.final_relative_path(knowledge_base_id, document_id, version_id, ".md")

    final = await local.finalize(temporary.path, relative)

    assert final.read_bytes() == b"safe"
    assert final.is_relative_to(local.root)
    assert relative == f"{knowledge_base_id}/{document_id}/{version_id}/content.md"


@pytest.mark.parametrize("relative", ["../secret.md", "/absolute/file.md", "a/../../secret.md"])
def test_rejects_unsafe_relative_paths(tmp_path: Path, relative: str) -> None:
    with pytest.raises(DocumentStorageError):
        storage(tmp_path).resolve_relative(relative, must_exist=False)


async def test_stage_restore_and_purge_document_directory(tmp_path: Path) -> None:
    local = storage(tmp_path)
    knowledge_base_id, document_id, version_id = uuid4(), uuid4(), uuid4()
    temporary = await local.write_upload(upload("sample.md", b"version"))
    relative = local.final_relative_path(knowledge_base_id, document_id, version_id, ".md")
    final = await local.finalize(temporary.path, relative)

    staged = await local.stage_document_deletion(knowledge_base_id, document_id)
    assert not final.exists()
    assert staged.trash_path.exists()

    await local.restore_staged(staged)
    assert final.exists()

    staged = await local.stage_document_deletion(knowledge_base_id, document_id)
    await local.purge_staged(staged)
    assert not staged.trash_path.exists()
