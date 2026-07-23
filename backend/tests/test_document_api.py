from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.exc import OperationalError

from app.api.routes.documents import get_document_parsing_service, get_document_service
from app.core.config import Settings
from app.main import create_app
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.repositories.document import DocumentRecord
from app.schemas.document import DocumentImportAction
from app.services.document import DocumentDownload, DocumentImportResult, DocumentService
from app.services.document_parsing import (
    ChunkPage,
    DocumentParsingService,
    ParseRequestResult,
)
from app.services.exceptions import (
    DocumentImportConflictError,
    DocumentNotFoundError,
    DocumentParsingQueueError,
    DocumentStorageError,
    DocumentTooLargeError,
    EmptyDocumentError,
    UnsupportedDocumentTypeError,
)


def make_record() -> DocumentRecord:
    now = datetime.now(UTC)
    knowledge_base_id, document_id = uuid4(), uuid4()
    document = Document(
        id=document_id,
        knowledge_base_id=knowledge_base_id,
        name="设计说明.md",
        normalized_name="设计说明.md",
        source_type="upload",
        created_at=now,
        updated_at=now,
    )
    version = DocumentVersion(
        id=uuid4(),
        document_id=document_id,
        version_number=1,
        content_hash="a" * 64,
        file_size=7,
        mime_type="text/markdown",
        extension=".md",
        storage_path="hidden/path/content.md",
        parse_status="pending",
        parser_name=None,
        parser_version=None,
        chunk_count=0,
        parse_started_at=None,
        parsed_at=None,
        last_parse_attempt_at=None,
        parse_error_code=None,
        parse_error_message=None,
        index_status="pending",
        active_index_generation=None,
        index_started_at=None,
        indexed_at=None,
        last_index_attempt_at=None,
        indexed_chunk_count=0,
        embedding_model=None,
        embedding_dimension=None,
        index_error_code=None,
        index_error_message=None,
        created_at=now,
    )
    return DocumentRecord(document, version, 1)


def make_service() -> AsyncMock:
    return AsyncMock(spec=DocumentService)


def make_app(service: AsyncMock, parsing_service: AsyncMock | None = None) -> FastAPI:
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_document_service] = lambda: service
    if parsing_service is not None:
        app.dependency_overrides[get_document_parsing_service] = lambda: parsing_service
    return app


async def client_for(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


async def request(app: FastAPI, method: str, path: str, **kwargs: object) -> Response:
    async for client in client_for(app):
        return await client.request(method, path, **kwargs)
    raise RuntimeError("Test client was not created")


@pytest.mark.parametrize(
    ("action", "expected_status"),
    [
        (DocumentImportAction.created, 201),
        (DocumentImportAction.version_created, 201),
        (DocumentImportAction.unchanged, 200),
    ],
)
async def test_upload_actions_and_multipart_field(
    action: DocumentImportAction, expected_status: int
) -> None:
    service = make_service()
    record = make_record()
    service.import_document.return_value = DocumentImportResult(action, record)

    response = await request(
        make_app(service),
        "POST",
        f"/api/v1/knowledge-bases/{record.document.knowledge_base_id}/documents",
        files={"file": ("设计说明.md", b"content", "text/markdown")},
    )

    assert response.status_code == expected_status
    assert response.json()["import_action"] == action.value
    assert response.json()["parsing_queued"] is False
    assert "storage_path" not in response.text
    uploaded = service.import_document.await_args.args[1]
    assert uploaded.filename == "设计说明.md"


async def test_missing_multipart_file_returns_422() -> None:
    response = await request(
        make_app(make_service()),
        "POST",
        f"/api/v1/knowledge-bases/{uuid4()}/documents",
    )
    assert response.status_code == 422


async def test_list_pagination_and_name_search() -> None:
    service = make_service()
    record = make_record()
    service.list_documents.return_value = ([record], 1)

    response = await request(
        make_app(service),
        "GET",
        f"/api/v1/knowledge-bases/{record.document.knowledge_base_id}/documents"
        "?offset=5&limit=10&query=设计",
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    service.list_documents.assert_awaited_once_with(
        record.document.knowledge_base_id, offset=5, limit=10, query="设计"
    )


async def test_document_detail_and_versions() -> None:
    service = make_service()
    record = make_record()
    service.get_document.return_value = record
    service.list_versions.return_value = [record.latest_version]
    base = (
        f"/api/v1/knowledge-bases/{record.document.knowledge_base_id}"
        f"/documents/{record.document.id}"
    )

    detail = await request(make_app(service), "GET", base)
    versions = await request(make_app(service), "GET", f"{base}/versions")

    assert detail.status_code == 200
    assert detail.json()["latest_version"]["version_number"] == 1
    assert versions.status_code == 200
    assert versions.json()[0]["id"] == str(record.latest_version.id)


async def test_current_and_historical_downloads(tmp_path: Path) -> None:
    service = make_service()
    record = make_record()
    path = tmp_path / "content.md"
    path.write_bytes(b"download")
    service.download_current.return_value = DocumentDownload(
        path, record.document.name, "text/markdown"
    )
    service.download_version.return_value = DocumentDownload(
        path, record.document.name, "text/markdown"
    )
    base = (
        f"/api/v1/knowledge-bases/{record.document.knowledge_base_id}"
        f"/documents/{record.document.id}"
    )

    current = await request(make_app(service), "GET", f"{base}/download")
    historical = await request(
        make_app(service), "GET", f"{base}/versions/{record.latest_version.id}/download"
    )

    assert current.status_code == historical.status_code == 200
    assert current.content == historical.content == b"download"
    assert "filename*=utf-8''" in current.headers["content-disposition"].lower()


async def test_delete_returns_204() -> None:
    service = make_service()
    record = make_record()
    response = await request(
        make_app(service),
        "DELETE",
        f"/api/v1/knowledge-bases/{record.document.knowledge_base_id}"
        f"/documents/{record.document.id}",
    )
    assert response.status_code == 204
    assert response.content == b""


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (DocumentNotFoundError("missing"), 404),
        (DocumentImportConflictError("race"), 409),
        (DocumentTooLargeError("large"), 413),
        (UnsupportedDocumentTypeError("type"), 415),
        (EmptyDocumentError("empty"), 422),
    ],
)
async def test_upload_domain_error_mapping(error: Exception, status_code: int) -> None:
    service = make_service()
    service.import_document.side_effect = error
    response = await request(
        make_app(service),
        "POST",
        f"/api/v1/knowledge-bases/{uuid4()}/documents",
        files={"file": ("sample.md", b"content")},
    )
    assert response.status_code == status_code


