from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.routes.knowledge_map import get_knowledge_map_service
from app.main import create_app
from app.schemas.knowledge_map import KnowledgeMapResponse
from app.services.exceptions import KnowledgeBaseNotFoundError


def test_get_knowledge_map_contract() -> None:
    app = create_app()
    service = AsyncMock()
    service.get.return_value = KnowledgeMapResponse(nodes=[], edges=[])
    app.dependency_overrides[get_knowledge_map_service] = lambda: service

    knowledge_base_id = uuid4()
    with TestClient(app) as client:
        response = client.get(f"/api/v1/knowledge-bases/{knowledge_base_id}/knowledge-map")

    assert response.status_code == 200
    assert response.json() == {"nodes": [], "edges": []}
    service.get.assert_awaited_once_with(knowledge_base_id)


def test_get_knowledge_map_missing_knowledge_base() -> None:
    app = create_app()
    service = AsyncMock()
    service.get.side_effect = KnowledgeBaseNotFoundError(uuid4())
    app.dependency_overrides[get_knowledge_map_service] = lambda: service

    with TestClient(app) as client:
        response = client.get(f"/api/v1/knowledge-bases/{uuid4()}/knowledge-map")

    assert response.status_code == 404
    assert response.json() == {"detail": "Knowledge base not found"}
