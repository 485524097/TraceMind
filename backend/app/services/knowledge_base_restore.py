import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar
from uuid import UUID, uuid4

from fastapi import UploadFile
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, ConversationMessage
from app.models.document import Document, DocumentVersion
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_entry import KnowledgeEntry
from app.repositories.knowledge_base_archive import (
    KnowledgeBaseArchiveRepository,
    KnowledgeBaseRestoreEntities,
    RestoreConflictCheck,
)
from app.repositories.knowledge_base_restore_lock import RestoreAdvisoryLock
from app.schemas.knowledge_base_archive import (
    CONVERSATIONS_DATA_PATH,
    DOCUMENT_VERSIONS_DATA_PATH,
    DOCUMENTS_DATA_PATH,
    KNOWLEDGE_BASE_DATA_PATH,
    KNOWLEDGE_ENTRIES_DATA_PATH,
    MESSAGES_DATA_PATH,
    ArchiveEntityCounts,
    ConversationArchiveRecord,
    ConversationMessageArchiveRecord,
    DocumentArchiveRecord,
    DocumentVersionArchiveRecord,
    KnowledgeBaseArchiveManifest,
    KnowledgeBaseArchiveRecord,
    KnowledgeBaseArchiveRestoreResponse,
    KnowledgeEntryArchiveRecord,
    StrictArchiveModel,
)
from app.services.exceptions import (
    ArchiveConflictError,
    ArchiveLimitExceededError,
    ArchiveStorageError,
    ArchiveValidationError,
    InvalidDocumentNameError,
    UnsupportedDocumentTypeError,
)
from app.storage.archive import (
    MANIFEST_PATH,
    LocalArchiveStorage,
    RestoreDocumentSource,
    StagedKnowledgeBaseRestore,
    TemporaryArchiveUpload,
    document_file_archive_path,
)
from app.storage.local import LocalFileStorage
from app.storage.names import SafeDocumentPath, normalize_document_path

logger = logging.getLogger(__name__)

ArchiveModelT = TypeVar("ArchiveModelT", bound=StrictArchiveModel)

EXPECTED_DATA_PATHS = {
    KNOWLEDGE_BASE_DATA_PATH,
    DOCUMENTS_DATA_PATH,
    DOCUMENT_VERSIONS_DATA_PATH,
    CONVERSATIONS_DATA_PATH,
    MESSAGES_DATA_PATH,
    KNOWLEDGE_ENTRIES_DATA_PATH,
}


class DuplicateJsonKeyError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedKnowledgeBaseArchive:
    manifest: KnowledgeBaseArchiveManifest
    knowledge_base: KnowledgeBaseArchiveRecord
    documents: tuple[DocumentArchiveRecord, ...]
    document_versions: tuple[DocumentVersionArchiveRecord, ...]
    conversations: tuple[ConversationArchiveRecord, ...]
    messages: tuple[ConversationMessageArchiveRecord, ...]
    knowledge_entries: tuple[KnowledgeEntryArchiveRecord, ...]
    normalized_documents: dict[UUID, SafeDocumentPath]
    restore_files: tuple[RestoreDocumentSource, ...]


