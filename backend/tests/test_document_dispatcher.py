from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.services.document_dispatcher import CeleryDocumentParsingDispatcher
from app.services.exceptions import DocumentParsingQueueError


async def test_dispatcher_passes_uuid_and_force(monkeypatch: pytest.MonkeyPatch) -> None:
    delay = Mock()
    monkeypatch.setattr("app.tasks.documents.parse_document_version.delay", delay)
    version_id = uuid4()

    await CeleryDocumentParsingDispatcher().enqueue(version_id, force=True)

    delay.assert_called_once_with(str(version_id), force=True)


async def test_dispatcher_converts_celery_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    delay = Mock(side_effect=RuntimeError("redis://private"))
    monkeypatch.setattr("app.tasks.documents.parse_document_version.delay", delay)

    with pytest.raises(DocumentParsingQueueError) as exc_info:
        await CeleryDocumentParsingDispatcher().enqueue(uuid4())

    assert str(exc_info.value) == "Document parsing queue is unavailable"
    assert "private" not in str(exc_info.value)
