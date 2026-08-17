from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, ConversationMessage
from app.models.document import Document, DocumentVersion
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_entry import KnowledgeEntry
from app.repositories.knowledge_base_archive import (
    KnowledgeBaseArchiveRepository,
    RestoreConflictCheck,
)


def scalar_result(value: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def scalars_result(values: list[object]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = values
    return result


async def test_repository_loads_all_source_entities_under_share_locks() -> None:
    now = datetime.now(UTC)
    knowledge_base_id = uuid4()
    knowledge_base = KnowledgeBase(
        id=knowledge_base_id,
        name="Archive",
        description=None,
        created_at=now,
        updated_at=now,
    )
    document = Document(id=uuid4(), knowledge_base_id=knowledge_base_id)
    version = DocumentVersion(id=uuid4(), document_id=document.id)
    conversation = Conversation(id=uuid4(), knowledge_base_id=knowledge_base_id)
    message = ConversationMessage(id=uuid4(), conversation_id=conversation.id)
    entry = KnowledgeEntry(id=uuid4(), knowledge_base_id=knowledge_base_id)
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [
        scalar_result(knowledge_base),
        scalars_result([document]),
        scalars_result([version]),
        scalars_result([conversation]),
        scalars_result([message]),
        scalars_result([entry]),
    ]
    repository = KnowledgeBaseArchiveRepository(cast(AsyncSession, session))

    snapshot = await repository.load_export_snapshot(knowledge_base_id)

    assert snapshot is not None
    assert snapshot.knowledge_base is knowledge_base
    assert snapshot.documents == (document,)
    assert snapshot.document_versions == (version,)
    assert snapshot.conversations == (conversation,)
    assert snapshot.messages == (message,)
    assert snapshot.knowledge_entries == (entry,)
    assert session.execute.await_count == 6
    statements = [call.args[0] for call in session.execute.await_args_list]
    compiled = [str(statement.compile(dialect=postgresql.dialect())) for statement in statements]
    assert all("FOR SHARE OF" in sql for sql in compiled)
    assert "knowledge_bases.id =" in compiled[0]
    assert "documents.knowledge_base_id =" in compiled[1]
    assert "JOIN documents" in compiled[2]
    assert "conversations.knowledge_base_id =" in compiled[3]
    assert "JOIN conversations" in compiled[4]
    assert "knowledge_entries.knowledge_base_id =" in compiled[5]


async def test_repository_stops_when_knowledge_base_does_not_exist() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = scalar_result(None)
    repository = KnowledgeBaseArchiveRepository(cast(AsyncSession, session))

    assert await repository.load_export_snapshot(uuid4()) is None
    session.execute.assert_awaited_once()


async def test_restore_preflight_reports_every_database_conflict_type() -> None:
    knowledge_base_id = uuid4()
    document_id, version_id, conversation_id = uuid4(), uuid4(), uuid4()
    message_id, entry_id, assistant_id = uuid4(), uuid4(), uuid4()
    check = RestoreConflictCheck(
        knowledge_base_id=knowledge_base_id,
        knowledge_base_name="Existing",
        document_ids=(document_id,),
        document_version_ids=(version_id,),
        conversation_ids=(conversation_id,),
        message_ids=(message_id,),
        knowledge_entry_ids=(entry_id,),
        normalized_paths=("docs/guide.md",),
        source_assistant_message_ids=(assistant_id,),
    )
    knowledge_base_result = MagicMock()
    knowledge_base_result.all.return_value = [
        SimpleNamespace(id=knowledge_base_id, name="Existing")
    ]
    entity_results = []
    for value in [document_id, version_id, conversation_id, message_id]:
        result = MagicMock()
        result.scalars.return_value.first.return_value = value
        entity_results.append(result)
    entry_result = MagicMock()
    entry_result.all.return_value = [
        SimpleNamespace(id=entry_id, source_assistant_message_id=assistant_id)
    ]
    normalized_result = MagicMock()
    normalized_result.scalars.return_value.first.return_value = document_id
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [
        knowledge_base_result,
        *entity_results,
        entry_result,
        normalized_result,
    ]
    repository = KnowledgeBaseArchiveRepository(cast(AsyncSession, session))

    conflicts = await repository.find_restore_conflicts(check)

    assert conflicts == [
        "knowledge_base_id",
        "knowledge_base_name",
        "document_id",
        "document_version_id",
        "conversation_id",
        "message_id",
        "knowledge_entry_id",
        "knowledge_source_assistant",
        "normalized_document_path",
    ]