@pytest.mark.parametrize(
    "error",
    [
        DocumentStorageError("private-secret-location"),
        OperationalError("SELECT secret", {}, Exception("postgresql://private")),
    ],
)
async def test_internal_errors_return_generic_500(error: Exception) -> None:
    service = make_service()
    service.list_documents.side_effect = error
    response = await request(
        make_app(service),
        "GET",
        f"/api/v1/knowledge-bases/{uuid4()}/documents",
    )

    assert response.status_code == 500
    assert "private" not in response.text
    assert "SELECT" not in response.text


@pytest.mark.parametrize(("queued", "status_code"), [(True, 202), (False, 200)])
async def test_manual_parse_dynamic_status(queued: bool, status_code: int) -> None:
    document_service = make_service()
    parsing_service = AsyncMock(spec=DocumentParsingService)
    record = make_record()
    parsing_service.request_parse.return_value = ParseRequestResult(queued, record.latest_version)
    path = (
        f"/api/v1/knowledge-bases/{record.document.knowledge_base_id}"
        f"/documents/{record.document.id}/versions/{record.latest_version.id}/parse?force=true"
    )

    response = await request(make_app(document_service, parsing_service), "POST", path)

    assert response.status_code == status_code
    assert response.json()["queued"] is queued
    parsing_service.request_parse.assert_awaited_once_with(
        record.document.knowledge_base_id,
        record.document.id,
        record.latest_version.id,
        force=True,
    )


async def test_manual_parse_queue_unavailable_returns_503() -> None:
    parsing_service = AsyncMock(spec=DocumentParsingService)
    parsing_service.request_parse.side_effect = DocumentParsingQueueError("redis://private")
    record = make_record()
    path = (
        f"/api/v1/knowledge-bases/{record.document.knowledge_base_id}"
        f"/documents/{record.document.id}/versions/{record.latest_version.id}/parse"
    )

    response = await request(make_app(make_service(), parsing_service), "POST", path)

    assert response.status_code == 503
    assert "private" not in response.text


async def test_parse_status_and_ordered_chunk_page() -> None:
    parsing_service = AsyncMock(spec=DocumentParsingService)
    record = make_record()
    version = record.latest_version
    version.parse_status = "succeeded"
    version.parser_name = "markdown"
    version.parser_version = "1"
    version.chunk_count = 1
    now = datetime.now(UTC)
    chunk = DocumentChunk(
        id=uuid4(),
        document_version_id=version.id,
        chunk_index=0,
        content="正文",
        content_hash="b" * 64,
        char_count=2,
        page_number=None,
        start_line=2,
        end_line=2,
        section_title="说明",
        chunk_type="paragraph",
        language=None,
        created_at=now,
    )
    parsing_service.get_status.return_value = version
    parsing_service.list_chunks.return_value = ChunkPage(version, [chunk], 1)
    base = (
        f"/api/v1/knowledge-bases/{record.document.knowledge_base_id}"
        f"/documents/{record.document.id}/versions/{version.id}"
    )

    status_response = await request(
        make_app(make_service(), parsing_service), "GET", f"{base}/parse-status"
    )
    chunks_response = await request(
        make_app(make_service(), parsing_service), "GET", f"{base}/chunks?offset=0&limit=10"
    )

    assert status_response.status_code == 200
    assert status_response.json()["parse_status"] == "succeeded"
    assert chunks_response.status_code == 200
    assert chunks_response.json()["items"][0]["section_title"] == "说明"
    assert "storage_path" not in chunks_response.text
