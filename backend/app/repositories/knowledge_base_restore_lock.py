import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

logger = logging.getLogger(__name__)


def restore_advisory_lock_key(knowledge_base_id: UUID) -> int:
    """Return a stable signed bigint key for one Knowledge Base restore."""
    digest = hashlib.blake2b(
        b"tracemind:knowledge-base-restore:" + knowledge_base_id.bytes,
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


class RestoreAdvisoryLock:
    """Serialize active restore and journal recovery across app processes."""

    def __init__(self, engine: AsyncEngine | None) -> None:
        self.engine = engine

    @classmethod
    def from_session(cls, session: AsyncSession) -> "RestoreAdvisoryLock":
        bind = getattr(session, "bind", None)
        return cls(bind if isinstance(bind, AsyncEngine) else None)

    @property
    def enabled(self) -> bool:
        return self.engine is not None and self.engine.dialect.name == "postgresql"

    @asynccontextmanager
    async def hold(self, knowledge_base_id: UUID) -> AsyncIterator[None]:
        if not self.enabled:
            yield
            return
        assert self.engine is not None
        key = restore_advisory_lock_key(knowledge_base_id)
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
            try:
                yield
            finally:
                await self._release(connection, key, knowledge_base_id)

    @asynccontextmanager
    async def try_hold(self, knowledge_base_id: UUID) -> AsyncIterator[bool]:
        if not self.enabled:
            yield True
            return
        assert self.engine is not None
        key = restore_advisory_lock_key(knowledge_base_id)
        async with self.engine.connect() as connection:
            acquired = bool(
                (
                    await connection.execute(
                        text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
                    )
                ).scalar_one()
            )
            try:
                yield acquired
            finally:
                if acquired:
                    await self._release(connection, key, knowledge_base_id)

    @staticmethod
    async def _release(
        connection: AsyncConnection,
        key: int,
        knowledge_base_id: UUID,
    ) -> None:
        try:
            await asyncio.shield(
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            )
        except BaseException:
            # A pooled physical connection must never retain a leaked session lock.
            with suppress(BaseException):
                await asyncio.shield(connection.invalidate())
            logger.warning(
                "Restore advisory lock release invalidated its connection kb_id=%s",
                knowledge_base_id,
            )
