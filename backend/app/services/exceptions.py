from uuid import UUID

from app.parsing.exceptions import (
    DocumentEncodingError,
    DocumentParseError,
    NoExtractableTextError,
    ParseLimitExceededError,
    PdfEncryptedError,
    UnsupportedParserError,
)


class KnowledgeBaseError(Exception):
    """Base exception for knowledge base business rules."""


class KnowledgeBaseNotFoundError(KnowledgeBaseError):
    def __init__(self, knowledge_base_id: UUID) -> None:
        super().__init__(f"Knowledge base {knowledge_base_id} was not found")
        self.knowledge_base_id = knowledge_base_id


class KnowledgeBaseNameConflictError(KnowledgeBaseError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Knowledge base name already exists: {name}")
        self.name = name


class KnowledgeBaseNotEmptyError(KnowledgeBaseError):
    def __init__(self, knowledge_base_id: UUID) -> None:
        super().__init__(f"Knowledge base {knowledge_base_id} must be empty before deletion")
        self.knowledge_base_id = knowledge_base_id


class KnowledgeBaseRebuildAlreadyActiveError(KnowledgeBaseError):
    def __init__(self, knowledge_base_id: UUID) -> None:
        super().__init__(f"Knowledge base {knowledge_base_id} already has an active rebuild")
        self.knowledge_base_id = knowledge_base_id


class KnowledgeBaseRebuildNotFoundError(KnowledgeBaseError):
    def __init__(self, knowledge_base_id: UUID) -> None:
        super().__init__(f"Knowledge base {knowledge_base_id} has no rebuild operation")
        self.knowledge_base_id = knowledge_base_id


class KnowledgeBaseRebuildNotRetryableError(KnowledgeBaseError):
    def __init__(self, knowledge_base_id: UUID) -> None:
        super().__init__(f"Knowledge base {knowledge_base_id} rebuild is not retryable")
        self.knowledge_base_id = knowledge_base_id


class ConsistencyAuditSelectionError(Exception):
    """Audit or selected findings are missing or outside the requested Knowledge Base."""


class ConsistencyRepairNotFoundError(Exception):
    """The requested repair operation does not exist in the Knowledge Base."""


class ConsistencyRepairAlreadyActiveError(Exception):
    """The Knowledge Base already has a queued or running consistency repair."""


class ConsistencyRepairNotRetryableError(Exception):
    """The requested consistency repair cannot be retried."""


class KnowledgeBaseArchiveError(Exception):
    """Base exception for Knowledge Base archive operations."""


class ArchiveStorageError(KnowledgeBaseArchiveError):
    pass


class ArchiveLimitExceededError(KnowledgeBaseArchiveError):
    pass


class ArchiveSourceIntegrityError(KnowledgeBaseArchiveError):
    pass


class ArchiveValidationError(KnowledgeBaseArchiveError):
    pass


class ArchiveConflictError(KnowledgeBaseArchiveError):
    def __init__(self, conflicts: list[str]) -> None:
        super().__init__("Archive conflicts with existing data")
        self.conflicts = tuple(conflicts)


class DocumentError(Exception):
    """Base exception for document ingestion business rules."""


class DocumentNotFoundError(DocumentError):
    pass


class InvalidDocumentNameError(DocumentError):
    pass


class UnsupportedDocumentTypeError(DocumentError):
    pass


class DocumentTooLargeError(DocumentError):
    pass


class EmptyDocumentError(DocumentError):
    pass


class DocumentStorageError(DocumentError):
    pass


class DocumentImportConflictError(DocumentError):
    pass


class DocumentVersionNotFoundError(DocumentError):
    pass


class DocumentAlreadyProcessingError(DocumentError):
    pass


class DocumentAlreadyParsedError(DocumentError):
    pass


class DocumentParsingQueueError(DocumentError):
    pass


class DocumentIndexingQueueError(DocumentError):
    pass


class DocumentNotReadyForIndexError(DocumentError):
    pass


class SemanticSearchUnavailableError(DocumentError):
    def __init__(
        self,
        message: str,
        *,
        scope_metadata: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.scope_metadata = scope_metadata


class HybridSearchUnavailableError(DocumentError):
    pass


class ConversationError(Exception):
    """Base exception for conversation business rules."""


class ConversationNotFoundError(ConversationError):
    def __init__(self, conversation_id: UUID) -> None:
        super().__init__(f"Conversation {conversation_id} was not found")
        self.conversation_id = conversation_id


class KnowledgeEntryError(Exception):
    """Base exception for structured problem and solution knowledge."""


class KnowledgeEntryNotFoundError(KnowledgeEntryError):
    def __init__(self, entry_id: UUID) -> None:
        super().__init__(f"Knowledge entry {entry_id} was not found")
        self.entry_id = entry_id


class KnowledgeEntrySourceNotFoundError(KnowledgeEntryError):
    pass


class InvalidKnowledgeEntrySourceError(KnowledgeEntryError):
    pass


class KnowledgeEntryAlreadyExistsError(KnowledgeEntryError):
    def __init__(self, source_message_id: UUID) -> None:
        super().__init__(f"Knowledge entry already exists for answer {source_message_id}")
        self.source_message_id = source_message_id


class KnowledgeEntryIndexingQueueError(KnowledgeEntryError):
    pass


class KnowledgeEntryNotReadyForIndexError(KnowledgeEntryError):
    pass


__all__ = [
    "DocumentAlreadyParsedError",
    "DocumentAlreadyProcessingError",
    "DocumentEncodingError",
    "DocumentParseError",
    "DocumentParsingQueueError",
    "DocumentVersionNotFoundError",
    "NoExtractableTextError",
    "ParseLimitExceededError",
    "PdfEncryptedError",
    "UnsupportedParserError",
]
