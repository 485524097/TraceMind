import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.knowledge_base_archive import (
    KnowledgeBaseArchiveRepository,
    KnowledgeBaseArchiveSnapshot,
)
from app.schemas.knowledge_base_archive import (
    CONVERSATIONS_DATA_PATH,
    DOCUMENT_VERSIONS_DATA_PATH,
    DOCUMENTS_DATA_PATH,
    KNOWLEDGE_BASE_DATA_PATH,
    KNOWLEDGE_ENTRIES_DATA_PATH,
    MESSAGES_DATA_PATH,
    ArchiveDataEntry,
    ArchiveDocumentFileEntry,
    ArchiveEntityCounts,
    ArchiveKnowledgeBaseSummary,
    ConversationArchiveRecord,
    ConversationMessageArchiveRecord,
    DocumentArchiveRecord,
    DocumentVersionArchiveRecord,
    KnowledgeBaseArchiveManifest,
    KnowledgeBaseArchiveRecord,
    KnowledgeEntryArchiveRecord,
    StrictArchiveModel,
)
from app.services.exceptions import (
    ArchiveStorageError,
    DocumentStorageError,
    KnowledgeBaseNotFoundError,
)
from app.storage.archive import (
    ArchiveDataPayload,
    ArchiveDocumentSource,
    LocalArchiveStorage,
    StagedArchiveDocuments,
    document_file_archive_path,
)
from app.storage.local import LocalFileStorage


@dataclass(frozen=True)
class KnowledgeBaseArchiveExport:
    path: Path
    filename: str
    manifest: KnowledgeBaseArchiveManifest


