from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.exc import OperationalError

from app.api.routes.archives import (
    get_knowledge_base_archive_service,
    get_knowledge_base_restore_service,
)
from app.core.config import Settings
from app.main import create_app
from app.schemas.knowledge_base_archive import (
    ArchiveEntityCounts,
    ArchiveKnowledgeBaseSummary,
    KnowledgeBaseArchiveManifest,
    KnowledgeBaseArchiveRestoreResponse,
)
from app.services.exceptions import (
    ArchiveConflictError,
    ArchiveLimitExceededError,
    ArchiveSourceIntegrityError,
    ArchiveStorageError,
    ArchiveValidationError,
    KnowledgeBaseNotFoundError,
)
from app.services.knowledge_base_archive import (
    KnowledgeBaseArchiveExport,
    KnowledgeBaseArchiveService,
)
from app.services.knowledge_base_restore import KnowledgeBaseRestoreService


def manifest() -> KnowledgeBaseArchiveManifest:
    knowledge_base_id = uuid4()
    now = datetime.now(UTC)
    return KnowledgeBaseArchiveManifest(
        archive_id=uuid4(),
        tracemind_version="test",
        exported_at=now,
        knowledge_base=ArchiveKnowledgeBaseSummary(
            id=knowledge_base_id,
            name="Test",
            description=None,
            created_at=now,
            updated_at=now,
        ),
        entity_counts=ArchiveEntityCounts(
            documents=0,
            document_versions=0,
            conversations=0,
            messages=0,
            knowledge_entries=0,
        ),
        data_entries=[],
        document_files=[],
    )


def make_app(service: AsyncMock, tmp_path: Path) -> FastAPI:
    app = create_app(
        Settings(app_env="test", document_storage_root=tmp_path / "uploads", _env_file=None)
    )
    app.dependency_overrides[get_knowledge_base_archive_service] = lambda: service
    return app


async def client_for(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


async def request(app: FastAPI, method: str, path: str) -> Response:
    async for client in client_for(app):
        return await client.request(method, path)
    raise RuntimeError("Test client was not created")


def make_service() -> AsyncMock:
    return AsyncMock(spec=KnowledgeBaseArchiveService)


def make_restore_service() -> AsyncMock:
    return AsyncMock(spec=KnowledgeBaseRestoreService)


def make_restore_app(service: AsyncMock, tmp_path: Path) -> FastAPI:
    app = create_app(
        Settings(app_env="test", document_storage_root=tmp_path / "uploads", _env_file=None)
    )
    app.dependency_overrides[get_knowledge_base_restore_service] = lambda: service
    return app


async def test_export_downloads_zip_and_cleans_temporary_file(tmp_path: Path) -> None:
    service = make_service()
    archive_path = tmp_path / "fixture.tracemind.zip"
    archive_path.write_bytes(b"zip-content")
    service.export.return_value = KnowledgeBaseArchiveExport(
        archive_path,
        "Backend-Notes.tracemind.zip",
        manifest(),
    )
    knowledge_base_id = uuid4()

    response = await request(
        make_app(service, tmp_path),
        "GET",
        f"/api/v1/knowledge-bases/{knowledge_base_id}/archive",
    )

    assert response.status_code == 200
    assert response.content == b"zip-content"
    assert response.headers["content-type"] == "application/zip"
    assert "Backend-Notes.tracemind.zip" in response.headers["content-disposition"]
    service.export.assert_awaited_once_with(knowledge_base_id)
    service.discard_export.assert_awaited_once_with(archive_path)


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (KnowledgeBaseNotFoundError(uuid4()), 404, "Knowledge base not found"),
        (
            ArchiveSourceIntegrityError("changed"),
            409,
            "Stored document changed while the archive was being created; please retry",
        ),
        (
            ArchiveLimitExceededError("too large"),
            413,
            "Knowledge base exceeds the configured archive limits",
        ),
        (
            ArchiveStorageError("private path C:/secret"),
            500,
            "Knowledge base archive could not be created",
        ),
    ],
)
async def test_export_maps_expected_errors(
    tmp_path: Path,
    error: Exception,
    expected_status: int,
    expected_detail: str,
) -> None:
    service = make_service()
    service.export.side_effect = error

    response = await request(
        make_app(service, tmp_path),
        "GET",
        f"/api/v1/knowledge-bases/{uuid4()}/archive",
    )

    assert response.status_code == expected_status
    assert response.json()["detail"] == expected_detail
    assert "C:/secret" not in response.text
    service.discard_export.assert_not_awaited()


async def test_database_error_does_not_leak_details(tmp_path: Path) -> None:
    service = make_service()
    service.export.side_effect = OperationalError(
        "SELECT secret FROM private_table",
        {},
        Exception("postgresql://private"),
    )

    response = await request(
        make_app(service, tmp_path),
        "GET",
        f"/api/v1/knowledge-bases/{uuid4()}/archive",
    )

    assert response.status_code == 500
    assert "secret" not in response.text
    assert "private" not in response.text


async def test_restore_returns_source_status_without_claiming_rebuild(tmp_path: Path) -> None:
    service = make_restore_service()
    knowledge_base_id, archive_id = uuid4(), uuid4()
    service.restore.return_value = KnowledgeBaseArchiveRestoreResponse(
        knowledge_base_id=knowledge_base_id,
        archive_id=archive_id,
        entity_counts=ArchiveEntityCounts(
            documents=1,
            document_versions=1,
            conversations=1,
            messages=2,
            knowledge_entries=1,
        ),
    )

    async for client in client_for(make_restore_app(service, tmp_path)):
        response = await client.post(
            "/api/v1/knowledge-base-archives/restore",
            files={"file": ("backup.tracemind.zip", b"archive", "application/zip")},
        )

    assert response.status_code == 201
    assert response.json()["knowledge_base_id"] == str(knowledge_base_id)
    assert response.json()["archive_id"] == str(archive_id)
    assert response.json()["restore_status"] == "succeeded"
    assert response.json()["rebuild_status"] == "not_started"
    restored_upload = service.restore.await_args.args[0]
    assert restored_upload.filename == "backup.tracemind.zip"


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (ArchiveConflictError(["knowledge_base_id"]), 409),
        (ArchiveValidationError("invalid"), 422),
        (ArchiveLimitExceededError("large"), 413),
        (ArchiveStorageError("C:/private"), 500),
    ],
)
async def test_restore_error_mapping_is_safe(
    tmp_path: Path, error: Exception, status_code: int
) -> None:
    service = make_restore_service()
    service.restore.side_effect = error

    async for client in client_for(make_restore_app(service, tmp_path)):
        response = await client.post(
            "/api/v1/knowledge-base-archives/restore",
            files={"file": ("backup.tracemind.zip", b"archive", "application/zip")},
        )

    assert response.status_code == status_code
    assert "C:/private" not in response.text
    assert "rebuild" not in response.text.lower()
