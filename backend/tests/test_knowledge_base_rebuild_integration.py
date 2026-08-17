import asyncio
import hashlib
import os
import shutil
from collections.abc import AsyncIterator
from io import BytesIO
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic.config import Config
from fastapi import UploadFile
from qdrant_client import AsyncQdrantClient
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.core.config import Settings, get_settings
from app.indexing import QdrantGateway, VectorPoint
from app.models.conversation import Conversation, ConversationMessage
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_base_rebuild import KnowledgeBaseRebuildOperation
from app.models.knowledge_entry import KnowledgeEntry
from app.repositories.document_indexing import DocumentIndexingRepository
from app.repositories.knowledge_entry_indexing import KnowledgeEntryIndexingRepository
from app.services.document_indexing import DocumentIndexingService
from app.services.document_parsing import DocumentParsingService
from app.services.knowledge_base_archive import KnowledgeBaseArchiveService
from app.services.knowledge_base_rebuild import (
    KnowledgeBaseRebuildExecutor,
    KnowledgeBaseRebuildService,
)
from app.services.knowledge_base_restore import KnowledgeBaseRestoreService
from app.services.knowledge_entry_indexing import KnowledgeEntryIndexingService
from app.services.rag_retrieval import RagRetrievalService
from app.storage.archive import ArchiveLimits, LocalArchiveStorage
from app.storage.local import LocalFileStorage

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
TEST_QDRANT_URL = os.getenv("TEST_QDRANT_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured"),
]


def require_test_database_url() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not configured")
    database_name = make_url(TEST_DATABASE_URL).database or ""
    if not database_name.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must point to a database ending in '_test'")
    return TEST_DATABASE_URL


def run_migration() -> None:
    os.environ["DATABASE_URL"] = require_test_database_url()
    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = require_test_database_url()
    await asyncio.to_thread(run_migration)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


class DeterministicEmbeddingProvider:
    model_name = "stage17-test-embedding"
    dimension = 4

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> list[float]:
        lowered = text.lower()
        return [
            1.0 if "latest" in lowered else 0.1,
            1.0 if "second" in lowered else 0.1,
            1.0 if "verified" in lowered else 0.1,
            0.5,
        ]


class InMemoryQdrantGateway:
    def __init__(self) -> None:
        self.points: dict[UUID, VectorPoint] = {}

    async def ensure_collection(self) -> None:
        return None

    async def upsert(self, points: list[VectorPoint]) -> None:
        self.points.update({point.id: point for point in points})

    async def count_generation(self, generation: UUID) -> int:
        return sum(
            point.payload.get("index_generation") == str(generation)
            for point in self.points.values()
        )

    async def delete_generation(self, generation: UUID) -> None:
        self.points = {
            point_id: point
            for point_id, point in self.points.items()
            if point.payload.get("index_generation") != str(generation)
        }

    async def delete_knowledge_entry(self, entry_id: UUID) -> None:
        self.points = {
            point_id: point
            for point_id, point in self.points.items()
            if point.payload.get("knowledge_entry_id") != str(entry_id)
        }


class RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID]] = []

    async def enqueue(self, operation_id: UUID, run_generation: UUID) -> None:
        self.calls.append((operation_id, run_generation))


def archive_limits() -> ArchiveLimits:
    return ArchiveLimits(
        max_upload_size=1_000_000,
        max_single_file_size=100_000,
        max_total_extracted_size=500_000,
        max_entries=100,
        max_json_size=100_000,
        max_jsonl_records=1_000,
        max_compression_ratio=100.0,
        io_chunk_size=64,
    )