class KnowledgeBaseArchiveValidator:
    def __init__(
        self,
        archive_storage: LocalArchiveStorage,
        document_storage: LocalFileStorage,
        allowed_extensions: set[str],
    ) -> None:
        self.archive_storage = archive_storage
        self.document_storage = document_storage
        self.allowed_extensions = allowed_extensions

    async def validate(self, archive_path: Path) -> ValidatedKnowledgeBaseArchive:
        try:
            return await self._validate(archive_path)
        except (ArchiveLimitExceededError, ArchiveValidationError):
            raise
        except (ArchiveStorageError, InvalidDocumentNameError, UnsupportedDocumentTypeError) as exc:
            raise ArchiveValidationError("Archive content is invalid") from exc
        except (ValidationError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ArchiveValidationError("Archive content is invalid") from exc

    async def _validate(self, archive_path: Path) -> ValidatedKnowledgeBaseArchive:
        inspected = await self.archive_storage.inspect_archive(archive_path)
        manifest_bytes = await self.archive_storage.read_archive_entry(archive_path, MANIFEST_PATH)
        manifest = self._parse_model(manifest_bytes, KnowledgeBaseArchiveManifest)
        data_by_path = {entry.path: entry for entry in manifest.data_entries}
        if len(data_by_path) != len(manifest.data_entries):
            raise ArchiveValidationError("Manifest contains duplicate data entries")
        if set(data_by_path) != EXPECTED_DATA_PATHS:
            raise ArchiveValidationError("Manifest data entry set is invalid")
        files_by_path = {entry.path: entry for entry in manifest.document_files}
        if len(files_by_path) != len(manifest.document_files):
            raise ArchiveValidationError("Manifest contains duplicate document files")
        declared_paths = {MANIFEST_PATH, *data_by_path, *files_by_path}
        if set(inspected.entries) != declared_paths:
            raise ArchiveValidationError("ZIP entries do not match the manifest allowlist")

        data_content: dict[str, bytes] = {}
        for entry in manifest.data_entries:
            zip_entry = inspected.entries[entry.path]
            if zip_entry.size != entry.size:
                raise ArchiveValidationError("Manifest data size does not match ZIP metadata")
            content = await self.archive_storage.read_archive_entry(archive_path, entry.path)
            if len(content) != entry.size:
                raise ArchiveValidationError("Manifest data size is invalid")
            if hashlib.sha256(content).hexdigest() != entry.sha256:
                raise ArchiveValidationError("Manifest data checksum is invalid")
            data_content[entry.path] = content

        knowledge_base = self._parse_model(
            data_content[KNOWLEDGE_BASE_DATA_PATH], KnowledgeBaseArchiveRecord
        )
        documents = self._parse_jsonl(data_content[DOCUMENTS_DATA_PATH], DocumentArchiveRecord)
        versions = self._parse_jsonl(
            data_content[DOCUMENT_VERSIONS_DATA_PATH], DocumentVersionArchiveRecord
        )
        conversations = self._parse_jsonl(
            data_content[CONVERSATIONS_DATA_PATH], ConversationArchiveRecord
        )
        messages = self._parse_jsonl(
            data_content[MESSAGES_DATA_PATH], ConversationMessageArchiveRecord
        )
        knowledge_entries = self._parse_jsonl(
            data_content[KNOWLEDGE_ENTRIES_DATA_PATH], KnowledgeEntryArchiveRecord
        )
        record_counts = {
            KNOWLEDGE_BASE_DATA_PATH: 1,
            DOCUMENTS_DATA_PATH: len(documents),
            DOCUMENT_VERSIONS_DATA_PATH: len(versions),
            CONVERSATIONS_DATA_PATH: len(conversations),
            MESSAGES_DATA_PATH: len(messages),
            KNOWLEDGE_ENTRIES_DATA_PATH: len(knowledge_entries),
        }
        if any(data_by_path[path].record_count != count for path, count in record_counts.items()):
            raise ArchiveValidationError("Manifest data record count is invalid")
        expected_counts = ArchiveEntityCounts(
            documents=len(documents),
            document_versions=len(versions),
            conversations=len(conversations),
            messages=len(messages),
            knowledge_entries=len(knowledge_entries),
        )
        if manifest.entity_counts != expected_counts:
            raise ArchiveValidationError("Manifest entity counts are invalid")
        if manifest.knowledge_base.model_dump() != knowledge_base.model_dump():
            raise ArchiveValidationError("Manifest Knowledge Base summary is inconsistent")

        normalized_documents = self._validate_reference_graph(
            knowledge_base,
            documents,
            versions,
            conversations,
            messages,
            knowledge_entries,
        )
        restore_files = await self._validate_document_files(
            archive_path,
            knowledge_base,
            documents,
            versions,
            manifest,
        )
        return ValidatedKnowledgeBaseArchive(
            manifest=manifest,
            knowledge_base=knowledge_base,
            documents=tuple(documents),
            document_versions=tuple(versions),
            conversations=tuple(conversations),
            messages=tuple(messages),
            knowledge_entries=tuple(knowledge_entries),
            normalized_documents=normalized_documents,
            restore_files=tuple(restore_files),
        )

    def _validate_reference_graph(
        self,
        knowledge_base: KnowledgeBaseArchiveRecord,
        documents: list[DocumentArchiveRecord],
        versions: list[DocumentVersionArchiveRecord],
        conversations: list[ConversationArchiveRecord],
        messages: list[ConversationMessageArchiveRecord],
        entries: list[KnowledgeEntryArchiveRecord],
    ) -> dict[UUID, SafeDocumentPath]:
        self._require_unique_ids("Document", [item.id for item in documents])
        self._require_unique_ids("DocumentVersion", [item.id for item in versions])
        self._require_unique_ids("Conversation", [item.id for item in conversations])
        self._require_unique_ids("Message", [item.id for item in messages])
        self._require_unique_ids("KnowledgeEntry", [item.id for item in entries])
        if any(item.knowledge_base_id != knowledge_base.id for item in documents):
            raise ArchiveValidationError("Document references a different Knowledge Base")
        document_ids = {item.id for item in documents}
        if any(item.document_id not in document_ids for item in versions):
            raise ArchiveValidationError("DocumentVersion references a missing Document")
        version_keys = [(item.document_id, item.version_number) for item in versions]
        if len(version_keys) != len(set(version_keys)):
            raise ArchiveValidationError("Document version numbers are duplicated")
        version_document_ids = {item.document_id for item in versions}
        if document_ids != version_document_ids:
            raise ArchiveValidationError("Every Document must contain at least one version")

        normalized_documents: dict[UUID, SafeDocumentPath] = {}
        normalized_paths: set[str] = set()
        for document in documents:
            normalized = normalize_document_path(document.relative_path, self.allowed_extensions)
            if normalized.display_name != document.name:
                raise ArchiveValidationError("Document name does not match its relative path")
            if normalized.normalized_path in normalized_paths:
                raise ArchiveValidationError("Normalized document paths are duplicated")
            normalized_paths.add(normalized.normalized_path)
            normalized_documents[document.id] = normalized

        if any(item.knowledge_base_id != knowledge_base.id for item in conversations):
            raise ArchiveValidationError("Conversation references a different Knowledge Base")
        conversation_ids = {item.id for item in conversations}
        messages_by_id = {item.id: item for item in messages}
        if any(item.conversation_id not in conversation_ids for item in messages):
            raise ArchiveValidationError("Message references a missing Conversation")
        if any(item.knowledge_base_id != knowledge_base.id for item in entries):
            raise ArchiveValidationError("KnowledgeEntry references a different Knowledge Base")
        assistant_sources = [
            item.source_assistant_message_id
            for item in entries
            if item.source_assistant_message_id is not None
        ]
        if len(assistant_sources) != len(set(assistant_sources)):
            raise ArchiveValidationError("KnowledgeEntry assistant sources are duplicated")
        for entry in entries:
            if (
                entry.source_conversation_id is not None
                and entry.source_conversation_id not in conversation_ids
            ):
                raise ArchiveValidationError("KnowledgeEntry references a missing Conversation")
            source_messages = [
                (entry.source_user_message_id, "user"),
                (entry.source_assistant_message_id, "assistant"),
            ]
            if (
                any(message_id is not None for message_id, _ in source_messages)
                and entry.source_conversation_id is None
            ):
                raise ArchiveValidationError(
                    "KnowledgeEntry source messages require a source Conversation"
                )
            for message_id, expected_role in source_messages:
                if message_id is None:
                    continue
                message = messages_by_id.get(message_id)
                if message is None or message.role != expected_role:
                    raise ArchiveValidationError("KnowledgeEntry source message is invalid")
                if (
                    entry.source_conversation_id is not None
                    and message.conversation_id != entry.source_conversation_id
                ):
                    raise ArchiveValidationError(
                        "KnowledgeEntry source message belongs to another Conversation"
                    )
        return normalized_documents

    async def _validate_document_files(
        self,
        archive_path: Path,
        knowledge_base: KnowledgeBaseArchiveRecord,
        documents: list[DocumentArchiveRecord],
        versions: list[DocumentVersionArchiveRecord],
        manifest: KnowledgeBaseArchiveManifest,
    ) -> list[RestoreDocumentSource]:
        documents_by_id = {item.id: item for item in documents}
        files_by_version = {item.document_version_id: item for item in manifest.document_files}
        if len(files_by_version) != len(manifest.document_files):
            raise ArchiveValidationError("Manifest has duplicate DocumentVersion files")
        if set(files_by_version) != {item.id for item in versions}:
            raise ArchiveValidationError("Manifest DocumentVersion file set is invalid")
        restore_files: list[RestoreDocumentSource] = []
        for version in versions:
            descriptor = files_by_version[version.id]
            expected_archive_path = document_file_archive_path(version.id, version.extension)
            if descriptor.path != expected_archive_path:
                raise ArchiveValidationError("DocumentVersion archive path is invalid")
            if descriptor.size != version.file_size or descriptor.sha256 != version.content_hash:
                raise ArchiveValidationError("DocumentVersion file metadata is inconsistent")
            size, content_hash = await self.archive_storage.hash_archive_entry(
                archive_path, descriptor.path
            )
            if size != descriptor.size or content_hash != descriptor.sha256:
                raise ArchiveValidationError("DocumentVersion file checksum is invalid")
            document = documents_by_id[version.document_id]
            storage_path = self.document_storage.final_relative_path(
                knowledge_base.id,
                document.id,
                version.id,
                version.extension,
            )
            restore_files.append(
                RestoreDocumentSource(
                    document_version_id=version.id,
                    archive_path=descriptor.path,
                    storage_path=storage_path,
                    size=descriptor.size,
                    sha256=descriptor.sha256,
                )
            )
        return restore_files

    @classmethod
    def _parse_model(cls, content: bytes, model_type: type[ArchiveModelT]) -> ArchiveModelT:
        cls._scan_json(content)
        return model_type.model_validate_json(content, strict=True)

    def _parse_jsonl(self, content: bytes, model_type: type[ArchiveModelT]) -> list[ArchiveModelT]:
        if not content:
            return []
        lines = content.splitlines()
        if len(lines) > self.archive_storage.limits.max_jsonl_records:
            raise ArchiveLimitExceededError("Archive JSONL contains too many records")
        if any(not line.strip() for line in lines):
            raise ArchiveValidationError("Archive JSONL contains an empty record")
        return [self._parse_model(line, model_type) for line in lines]

    @staticmethod
    def _scan_json(content: bytes) -> None:
        text = content.decode("utf-8")

        def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise DuplicateJsonKeyError(f"Duplicate JSON key: {key}")
                result[key] = value
            return result

        def reject_constant(value: str) -> None:
            raise ValueError(f"Invalid JSON number: {value}")

        json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )

    @staticmethod
    def _require_unique_ids(entity: str, ids: list[UUID]) -> None:
        if len(ids) != len(set(ids)):
            raise ArchiveValidationError(f"{entity} UUIDs are duplicated")