class KnowledgeBaseArchiveService:
    def __init__(
        self,
        session: AsyncSession,
        document_storage: LocalFileStorage,
        archive_storage: LocalArchiveStorage,
        tracemind_version: str,
        repository: KnowledgeBaseArchiveRepository | None = None,
    ) -> None:
        self.session = session
        self.document_storage = document_storage
        self.archive_storage = archive_storage
        self.tracemind_version = tracemind_version
        self.repository = repository or KnowledgeBaseArchiveRepository(session)

    async def export(self, knowledge_base_id: UUID) -> KnowledgeBaseArchiveExport:
        staged: StagedArchiveDocuments | None = None
        archive_path: Path | None = None
        try:
            await self.session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            snapshot = await self.repository.load_export_snapshot(knowledge_base_id)
            if snapshot is None:
                raise KnowledgeBaseNotFoundError(knowledge_base_id)

            payloads = self._serialize_data(snapshot)
            sources = self._document_sources(snapshot)
            staged = await self.archive_storage.stage_document_files(sources)
            manifest = self._build_manifest(snapshot, payloads, staged)
            manifest_bytes = self._json_bytes(manifest)
            archive_path = await self.archive_storage.build_export_archive(
                manifest_bytes,
                payloads,
                staged,
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            if archive_path is not None:
                await self.archive_storage.discard_archive(archive_path)
            raise
        finally:
            if staged is not None:
                await self.archive_storage.discard_staged(staged)

        return KnowledgeBaseArchiveExport(
            path=archive_path,
            filename=self._download_filename(snapshot.knowledge_base.name),
            manifest=manifest,
        )

    async def discard_export(self, path: Path) -> None:
        await self.archive_storage.discard_archive(path)

    def _serialize_data(self, snapshot: KnowledgeBaseArchiveSnapshot) -> list[ArchiveDataPayload]:
        knowledge_base = KnowledgeBaseArchiveRecord.model_validate(snapshot.knowledge_base)
        documents = [DocumentArchiveRecord.model_validate(item) for item in snapshot.documents]
        versions = [
            DocumentVersionArchiveRecord.model_validate(item) for item in snapshot.document_versions
        ]
        conversations = [
            ConversationArchiveRecord.model_validate(item) for item in snapshot.conversations
        ]
        messages = [
            ConversationMessageArchiveRecord.model_validate(item) for item in snapshot.messages
        ]
        entries = [
            KnowledgeEntryArchiveRecord.model_validate(item) for item in snapshot.knowledge_entries
        ]
        return [
            ArchiveDataPayload(KNOWLEDGE_BASE_DATA_PATH, self._json_bytes(knowledge_base), 1),
            ArchiveDataPayload(DOCUMENTS_DATA_PATH, self._jsonl_bytes(documents), len(documents)),
            ArchiveDataPayload(
                DOCUMENT_VERSIONS_DATA_PATH,
                self._jsonl_bytes(versions),
                len(versions),
            ),
            ArchiveDataPayload(
                CONVERSATIONS_DATA_PATH,
                self._jsonl_bytes(conversations),
                len(conversations),
            ),
            ArchiveDataPayload(MESSAGES_DATA_PATH, self._jsonl_bytes(messages), len(messages)),
            ArchiveDataPayload(
                KNOWLEDGE_ENTRIES_DATA_PATH,
                self._jsonl_bytes(entries),
                len(entries),
            ),
        ]

    def _document_sources(
        self, snapshot: KnowledgeBaseArchiveSnapshot
    ) -> list[ArchiveDocumentSource]:
        sources: list[ArchiveDocumentSource] = []
        for version in snapshot.document_versions:
            try:
                source_path = self.document_storage.resolve_relative(version.storage_path)
            except DocumentStorageError as exc:
                raise ArchiveStorageError("A stored document is unavailable") from exc
            sources.append(
                ArchiveDocumentSource(
                    document_version_id=version.id,
                    archive_path=document_file_archive_path(version.id, version.extension),
                    source_path=source_path,
                    expected_size=version.file_size,
                    expected_sha256=version.content_hash,
                )
            )
        return sources

    def _build_manifest(
        self,
        snapshot: KnowledgeBaseArchiveSnapshot,
        payloads: list[ArchiveDataPayload],
        staged: StagedArchiveDocuments,
    ) -> KnowledgeBaseArchiveManifest:
        knowledge_base = snapshot.knowledge_base
        return KnowledgeBaseArchiveManifest(
            archive_id=uuid4(),
            tracemind_version=self.tracemind_version,
            exported_at=datetime.now(UTC),
            knowledge_base=ArchiveKnowledgeBaseSummary(
                id=knowledge_base.id,
                name=knowledge_base.name,
                description=knowledge_base.description,
                created_at=knowledge_base.created_at,
                updated_at=knowledge_base.updated_at,
            ),
            entity_counts=ArchiveEntityCounts(
                documents=len(snapshot.documents),
                document_versions=len(snapshot.document_versions),
                conversations=len(snapshot.conversations),
                messages=len(snapshot.messages),
                knowledge_entries=len(snapshot.knowledge_entries),
            ),
            data_entries=[
                ArchiveDataEntry(
                    path=payload.path,
                    size=len(payload.content),
                    sha256=hashlib.sha256(payload.content).hexdigest(),
                    record_count=payload.record_count,
                )
                for payload in payloads
            ],
            document_files=[
                ArchiveDocumentFileEntry(
                    document_version_id=entry.document_version_id,
                    path=entry.archive_path,
                    size=entry.size,
                    sha256=entry.sha256,
                )
                for entry in staged.entries
            ],
        )

    @staticmethod
    def _json_bytes(model: StrictArchiveModel) -> bytes:
        return json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def _jsonl_bytes(cls, models: Sequence[StrictArchiveModel]) -> bytes:
        if not models:
            return b""
        return b"\n".join(cls._json_bytes(model) for model in models) + b"\n"

    @staticmethod
    def _download_filename(name: str) -> str:
        normalized = re.sub(r"[^\w.-]+", "-", name.strip(), flags=re.UNICODE)
        safe_name = normalized.strip(".-_")[:80] or "knowledge-base"
        return f"{safe_name}.tracemind.zip"
