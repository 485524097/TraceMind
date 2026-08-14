from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from app.core.config import Settings
from app.indexing import HybridSearchBatch, VectorSearchHit
from app.repositories.document_indexing import ActiveGeneration
from app.repositories.knowledge_entry_indexing import ActiveKnowledgeGeneration
from app.services.document_indexing import DocumentIndexingService
from app.services.rag_retrieval import KnowledgeSearchResult, RagRetrievalService
from app.services.retrieval_query import PreparedRetrievalQuery


class FakeProvider:
    model_name = "fake"
    dimension = 3

    def embed_query(self, _text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


class FakeDocumentRepository:
    def __init__(self, generation: ActiveGeneration) -> None:
        self.generation = generation

    async def list_active_generations(
        self, _knowledge_base_id: object, *, document_id: object | None
    ) -> list[ActiveGeneration]:
        if document_id is not None and document_id != self.generation.document_id:
            return []
        return [self.generation]


class FakeDocumentService:
    def __init__(self, repository: FakeDocumentRepository) -> None:
        self.repository = repository

    async def prepare_retrieval_query(
        self, _knowledge_base_id: object, query: str, *, document_id: object | None
    ) -> PreparedRetrievalQuery:
        return PreparedRetrievalQuery(query, query, cast(object, document_id), "none", None)

    async def list_active_generations(
        self, _knowledge_base_id: object, *, document_id: object | None
    ) -> list[ActiveGeneration]:
        return await self.repository.list_active_generations(
            _knowledge_base_id, document_id=document_id
        )

    search_result = staticmethod(DocumentIndexingService.search_result)


class FakeKnowledgeRepository:
    def __init__(self, generation: ActiveKnowledgeGeneration) -> None:
        self.generation = generation

    async def list_active_generations(
        self, _knowledge_base_id: object
    ) -> list[ActiveKnowledgeGeneration]:
        return [self.generation]


class FakeGateway:
    def __init__(self, hits: list[VectorSearchHit]) -> None:
        self.hits = hits

    async def ensure_collection(self) -> None:
        pass

    async def hybrid_search_with_diagnostics(
        self, *_args: object, **_kwargs: object
    ) -> HybridSearchBatch:
        return HybridSearchBatch(self.hits, 2, 1, len(self.hits), len(self.hits))


async def test_rag_retrieval_fuses_document_and_verified_knowledge_generations() -> None:
    knowledge_base_id = uuid4()
    document_id, version_id, document_chunk_id = uuid4(), uuid4(), uuid4()
    document_generation = uuid4()
    knowledge_entry_id, knowledge_chunk_id, knowledge_generation = uuid4(), uuid4(), uuid4()
    hits = [
        VectorSearchHit(
            0.8,
            {
                "source_type": "document",
                "knowledge_base_id": str(knowledge_base_id),
                "document_id": str(document_id),
                "document_version_id": str(version_id),
                "chunk_id": str(document_chunk_id),
                "index_generation": str(document_generation),
                "document_name": "guide.md",
                "relative_path": "docs/guide.md",
                "version_number": 1,
                "chunk_index": 0,
                "content": "original document",
                "content_hash": "a" * 64,
                "chunk_type": "paragraph",
                "language": "markdown",
            },
        ),
        VectorSearchHit(
            0.7,
            {
                "source_type": "knowledge_entry",
                "knowledge_base_id": str(knowledge_base_id),
                "knowledge_entry_id": str(knowledge_entry_id),
                "chunk_id": str(knowledge_chunk_id),
                "index_generation": str(knowledge_generation),
                "knowledge_question": "事务为什么失败？",
                "knowledge_updated_at": datetime.now(UTC).isoformat(),
                "chunk_index": 0,
                "content": "使用同一个事务。",
                "content_hash": "b" * 64,
                "chunk_type": "knowledge_entry",
                "section_title": "Solution",
            },
        ),
    ]
    service = RagRetrievalService(
        cast(
            DocumentIndexingService,
            FakeDocumentService(
                FakeDocumentRepository(
                    ActiveGeneration(document_id, version_id, document_generation)
                )
            ),
        ),
        Settings(embedding_dimension=3),
        FakeProvider(),
        cast(object, FakeGateway(hits)),
        cast(
            object,
            FakeKnowledgeRepository(
                ActiveKnowledgeGeneration(knowledge_entry_id, knowledge_generation)
            ),
        ),
    )

    prepared = await service.prepare_hybrid_search(
        knowledge_base_id,
        query="事务失败",
        limit=5,
        language=None,
        document_id=None,
    )
    result = await service.execute_hybrid_search(prepared)

    assert set(prepared.generations) == {document_generation, knowledge_generation}
    assert len(result.items) == 2
    knowledge = result.items[1]
    assert isinstance(knowledge, KnowledgeSearchResult)
    assert knowledge.knowledge_entry_id == knowledge_entry_id
    assert knowledge.title == "事务为什么失败？"