class KnowledgeBaseRestoreService:
    def __init__(
        self,
        session: AsyncSession,
        document_storage: LocalFileStorage,
        archive_storage: LocalArchiveStorage,
        allowed_extensions: set[str],
        repository: KnowledgeBaseArchiveRepository | None = None,
        restore_lock: RestoreAdvisoryLock | None = None,
    ) -> None:
        self.session = session
        self.document_storage = document_storage
        self.archive_storage = archive_storage
        self.repository = repository or KnowledgeBaseArchiveRepository(session)
        self.restore_lock = restore_lock or RestoreAdvisoryLock.from_session(session)
        self.validator = KnowledgeBaseArchiveValidator(
            archive_storage,
            document_storage,
            allowed_extensions,
        )

    async def restore(self, upload: UploadFile) -> KnowledgeBaseArchiveRestoreResponse:
        if upload.filename is None or not upload.filename.lower().endswith(".tracemind.zip"):
            raise ArchiveValidationError("Restore requires a .tracemind.zip archive")
        temporary: TemporaryArchiveUpload | None = None
        try:
            temporary = await self.archive_storage.write_restore_upload(upload)
            validated = await self.validator.validate(temporary.path)
            return await self._restore_validated(temporary.path, validated)
        finally:
            if temporary is not None:
                await self.archive_storage.discard_archive(temporary.path)

    async def _restore_validated(
        self,
        archive_path: Path,
        validated: ValidatedKnowledgeBaseArchive,
    ) -> KnowledgeBaseArchiveRestoreResponse:
        staged: StagedKnowledgeBaseRestore | None = None
        journal_path: Path | None = None
        journal = None
        committed = False
        operation_id = uuid4()
        async with self.restore_lock.hold(validated.knowledge_base.id):
            try:
                conflicts = await self.repository.find_restore_conflicts(
                    self._conflict_check(validated)
                )
                await self.session.rollback()
                final_path = self.document_storage.root / str(validated.knowledge_base.id)
                if final_path.exists():
                    conflicts.append("knowledge_base_storage_path")
                if conflicts:
                    raise ArchiveConflictError(sorted(set(conflicts)))

                staged = await self.archive_storage.stage_restore_files(
                    archive_path,
                    operation_id,
                    validated.knowledge_base.id,
                    list(validated.restore_files),
                )
                journal_path, journal = await self.archive_storage.create_restore_journal(
                    staged, list(validated.restore_files)
                )
                entities = self._restore_entities(validated)
                try:
                    async with self.session.begin():
                        await self.repository.add_restore_entities(entities)
                        await self.archive_storage.promote_restore(staged)
                        await self.archive_storage.mark_restore_promoted(journal_path, journal)
                    committed = True
                except IntegrityError as exc:
                    raise ArchiveConflictError(["database_constraint"]) from exc

                try:
                    await self.archive_storage.finish_restore(staged, journal_path)
                except ArchiveStorageError:
                    logger.warning(
                        "Restore committed but journal cleanup is pending operation_id=%s",
                        staged.operation_id,
                    )
                return self._response(validated)
            except Exception:
                if not committed:
                    await self.session.rollback()
                    database_exists: bool | None = False
                    if staged is not None and journal_path is not None and journal is not None:
                        try:
                            database_exists = await self.repository.knowledge_base_exists(
                                staged.knowledge_base_id
                            )
                            await self.session.rollback()
                        except Exception:
                            database_exists = None
                            logger.exception(
                                "Restore outcome is uncertain; startup journal "
                                "recovery is required operation_id=%s",
                                staged.operation_id,
                            )
                    if database_exists is True and journal_path is not None and journal is not None:
                        if await self.archive_storage.final_restore_is_complete(journal):
                            committed = True
                            try:
                                await self.archive_storage.finish_recovered_restore(
                                    journal_path, journal
                                )
                            except OSError:
                                logger.warning(
                                    "Committed restore journal cleanup remains "
                                    "pending operation_id=%s",
                                    staged.operation_id if staged is not None else None,
                                )
                            return self._response(validated)
                    if database_exists is False and staged is not None:
                        await self.archive_storage.compensate_failed_restore(staged)
                        await self.archive_storage.discard_restore_journal(journal_path)
                raise

    @staticmethod
    def _response(
        validated: ValidatedKnowledgeBaseArchive,
    ) -> KnowledgeBaseArchiveRestoreResponse:
        return KnowledgeBaseArchiveRestoreResponse(
            knowledge_base_id=validated.knowledge_base.id,
            archive_id=validated.manifest.archive_id,
            entity_counts=validated.manifest.entity_counts,
        )

    @staticmethod
    def _conflict_check(validated: ValidatedKnowledgeBaseArchive) -> RestoreConflictCheck:
        return RestoreConflictCheck(
            knowledge_base_id=validated.knowledge_base.id,
            knowledge_base_name=validated.knowledge_base.name,
            document_ids=tuple(item.id for item in validated.documents),
            document_version_ids=tuple(item.id for item in validated.document_versions),
            conversation_ids=tuple(item.id for item in validated.conversations),
            message_ids=tuple(item.id for item in validated.messages),
            knowledge_entry_ids=tuple(item.id for item in validated.knowledge_entries),
            normalized_paths=tuple(
                item.normalized_path for item in validated.normalized_documents.values()
            ),
            source_assistant_message_ids=tuple(
                item.source_assistant_message_id
                for item in validated.knowledge_entries
                if item.source_assistant_message_id is not None
            ),
        )

    @staticmethod
    def _restore_entities(
        validated: ValidatedKnowledgeBaseArchive,
    ) -> KnowledgeBaseRestoreEntities:
        knowledge_base_record = validated.knowledge_base
        knowledge_base = KnowledgeBase(
            id=knowledge_base_record.id,
            name=knowledge_base_record.name,
            description=knowledge_base_record.description,
            created_at=knowledge_base_record.created_at,
            updated_at=knowledge_base_record.updated_at,
        )
        documents = tuple(
            Document(
                id=item.id,
                knowledge_base_id=item.knowledge_base_id,
                name=item.name,
                normalized_name=validated.normalized_documents[item.id].normalized_name,
                relative_path=item.relative_path,
                normalized_path=validated.normalized_documents[item.id].normalized_path,
                source_type=item.source_type,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in validated.documents
        )
        files_by_version = {item.document_version_id: item for item in validated.restore_files}
        versions = tuple(
            DocumentVersion(
                id=item.id,
                document_id=item.document_id,
                version_number=item.version_number,
                content_hash=item.content_hash,
                file_size=item.file_size,
                mime_type=item.mime_type,
                extension=item.extension,
                storage_path=files_by_version[item.id].storage_path,
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
                index_attempt_generation=None,
                index_started_at=None,
                indexed_at=None,
                last_index_attempt_at=None,
                indexed_chunk_count=0,
                embedding_model=None,
                embedding_dimension=None,
                index_error_code=None,
                index_error_message=None,
                created_at=item.created_at,
            )
            for item in validated.document_versions
        )
        conversations = tuple(
            Conversation(
                id=item.id,
                knowledge_base_id=item.knowledge_base_id,
                title=item.title,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in validated.conversations
        )
        messages = tuple(
            ConversationMessage(
                id=item.id,
                conversation_id=item.conversation_id,
                role=item.role,
                status=item.status,
                content=item.content,
                trace_id=item.trace_id,
                sources=item.sources,
                generation_metadata=item.generation_metadata,
                created_at=item.created_at,
            )
            for item in validated.messages
        )
        entries = tuple(
            KnowledgeEntry(
                id=item.id,
                knowledge_base_id=item.knowledge_base_id,
                question=item.question,
                background=item.background,
                root_cause=item.root_cause,
                solution=item.solution,
                failed_attempts=item.failed_attempts,
                validation_status=item.validation_status,
                tags=item.tags,
                source_conversation_id=item.source_conversation_id,
                source_user_message_id=item.source_user_message_id,
                source_assistant_message_id=item.source_assistant_message_id,
                question_snapshot=item.question_snapshot,
                answer_snapshot=item.answer_snapshot,
                sources_snapshot=item.sources_snapshot,
                generation_metadata_snapshot=item.generation_metadata_snapshot,
                index_status="pending" if item.validation_status == "verified" else "not_indexed",
                active_index_generation=None,
                index_attempt_generation=None,
                index_started_at=None,
                indexed_at=None,
                indexed_source_updated_at=None,
                last_index_attempt_at=None,
                indexed_chunk_count=0,
                embedding_model=None,
                embedding_dimension=None,
                index_error_code=None,
                index_error_message=None,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in validated.knowledge_entries
        )
        return KnowledgeBaseRestoreEntities(
            knowledge_base=knowledge_base,
            documents=documents,
            document_versions=versions,
            conversations=conversations,
            messages=messages,
            knowledge_entries=entries,
        )


class KnowledgeBaseRestoreRecoveryService:
    def __init__(
        self,
        session: AsyncSession,
        archive_storage: LocalArchiveStorage,
        repository: KnowledgeBaseArchiveRepository | None = None,
        restore_lock: RestoreAdvisoryLock | None = None,
    ) -> None:
        self.session = session
        self.archive_storage = archive_storage
        self.repository = repository or KnowledgeBaseArchiveRepository(session)
        self.restore_lock = restore_lock or RestoreAdvisoryLock.from_session(session)

    async def recover(self) -> None:
        journals = await self.archive_storage.load_restore_journals()
        for path, journal in journals:
            async with self.restore_lock.try_hold(journal.knowledge_base_id) as acquired:
                if not acquired:
                    logger.info(
                        "Restore recovery deferred for active restore operation_id=%s kb_id=%s",
                        journal.operation_id,
                        journal.knowledge_base_id,
                    )
                    continue
                exists = await self.repository.knowledge_base_exists(journal.knowledge_base_id)
                await self.session.rollback()
                if not exists:
                    await self.archive_storage.recover_absent_database_restore(path, journal)
                    continue
                if await self.archive_storage.final_restore_is_complete(journal):
                    await self.archive_storage.finish_recovered_restore(path, journal)
                    continue
                logger.error(
                    "Restore journal requires manual attention operation_id=%s "
                    "knowledge_base_id=%s",
                    journal.operation_id,
                    journal.knowledge_base_id,
                )
