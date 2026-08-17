import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic.config import Config
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.core.config import get_settings
from app.models.consistency_repair import (
    ConsistencyAuditFindingRecord,
    ConsistencyAuditSnapshotRecord,
    ConsistencyRepairItem,
    ConsistencyRepairOperation,
)
from app.models.knowledge_base import KnowledgeBase
from app.repositories.consistency_repair import ConsistencyRepairRepository

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
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
    await asyncio.to_thread(run_migration)
    engine = create_async_engine(require_test_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def create_repair_graph(
    session: AsyncSession,
    *,
    operation_status: str = "queued",
    heartbeat_at: datetime | None = None,
    item_status: str = "pending",
) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    now = datetime.now(UTC)
    kb_id, audit_id, finding_id, operation_id, item_id = (uuid4() for _ in range(5))
    session.add(
        KnowledgeBase(
            id=kb_id,
            name=f"repair-{kb_id}",
            description="seal integration",
            created_at=now,
            updated_at=now,
        )
    )
    await session.flush()
    session.add(
        ConsistencyAuditSnapshotRecord(
            id=audit_id,
            scope="knowledge_base",
            status="completed",
            knowledge_base_id=kb_id,
            started_at=now,
            completed_at=now,
        )
    )
    await session.flush()
    session.add(
        ConsistencyAuditFindingRecord(
            id=finding_id,
            audit_id=audit_id,
            code="latest_index_generation_missing",
            severity="ERROR",
            entity_type="document_version",
            entity_id=str(uuid4()),
            knowledge_base_id=kb_id,
            safe_message="finding",
            details={},
        )
    )
    await session.flush()
    operation = ConsistencyRepairOperation(
        id=operation_id,
        audit_id=audit_id,
        knowledge_base_id=kb_id,
        status=operation_status,
        run_generation=uuid4(),
        heartbeat_at=heartbeat_at,
        started_at=now if operation_status == "running" else None,
    )
    session.add(operation)
    await session.flush()
    session.add(
        ConsistencyRepairItem(
            id=item_id,
            operation_id=operation_id,
            finding_id=finding_id,
            finding_code="latest_index_generation_missing",
            entity_type="document_version",
            entity_id=str(uuid4()),
            status=item_status,
            action="index_latest_document_version",
            started_at=now if item_status == "running" else None,
            safe_message="pending",
        )
    )
    await session.flush()
    return kb_id, audit_id, finding_id, operation_id, item_id


async def test_real_postgresql_repair_stale_worker_takeover_fences_old_generation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as setup:
        kb_id, _, _, operation_id, item_id = await create_repair_graph(
            setup,
            operation_status="running",
            heartbeat_at=datetime.now(UTC) - timedelta(hours=2),
            item_status="running",
        )
        operation = await setup.get(ConsistencyRepairOperation, operation_id)
        assert operation is not None
        old_generation = operation.run_generation
        await setup.commit()

    async with session_factory() as takeover_session:
        takeover = ConsistencyRepairRepository(takeover_session)
        operation, prepared = await takeover.prepare_retry(operation_id, stale_after_seconds=1)
        assert prepared
        new_generation = operation.run_generation
        assert new_generation != old_generation
        await takeover_session.commit()

    async with session_factory() as new_worker_claim_session:
        new_worker_claim = ConsistencyRepairRepository(new_worker_claim_session)
        claimed_generation = await new_worker_claim.claim_operation(
            operation_id, new_generation, stale_after_seconds=1
        )
        assert claimed_generation == new_generation

    async with session_factory() as old_worker_session:
        old_worker = ConsistencyRepairRepository(old_worker_session)
        assert not await old_worker.finish_item(
            item_id,
            operation_id,
            old_generation,
            "succeeded",
            "old worker must be fenced",
        )

    async with session_factory() as new_worker_session:
        new_worker = ConsistencyRepairRepository(new_worker_session)
        assert await new_worker.mark_item_running(item_id, operation_id, new_generation)
        assert await new_worker.finish_item(
            item_id,
            operation_id,
            new_generation,
            "succeeded",
            "new worker completed",
        )
        assert await new_worker.finalize(operation_id, new_generation)
        await new_worker_session.execute(delete(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        await new_worker_session.commit()


async def test_real_postgresql_allows_only_one_active_repair_per_knowledge_base(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as setup:
        kb_id, audit_id, _, existing_operation_id, _ = await create_repair_graph(setup)
        await setup.execute(
            delete(ConsistencyRepairOperation).where(
                ConsistencyRepairOperation.id == existing_operation_id
            )
        )
        await setup.commit()

    async def create_second(operation_id: UUID) -> bool:
        async with session_factory() as session:
            session.add(
                ConsistencyRepairOperation(
                    id=operation_id,
                    audit_id=audit_id,
                    knowledge_base_id=kb_id,
                    status="queued",
                    run_generation=uuid4(),
                )
            )
            try:
                await session.commit()
                return True
            except IntegrityError:
                await session.rollback()
                return False

    first_operation_id, second_operation_id = uuid4(), uuid4()
    results = await asyncio.gather(
        create_second(first_operation_id),
        create_second(second_operation_id),
    )
    assert results.count(True) == 1
    async with session_factory() as verify:
        active_count = int(
            (
                await verify.execute(
                    select(func.count())
                    .select_from(ConsistencyRepairOperation)
                    .where(
                        ConsistencyRepairOperation.knowledge_base_id == kb_id,
                        ConsistencyRepairOperation.status.in_(("queued", "running")),
                    )
                )
            ).scalar_one()
        )
        assert active_count == 1
        await verify.execute(delete(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        await verify.commit()


async def test_real_postgresql_knowledge_base_delete_cascades_audit_and_repair_graph(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as setup:
        kb_id, audit_id, finding_id, operation_id, item_id = await create_repair_graph(setup)
        await setup.commit()

    async with session_factory() as deleting:
        await deleting.execute(delete(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        await deleting.commit()

    async with session_factory() as verify:
        assert await verify.get(ConsistencyAuditSnapshotRecord, audit_id) is None
        assert await verify.get(ConsistencyAuditFindingRecord, finding_id) is None
        assert await verify.get(ConsistencyRepairOperation, operation_id) is None
        assert await verify.get(ConsistencyRepairItem, item_id) is None