def version(
    storage: LocalFileStorage,
    document: Document,
    number: int,
    text: str,
) -> DocumentVersion:
    version_id = uuid4()
    content = text.encode()
    relative_path = storage.final_relative_path(
        document.knowledge_base_id,
        document.id,
        version_id,
        ".md",
    )
    path = storage.resolve_relative(relative_path, must_exist=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return DocumentVersion(
        id=version_id,
        document_id=document.id,
        version_number=number,
        content_hash=hashlib.sha256(content).hexdigest(),
        file_size=len(content),
        mime_type="text/markdown",
        extension=".md",
        storage_path=relative_path,
        parse_status="pending",
        index_status="pending",
    )


def entry(knowledge_base_id: UUID, status: str, question: str) -> KnowledgeEntry:
    return KnowledgeEntry(
        id=uuid4(),
        knowledge_base_id=knowledge_base_id,
        question=question,
        solution=f"{status} maintained solution",
        failed_attempts=[],
        validation_status=status,
        tags=["stage17"],
        question_snapshot=question,
        answer_snapshot="raw answer snapshot must not be indexed",
        sources_snapshot=[],
        index_status="pending" if status == "verified" else "not_indexed",
    )


async def test_real_postgresql_rebuilds_all_versions_and_only_latest_active_sources(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    knowledge_base_id = uuid4()
    storage = LocalFileStorage(tmp_path / "uploads", max_size=10_000, chunk_size=64)
    settings = Settings(
        app_env="test",
        document_storage_root=storage.root,
        embedding_dimension=4,
        document_chunk_max_chars=100,
        document_chunk_overlap_chars=10,
    )
    knowledge_base = KnowledgeBase(
        id=knowledge_base_id,
        name=f"Rebuild Integration {knowledge_base_id}",
    )
    first = Document(
        id=uuid4(),
        knowledge_base_id=knowledge_base_id,
        name="first.md",
        normalized_name="first.md",
        relative_path="first.md",
        normalized_path="first.md",
        source_type="upload",
    )
    second = Document(
        id=uuid4(),
        knowledge_base_id=knowledge_base_id,
        name="second.md",
        normalized_name="second.md",
        relative_path="second.md",
        normalized_path="second.md",
        source_type="upload",
    )
    v1 = version(storage, first, 1, "historical version content")
    v2 = version(storage, first, 2, "latest version content")
    second_v1 = version(storage, second, 1, "second document latest content")
    verified = entry(knowledge_base_id, "verified", "verified production incident")
    unverified = entry(knowledge_base_id, "unverified", "draft incident")
    outdated = entry(knowledge_base_id, "outdated", "obsolete incident")
    async with session_factory() as setup:
        setup.add_all(
            [
                knowledge_base,
                first,
                second,
                v1,
                v2,
                second_v1,
                verified,
                unverified,
                outdated,
            ]
        )
        await setup.commit()

    gateway = InMemoryQdrantGateway()
    provider = DeterministicEmbeddingProvider()
    dispatcher = RecordingDispatcher()
    try:
        async with session_factory() as control_session:
            control = KnowledgeBaseRebuildService(
                control_session,
                settings,
                dispatcher,
            )
            queued = await control.start(knowledge_base_id)
            assert queued.status == "queued"
            assert queued.document_versions_total == 3
            assert queued.documents_total == 2
            assert queued.knowledge_entries_total == 1
            assert queued.operation_id is not None
            operation_id = queued.operation_id
            run_generation = dispatcher.calls[0][1]

        async with session_factory() as worker_session:
            executor = KnowledgeBaseRebuildExecutor(
                worker_session,
                DocumentParsingService(worker_session, storage, settings),
                DocumentIndexingService(
                    worker_session,
                    settings,
                    provider,
                    cast(QdrantGateway, gateway),
                ),
                KnowledgeEntryIndexingService(
                    worker_session,
                    settings,
                    provider,
                    cast(QdrantGateway, gateway),
                ),
            )
            assert await executor.run(operation_id, run_generation)

        async with session_factory() as verification:
            versions = {
                current.id: current
                for current in (
                    await verification.execute(
                        select(DocumentVersion).where(
                            DocumentVersion.document_id.in_((first.id, second.id))
                        )
                    )
                ).scalars()
            }
            assert all(
                versions[item].parse_status == "succeeded" for item in (v1.id, v2.id, second_v1.id)
            )
            assert versions[v1.id].index_status == "pending"
            assert versions[v1.id].active_index_generation is None
            assert versions[v2.id].index_status == "succeeded"
            assert versions[second_v1.id].index_status == "succeeded"
            assert (
                await verification.scalar(
                    select(func.count())
                    .select_from(DocumentChunk)
                    .where(DocumentChunk.document_version_id.in_((v1.id, v2.id, second_v1.id)))
                )
            ) == 3
            active = await DocumentIndexingRepository(verification).list_active_generations(
                knowledge_base_id, document_id=None
            )
            assert {item.version_id for item in active} == {v2.id, second_v1.id}
            knowledge_active = await KnowledgeEntryIndexingRepository(
                verification
            ).list_active_generations(knowledge_base_id)
            assert [item.entry_id for item in knowledge_active] == [verified.id]
            stored_entries = {
                current.id: current
                for current in (
                    await verification.execute(
                        select(KnowledgeEntry).where(
                            KnowledgeEntry.knowledge_base_id == knowledge_base_id
                        )
                    )
                ).scalars()
            }
            assert stored_entries[verified.id].index_status == "succeeded"
            assert stored_entries[unverified.id].index_status == "not_indexed"
            assert stored_entries[outdated.id].index_status == "not_indexed"
            assert all(
                "raw answer snapshot" not in point.sparse_text
                for point in gateway.points.values()
                if point.payload.get("source_type") == "knowledge_entry"
            )
            operation_record = await verification.get(KnowledgeBaseRebuildOperation, operation_id)
            assert operation_record is not None
            assert operation_record.status == "succeeded"
    finally:
        async with session_factory() as cleanup:
            await cleanup.execute(
                delete(KnowledgeEntry).where(KnowledgeEntry.knowledge_base_id == knowledge_base_id)
            )
            await cleanup.execute(
                delete(Document).where(Document.knowledge_base_id == knowledge_base_id)
            )
            await cleanup.execute(
                delete(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id)
            )
            await cleanup.commit()


@pytest.mark.skipif(not TEST_QDRANT_URL, reason="TEST_QDRANT_URL is not configured")
async def test_export_restore_rebuild_and_rag_retrieval_end_to_end(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    assert TEST_QDRANT_URL is not None
    knowledge_base_id, document_id = uuid4(), uuid4()
    storage = LocalFileStorage(tmp_path / "round-trip-uploads", max_size=50_000, chunk_size=64)
    collection = f"tracemind_stage17_e2e_{uuid4().hex}"
    settings = Settings(
        app_env="test",
        document_storage_root=storage.root,
        qdrant_url=TEST_QDRANT_URL,
        qdrant_collection_name=collection,
        embedding_dimension=4,
        document_chunk_max_chars=100,
        document_chunk_overlap_chars=10,
        semantic_search_score_threshold=0.01,
    )
    qdrant_client = AsyncQdrantClient(
        url=TEST_QDRANT_URL,
        check_compatibility=False,
        trust_env=False,
    )
    gateway = QdrantGateway(
        qdrant_client,
        collection_name=collection,
        vector_name=settings.qdrant_dense_vector_name,
        sparse_vector_name=settings.qdrant_sparse_vector_name,
        bm25_model=settings.qdrant_bm25_model,
        bm25_tokenizer=settings.qdrant_bm25_tokenizer,
        bm25_language=settings.qdrant_bm25_language,
        dimension=4,
        upsert_batch_size=64,
        dense_prefetch_limit=20,
        sparse_prefetch_limit=20,
    )
    provider = DeterministicEmbeddingProvider()
    archive_storage = LocalArchiveStorage(storage.root, archive_limits())
    knowledge_base = KnowledgeBase(
        id=knowledge_base_id,
        name=f"Stage17 E2E {knowledge_base_id}",
        description="Export Restore Rebuild Retrieval",
    )
    document = Document(
        id=document_id,
        knowledge_base_id=knowledge_base_id,
        name="runbook.md",
        normalized_name="runbook.md",
        relative_path="ops/runbook.md",
        normalized_path="ops/runbook.md",
        source_type="upload",
    )
    historical = version(storage, document, 1, "historical rollback instructions")
    latest = version(storage, document, 2, "latest durable recovery procedure")
    conversation = Conversation(
        id=uuid4(),
        knowledge_base_id=knowledge_base_id,
        title="Recovery incident",
    )
    user_message = ConversationMessage(
        id=uuid4(),
        conversation_id=conversation.id,
        role="user",
        status="completed",
        content="How is durable recovery performed?",
    )
    assistant_message = ConversationMessage(
        id=uuid4(),
        conversation_id=conversation.id,
        role="assistant",
        status="completed",
        content="Use the latest durable recovery procedure.",
        trace_id=uuid4(),
        sources=[
            {
                "document_id": str(document_id),
                "document_version_id": str(latest.id),
                "document_name": "runbook.md",
            }
        ],
        generation_metadata={"provider": "integration"},
    )
    verified = KnowledgeEntry(
        id=uuid4(),
        knowledge_base_id=knowledge_base_id,
        question="How do we recover durable local data?",
        background="A verified production incident",
        root_cause="Derived state was absent",
        solution="Rebuild verified maintained knowledge after source restore",
        failed_attempts=["Indexing snapshots directly"],
        validation_status="verified",
        tags=["recovery", "verified"],
        source_conversation_id=conversation.id,
        source_user_message_id=user_message.id,
        source_assistant_message_id=assistant_message.id,
        question_snapshot=user_message.content,
        answer_snapshot=assistant_message.content,
        sources_snapshot=assistant_message.sources or [],
        generation_metadata_snapshot=assistant_message.generation_metadata,
        index_status="pending",
    )
    exported_path: Path | None = None
    try:
        async with session_factory() as setup:
            setup.add(knowledge_base)
            await setup.flush()
            setup.add(document)
            await setup.flush()
            setup.add_all([historical, latest])
            await setup.flush()
            setup.add(conversation)
            await setup.flush()
            setup.add_all([user_message, assistant_message])
            await setup.flush()
            setup.add(verified)
            await setup.commit()

        async with session_factory() as exporting:
            exported = await KnowledgeBaseArchiveService(
                exporting,
                storage,
                archive_storage,
                "stage17-e2e",
            ).export(knowledge_base_id)
            exported_path = exported.path

        async with session_factory() as deleting:
            await deleting.execute(
                delete(KnowledgeEntry).where(KnowledgeEntry.knowledge_base_id == knowledge_base_id)
            )
            await deleting.execute(
                delete(Conversation).where(Conversation.knowledge_base_id == knowledge_base_id)
            )
            await deleting.execute(
                delete(Document).where(Document.knowledge_base_id == knowledge_base_id)
            )
            await deleting.execute(
                delete(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id)
            )
            await deleting.commit()
        await asyncio.to_thread(
            shutil.rmtree,
            storage.root / str(knowledge_base_id),
            True,
        )

        async with session_factory() as restoring:
            restored = await KnowledgeBaseRestoreService(
                restoring,
                storage,
                archive_storage,
                {".md"},
            ).restore(
                UploadFile(
                    filename="stage17-e2e.tracemind.zip",
                    file=BytesIO(exported.path.read_bytes()),
                )
            )
            assert restored.knowledge_base_id == knowledge_base_id
            assert restored.rebuild_status == "not_started"

        dispatcher = RecordingDispatcher()
        async with session_factory() as control_session:
            queued = await KnowledgeBaseRebuildService(
                control_session,
                settings,
                dispatcher,
            ).start(knowledge_base_id)
            assert queued.operation_id is not None
            operation_id = queued.operation_id
            run_generation = dispatcher.calls[0][1]

        async with session_factory() as worker_session:
            executor = KnowledgeBaseRebuildExecutor(
                worker_session,
                DocumentParsingService(worker_session, storage, settings),
                DocumentIndexingService(
                    worker_session,
                    settings,
                    provider,
                    gateway,
                ),
                KnowledgeEntryIndexingService(
                    worker_session,
                    settings,
                    provider,
                    gateway,
                ),
            )
            assert await executor.run(operation_id, run_generation)

        async with session_factory() as retrieval_session:
            chunks = (
                await retrieval_session.execute(
                    select(DocumentChunk).where(
                        DocumentChunk.document_version_id.in_((historical.id, latest.id))
                    )
                )
            ).scalars()
            assert {chunk.document_version_id for chunk in chunks} == {
                historical.id,
                latest.id,
            }
            document_service = DocumentIndexingService(
                retrieval_session,
                settings,
                provider,
                gateway,
            )
            active = await document_service.list_active_generations(
                knowledge_base_id, document_id=None
            )
            assert [item.version_id for item in active] == [latest.id]
            retrieval = RagRetrievalService(
                document_service,
                settings,
                provider,
                gateway,
                KnowledgeEntryIndexingRepository(retrieval_session),
            )
            hits = await retrieval.hybrid_search(
                knowledge_base_id,
                query="latest durable recovery verified knowledge",
                limit=10,
                language=None,
                document_id=None,
            )
            document_hits = [hit for hit in hits if hit.source_type == "document"]
            knowledge_hits = [hit for hit in hits if hit.source_type == "knowledge_entry"]
            assert document_hits
            assert knowledge_hits
            assert all(getattr(hit, "document_id", None) == document_id for hit in document_hits)
            assert all(
                getattr(hit, "document_version_id", None) == latest.id for hit in document_hits
            )
            assert all(
                getattr(hit, "knowledge_entry_id", None) == verified.id for hit in knowledge_hits
            )
            assert not any(
                getattr(hit, "document_version_id", None) == historical.id for hit in hits
            )
    finally:
        if exported_path is not None:
            await archive_storage.discard_archive(exported_path)
        try:
            if await qdrant_client.collection_exists(collection):
                await qdrant_client.delete_collection(collection)
        finally:
            await qdrant_client.close()
        async with session_factory() as cleanup:
            await cleanup.execute(
                delete(KnowledgeEntry).where(KnowledgeEntry.knowledge_base_id == knowledge_base_id)
            )
            await cleanup.execute(
                delete(Conversation).where(Conversation.knowledge_base_id == knowledge_base_id)
            )
            await cleanup.execute(
                delete(Document).where(Document.knowledge_base_id == knowledge_base_id)
            )
            await cleanup.execute(
                delete(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id)
            )
            await cleanup.commit()
