import asyncio
import hashlib
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.config import Config
from qdrant_client import AsyncQdrantClient
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.core.config import Settings, get_settings
from app.indexing import QdrantGateway, VectorPoint
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_entry import KnowledgeEntry
from app.repositories.consistency_audit import ConsistencyAuditRepository
from app.repositories.consistency_repair import ConsistencyRepairRepository
from app.schemas.consistency_repair import ConsistencyRepairRequest
from app.services.consistency_audit import ConsistencyAuditService
from app.services.consistency_repair import (
    REPAIR_ACTIONS,
    ConsistencyRepairExecutor,
    ConsistencyRepairService,
)
from app.services.consistency_repair_dispatcher import ConsistencyRepairDispatcher
from app.services.document_indexing import DocumentIndexingService
from app.services.document_parsing import DocumentParsingService
from app.services.knowledge_entry_indexing import KnowledgeEntryIndexingService
from app.storage.archive import LocalArchiveStorage, archive_limits_from_settings
from app.storage.local import LocalFileStorage

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
TEST_QDRANT_URL = os.getenv("TEST_QDRANT_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured"),
    pytest.mark.skipif(not TEST_QDRANT_URL, reason="TEST_QDRANT_URL is not configured"),
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
    await asyncio.to_thread(run_migration)
    engine = create_async_engine(require_test_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


class DeterministicEmbeddingProvider:
    model_name = "stage17-audit-test"
    dimension = 4

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> list[float]:
        return [1.0, 0.5, 0.25, float(len(text) % 7) / 10]


class CaptureDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    async def enqueue(self, operation_id: object, run_generation: object) -> None:
        self.calls.append((operation_id, run_generation))


def create_version(storage: LocalFileStorage, document: Document, text: str) -> DocumentVersion:
    version_id = uuid4()
    content = text.encode()
    storage_path = storage.final_relative_path(
        document.knowledge_base_id, document.id, version_id, ".md"
    )
    path = storage.resolve_relative(storage_path, must_exist=False)
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    return DocumentVersion(
        id=version_id,
        document_id=document.id,
        version_number=1,
        content_hash=hashlib.sha256(content).hexdigest(),
        file_size=len(content),
        mime_type="text/markdown",
        extension=".md",
        storage_path=storage_path,
        parse_status="pending",
        index_status="pending",
    )


async def test_real_audit_detects_injected_storage_chunk_qdrant_and_orphan_faults(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    assert TEST_QDRANT_URL is not None
    kb_id, document_id = uuid4(), uuid4()
    storage = LocalFileStorage(tmp_path / "uploads", max_size=100_000, chunk_size=64)
    collection = f"tracemind_stage17_audit_{uuid4().hex}"
    config = Settings(
        app_env="test",
        document_storage_root=storage.root,
        qdrant_url=TEST_QDRANT_URL,
        qdrant_collection_name=collection,
        embedding_dimension=4,
        document_chunk_max_chars=100,
        document_chunk_overlap_chars=10,
        consistency_audit_qdrant_page_size=1,
    )
    client = AsyncQdrantClient(
        url=TEST_QDRANT_URL,
        check_compatibility=False,
        trust_env=False,
    )
    gateway = QdrantGateway(
        client,
        collection_name=collection,
        vector_name=config.qdrant_dense_vector_name,
        sparse_vector_name=config.qdrant_sparse_vector_name,
        bm25_model=config.qdrant_bm25_model,
        bm25_tokenizer=config.qdrant_bm25_tokenizer,
        bm25_language=config.qdrant_bm25_language,
        dimension=4,
        upsert_batch_size=64,
        dense_prefetch_limit=20,
        sparse_prefetch_limit=20,
    )
    document = Document(
        id=document_id,
        knowledge_base_id=kb_id,
        name="audit.md",
        normalized_name="audit.md",
        relative_path="audit.md",
        normalized_path="audit.md",
        source_type="upload",
    )
    version = create_version(storage, document, "audit source of truth remains durable")
    entry = KnowledgeEntry(
        id=uuid4(),
        knowledge_base_id=kb_id,
        question="How is consistency audited?",
        solution="Compare maintained source metadata with derived state.",
        failed_attempts=[],
        validation_status="verified",
        tags=["audit"],
        question_snapshot="How is consistency audited?",
        answer_snapshot="This snapshot must never define expected indexed content.",
        sources_snapshot=[],
        index_status="pending",
    )
    archive_storage = LocalArchiveStorage(storage.root, archive_limits_from_settings(config))
    try:
        async with session_factory() as setup:
            setup.add(KnowledgeBase(id=kb_id, name=f"Audit {kb_id}"))
            await setup.flush()
            setup.add(document)
            await setup.flush()
            setup.add_all([version, entry])
            await setup.commit()

        provider = DeterministicEmbeddingProvider()
        async with session_factory() as worker:
            assert await DocumentParsingService(worker, storage, config).parse_version(
                version.id, enqueue_index=False
            )
            assert await DocumentIndexingService(worker, config, provider, gateway).index_version(
                version.id
            )
            assert await KnowledgeEntryIndexingService(
                worker, config, provider, gateway
            ).sync_entry(entry.id)

        async with session_factory() as healthy_session:
            healthy = await ConsistencyAuditService(
                config,
                ConsistencyAuditRepository(healthy_session),
                LocalFileStorage(
                    storage.root,
                    max_size=100_000,
                    chunk_size=64,
                    create_roots=False,
                ),
                LocalArchiveStorage(
                    storage.root,
                    archive_limits_from_settings(config),
                    create_roots=False,
                ),
                gateway,
            ).audit_knowledge_base(kb_id)
            assert healthy.status == "completed"
            assert healthy.summary.healthy is True
            assert healthy.findings == []

        async with session_factory() as fault_session:
            stored_version = await fault_session.get(DocumentVersion, version.id)
            assert stored_version is not None
            active_generation = stored_version.active_index_generation
            assert active_generation is not None
            first_chunk = (
                await fault_session.execute(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_version_id == version.id)
                    .order_by(DocumentChunk.chunk_index)
                    .limit(1)
                )
            ).scalar_one()
            await fault_session.execute(
                delete(DocumentChunk).where(DocumentChunk.id == first_chunk.id)
            )
            await fault_session.commit()

        source_path = storage.resolve_relative(version.storage_path)
        original = source_path.read_bytes()
        source_path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        await gateway.delete_generation(active_generation)
        orphan_kb = uuid4()
        await gateway.upsert(
            [
                VectorPoint(
                    id=uuid4(),
                    dense_vector=[1.0, 0.0, 0.0, 0.0],
                    sparse_text="orphan audit payload",
                    payload={
                        "source_type": "document",
                        "knowledge_base_id": str(orphan_kb),
                        "document_id": str(uuid4()),
                        "document_version_id": str(uuid4()),
                        "index_generation": str(uuid4()),
                    },
                )
            ]
        )

        async with session_factory() as damaged_session:
            damaged = await ConsistencyAuditService(
                config,
                ConsistencyAuditRepository(damaged_session),
                storage,
                archive_storage,
                gateway,
            ).audit_all()
            codes = {item.code for item in damaged.findings}
            assert {
                "document_file_hash_mismatch",
                "parsed_version_missing_chunks",
                "chunk_count_mismatch",
                "active_index_points_missing",
                "active_index_point_count_mismatch",
                "orphan_qdrant_point",
            } <= codes
            assert damaged.summary.critical_count >= 1
            assert damaged.summary.error_count >= 1
    finally:
        try:
            if await client.collection_exists(collection):
                await client.delete_collection(collection)
        finally:
            await client.close()


async def test_real_selected_repair_closes_derived_faults_but_preserves_source_damage(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    assert TEST_QDRANT_URL is not None
    kb_id = uuid4()
    storage = LocalFileStorage(tmp_path / "repair-uploads", max_size=100_000, chunk_size=64)
    collection = f"tracemind_stage17_repair_{uuid4().hex}"
    config = Settings(
        app_env="test",
        document_storage_root=storage.root,
        qdrant_url=TEST_QDRANT_URL,
        qdrant_collection_name=collection,
        embedding_dimension=4,
        document_chunk_max_chars=100,
        document_chunk_overlap_chars=10,
        consistency_audit_qdrant_page_size=2,
    )
    client = AsyncQdrantClient(url=TEST_QDRANT_URL, check_compatibility=False, trust_env=False)
    gateway = QdrantGateway(
        client,
        collection_name=collection,
        vector_name=config.qdrant_dense_vector_name,
        sparse_vector_name=config.qdrant_sparse_vector_name,
        bm25_model=config.qdrant_bm25_model,
        bm25_tokenizer=config.qdrant_bm25_tokenizer,
        bm25_language=config.qdrant_bm25_language,
        dimension=4,
        upsert_batch_size=64,
        dense_prefetch_limit=20,
        sparse_prefetch_limit=20,
    )
    archive = LocalArchiveStorage(storage.root, archive_limits_from_settings(config))
    provider = DeterministicEmbeddingProvider()
    repair_document = Document(
        id=uuid4(),
        knowledge_base_id=kb_id,
        name="repair.md",
        normalized_name="repair.md",
        relative_path="repair.md",
        normalized_path="repair.md",
        source_type="upload",
    )
    damaged_document = Document(
        id=uuid4(),
        knowledge_base_id=kb_id,
        name="damaged.md",
        normalized_name="damaged.md",
        relative_path="damaged.md",
        normalized_path="damaged.md",
        source_type="upload",
    )
    repair_version = create_version(storage, repair_document, "repairable source remains valid")
    damaged_version = create_version(storage, damaged_document, "source damage must remain visible")
    entry = KnowledgeEntry(
        id=uuid4(),
        knowledge_base_id=kb_id,
        question="How is repair safe?",
        solution="Revalidate and use maintained fields.",
        failed_attempts=[],
        validation_status="verified",
        tags=["repair"],
        question_snapshot="How is repair safe?",
        answer_snapshot="Never index this raw snapshot.",
        sources_snapshot=[],
        index_status="pending",
    )
    try:
        async with session_factory() as setup:
            setup.add(KnowledgeBase(id=kb_id, name=f"Repair {kb_id}"))
            await setup.flush()
            setup.add_all([repair_document, damaged_document])
            await setup.flush()
            setup.add_all([repair_version, damaged_version, entry])
            await setup.commit()
        async with session_factory() as worker:
            parser = DocumentParsingService(worker, storage, config)
            indexer = DocumentIndexingService(worker, config, provider, gateway)
            assert await parser.parse_version(repair_version.id, enqueue_index=False)
            assert await parser.parse_version(damaged_version.id, enqueue_index=False)
            assert await indexer.index_version(repair_version.id)
            assert await indexer.index_version(damaged_version.id)
            assert await KnowledgeEntryIndexingService(
                worker, config, provider, gateway
            ).sync_entry(entry.id)
        async with session_factory() as inject:
            repair_row = await inject.get(DocumentVersion, repair_version.id)
            entry_row = await inject.get(KnowledgeEntry, entry.id)
            assert repair_row is not None and entry_row is not None
            old_document_generation = repair_row.active_index_generation
            old_knowledge_generation = entry_row.active_index_generation
            assert old_document_generation is not None and old_knowledge_generation is not None
            await inject.execute(
                delete(DocumentChunk).where(DocumentChunk.document_version_id == repair_version.id)
            )
            repair_row.chunk_count = 1
            repair_row.active_index_generation = None
            repair_row.index_status = "pending"
            repair_row.indexed_at = None
            repair_row.indexed_chunk_count = 0
            entry_row.active_index_generation = None
            entry_row.index_status = "pending"
            entry_row.indexed_at = None
            entry_row.indexed_chunk_count = 0
            await inject.commit()
        await gateway.delete_generation(old_knowledge_generation)
        stale_generation, orphan_id = uuid4(), uuid4()
        await gateway.upsert(
            [
                VectorPoint(
                    id=uuid4(),
                    dense_vector=[1.0, 0.0, 0.0, 0.0],
                    sparse_text="stale",
                    payload={
                        "source_type": "document",
                        "knowledge_base_id": str(kb_id),
                        "document_id": str(repair_document.id),
                        "document_version_id": str(repair_version.id),
                        "index_generation": str(stale_generation),
                    },
                ),
                VectorPoint(
                    id=orphan_id,
                    dense_vector=[0.0, 1.0, 0.0, 0.0],
                    sparse_text="orphan",
                    payload={
                        "source_type": "document",
                        "knowledge_base_id": str(kb_id),
                        "document_id": str(uuid4()),
                        "document_version_id": str(uuid4()),
                        "index_generation": str(uuid4()),
                    },
                ),
            ]
        )
        damaged_path = storage.resolve_relative(damaged_version.storage_path)
        original = damaged_path.read_bytes()
        damaged_path.write_bytes(bytes([original[0] ^ 1]) + original[1:])

        async with session_factory() as repair_session:
            audit_repository = ConsistencyAuditRepository(repair_session)
            audit_service = ConsistencyAuditService(
                config, audit_repository, storage, archive, gateway
            )
            injected = await audit_service.audit_knowledge_base(kb_id)
            selected = [
                finding.finding_id
                for finding in injected.findings
                if finding.code
                in {
                    "parsed_version_missing_chunks",
                    "chunk_count_mismatch",
                    "latest_index_generation_missing",
                    "verified_knowledge_index_missing",
                    "stale_qdrant_generation",
                    "orphan_qdrant_point",
                    "document_file_hash_mismatch",
                }
            ]
            dispatcher = CaptureDispatcher()
            repair_service = ConsistencyRepairService(
                repair_session, audit_service, cast(ConsistencyRepairDispatcher, dispatcher)
            )
            request = ConsistencyRepairRequest(
                audit_id=injected.audit_id,
                knowledge_base_id=kb_id,
                finding_ids=selected,
                dry_run=True,
            )
            dry_run = await repair_service.start(request)
            assert dry_run.operation_id is None and dispatcher.calls == []
            assert any(item.status == "not_repairable" for item in dry_run.items)
            execution = await repair_service.start(request.model_copy(update={"dry_run": False}))
            assert execution.operation_id is not None and len(dispatcher.calls) == 1
            operation = await ConsistencyRepairRepository(repair_session).get_operation(
                execution.operation_id, kb_id
            )
            assert operation is not None
            executor = ConsistencyRepairExecutor(
                repair_session,
                audit_service,
                DocumentParsingService(repair_session, storage, config),
                DocumentIndexingService(repair_session, config, provider, gateway),
                KnowledgeEntryIndexingService(repair_session, config, provider, gateway),
                gateway,
                archive,
                audit_repository,
            )
            await executor.run(operation.id, operation.run_generation)
            final = await audit_service.inspect_knowledge_base(kb_id)
            final_codes = {finding.code for finding in final.findings}
            assert "document_file_hash_mismatch" in final_codes
            assert not final_codes.intersection(REPAIR_ACTIONS)
            second = await repair_service.start(request)
            assert all(item.status in {"skipped", "not_repairable"} for item in second.items)
    finally:
        try:
            if await client.collection_exists(collection):
                await client.delete_collection(collection)
        finally:
            await client.close()
