from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_entry import KnowledgeEntry
from app.repositories.document import DocumentRepository
from app.repositories.knowledge_base import KnowledgeBaseRepository
from app.repositories.knowledge_entry import KnowledgeEntryRepository
from app.services.exceptions import KnowledgeBaseNotFoundError
from app.services.knowledge_map import KnowledgeMapService


def make_entry(
    knowledge_base_id: UUID,
    *,
    tags: list[str],
    document_ids: list[UUID],
) -> KnowledgeEntry:
    return KnowledgeEntry(
        id=uuid4(),
        knowledge_base_id=knowledge_base_id,
        question=f"Question {uuid4()}",
        solution="Solution",
        failed_attempts=[],
        validation_status="verified",
        tags=tags,
        question_snapshot="Question",
        answer_snapshot="Answer",
        sources_snapshot=[
            {"source_id": f"S{index}", "document_id": str(document_id)}
            for index, document_id in enumerate(document_ids, start=1)
        ],
        updated_at=datetime.now(UTC),
    )


def make_service() -> tuple[
    KnowledgeMapService,
    AsyncMock,
    AsyncMock,
    AsyncMock,
]:
    session = AsyncMock(spec=AsyncSession)
    knowledge_bases = AsyncMock(spec=KnowledgeBaseRepository)
    documents = AsyncMock(spec=DocumentRepository)
    entries = AsyncMock(spec=KnowledgeEntryRepository)
    return (
        KnowledgeMapService(
            cast(AsyncSession, session),
            cast(KnowledgeBaseRepository, knowledge_bases),
            cast(DocumentRepository, documents),
            cast(KnowledgeEntryRepository, entries),
        ),
        knowledge_bases,
        documents,
        entries,
    )


async def test_derives_stable_nodes_edges_and_transparent_related_reasons() -> None:
    service, knowledge_bases, documents, entries = make_service()
    knowledge_base_id = uuid4()
    live_document_id = uuid4()
    deleted_document_id = uuid4()
    knowledge_bases.get_by_id.return_value = KnowledgeBase(id=knowledge_base_id, name="Engineering")
    documents.list_all.return_value = [
        Document(
            id=live_document_id,
            knowledge_base_id=knowledge_base_id,
            name="transactions.md",
            normalized_name="transactions.md",
            relative_path="docs/transactions.md",
            normalized_path="docs/transactions.md",
            source_type="upload",
        )
    ]
    first = make_entry(
        knowledge_base_id,
        tags=["postgres", "transaction"],
        document_ids=[live_document_id, live_document_id, deleted_document_id],
    )
    second = make_entry(
        knowledge_base_id,
        tags=["postgres"],
        document_ids=[live_document_id],
    )
    entries.list_all.return_value = [first, second]

    result = await service.get(knowledge_base_id)

    assert {node.type for node in result.nodes} == {
        "knowledge_base",
        "knowledge_entry",
        "document",
        "tag",
    }
    assert {node.id for node in result.nodes if node.type == "tag"} == {
        "tag:postgres",
        "tag:transaction",
    }
    cite_edges = [edge for edge in result.edges if edge.type == "cites"]
    assert len(cite_edges) == 2
    assert all(str(deleted_document_id) not in edge.target for edge in cite_edges)
    related = [edge for edge in result.edges if edge.type == "related"]
    assert len(related) == 1
    assert related[0].metadata == {
        "shared_tags": ["postgres"],
        "shared_document_ids": [str(live_document_id)],
    }
    assert first.sources_snapshot[2]["document_id"] == str(deleted_document_id)


async def test_empty_map_and_missing_knowledge_base() -> None:
    service, knowledge_bases, documents, entries = make_service()
    knowledge_base_id = uuid4()
    knowledge_bases.get_by_id.return_value = KnowledgeBase(id=knowledge_base_id, name="Empty")
    documents.list_all.return_value = []
    entries.list_all.return_value = []

    result = await service.get(knowledge_base_id)
    assert [node.type for node in result.nodes] == ["knowledge_base"]
    assert result.edges == []

    knowledge_bases.get_by_id.return_value = None
    with pytest.raises(KnowledgeBaseNotFoundError):
        await service.get(uuid4())
