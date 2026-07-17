import asyncio
import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from fastapi import UploadFile

from app.services.exceptions import (
    DocumentStorageError,
    DocumentTooLargeError,
    EmptyDocumentError,
)


@dataclass(frozen=True)
class TemporaryUpload:
    path: Path
    content_hash: str
    file_size: int


@dataclass(frozen=True)
class StagedDeletion:
    original_path: Path
    trash_path: Path


class LocalFileStorage:
    def __init__(self, root: Path, *, max_size: int, chunk_size: int) -> None:
        self.root = root.expanduser().resolve()
        self.max_size = max_size
        self.chunk_size = chunk_size
        self.temp_root = self.root / ".upload-tmp"
        self.trash_root = self.root / ".trash"
        self.root.mkdir(parents=True, exist_ok=True)
        self.temp_root.mkdir(exist_ok=True)
        self.trash_root.mkdir(exist_ok=True)

    async def write_upload(self, upload: UploadFile) -> TemporaryUpload:
        descriptor, raw_path = await asyncio.to_thread(
            tempfile.mkstemp, prefix="upload-", dir=self.temp_root
        )
        path = Path(raw_path)
        digest = hashlib.sha256()
        size = 0
        file_handle = await asyncio.to_thread(os.fdopen, descriptor, "wb")
        try:
            while chunk := await upload.read(self.chunk_size):
                size += len(chunk)
                if size > self.max_size:
                    raise DocumentTooLargeError("Document exceeds configured size limit")
                digest.update(chunk)
                await asyncio.to_thread(file_handle.write, chunk)
            await asyncio.to_thread(file_handle.flush)
            if size == 0:
                raise EmptyDocumentError("Empty documents are not allowed")
            return TemporaryUpload(path=path, content_hash=digest.hexdigest(), file_size=size)
        except (DocumentTooLargeError, EmptyDocumentError):
            await asyncio.to_thread(file_handle.close)
            await asyncio.to_thread(self._unlink_if_exists, path)
            raise
        except OSError as exc:
            await asyncio.to_thread(file_handle.close)
            await asyncio.to_thread(self._unlink_if_exists, path)
            raise DocumentStorageError("Document upload could not be stored") from exc
        finally:
            await asyncio.to_thread(file_handle.close)

    def final_relative_path(
        self,
        knowledge_base_id: UUID,
        document_id: UUID,
        version_id: UUID,
        extension: str,
    ) -> str:
        return PurePosixPath(
            str(knowledge_base_id),
            str(document_id),
            str(version_id),
            f"content{extension}",
        ).as_posix()

    async def finalize(self, temporary_path: Path, relative_path: str) -> Path:
        try:
            return await asyncio.to_thread(self._finalize, temporary_path, relative_path)
        except OSError as exc:
            raise DocumentStorageError("Document upload could not be finalized") from exc

    def _finalize(self, temporary_path: Path, relative_path: str) -> Path:
        destination = self.resolve_relative(relative_path, must_exist=False)
        destination.parent.mkdir(parents=True, exist_ok=False)
        try:
            os.rename(temporary_path, destination)
        except OSError:
            shutil.rmtree(destination.parent, ignore_errors=True)
            raise
        return destination

    async def discard_temporary(self, path: Path) -> None:
        await asyncio.to_thread(self._unlink_if_exists, path)

    async def remove_final_version(self, relative_path: str) -> None:
        path = self.resolve_relative(relative_path, must_exist=False)
        await asyncio.to_thread(self._remove_version_directory, path)

    def _remove_version_directory(self, path: Path) -> None:
        shutil.rmtree(path.parent, ignore_errors=True)
        self._prune_empty_parents(path.parent.parent)

    def resolve_relative(self, relative_path: str, *, must_exist: bool = True) -> Path:
        pure_path = PurePosixPath(relative_path)
        if pure_path.is_absolute() or not pure_path.parts or ".." in pure_path.parts:
            raise DocumentStorageError("Invalid document storage path")
        candidate = (self.root / Path(*pure_path.parts)).resolve(strict=False)
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise DocumentStorageError("Invalid document storage path") from exc
        if must_exist and (not candidate.is_file() or candidate.is_symlink()):
            raise DocumentStorageError("Stored document is unavailable")
        return candidate

    async def stage_document_deletion(
        self, knowledge_base_id: UUID, document_id: UUID
    ) -> StagedDeletion:
        try:
            return await asyncio.to_thread(
                self._stage_document_deletion, knowledge_base_id, document_id
            )
        except OSError as exc:
            raise DocumentStorageError("Document could not be staged for deletion") from exc

    def _stage_document_deletion(
        self, knowledge_base_id: UUID, document_id: UUID
    ) -> StagedDeletion:
        original = self.resolve_relative(f"{knowledge_base_id}/{document_id}", must_exist=False)
        if not original.is_dir() or original.is_symlink():
            raise DocumentStorageError("Stored document is unavailable")
        trash = self.trash_root / str(uuid4())
        os.rename(original, trash)
        return StagedDeletion(original_path=original, trash_path=trash)

    async def restore_staged(self, staged: StagedDeletion) -> None:
        try:
            await asyncio.to_thread(self._restore_staged, staged)
        except OSError as exc:
            raise DocumentStorageError("Document deletion could not be restored") from exc

    @staticmethod
    def _restore_staged(staged: StagedDeletion) -> None:
        staged.original_path.parent.mkdir(parents=True, exist_ok=True)
        os.rename(staged.trash_path, staged.original_path)

    async def purge_staged(self, staged: StagedDeletion) -> None:
        try:
            await asyncio.to_thread(self._purge_staged, staged)
        except OSError as exc:
            raise DocumentStorageError("Staged document files could not be removed") from exc

    def _purge_staged(self, staged: StagedDeletion) -> None:
        shutil.rmtree(staged.trash_path)
        self._prune_empty_parents(staged.original_path.parent)

    def _prune_empty_parents(self, path: Path) -> None:
        current = path
        while current != self.root:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    @staticmethod
    def _unlink_if_exists(path: Path) -> None:
        path.unlink(missing_ok=True)
