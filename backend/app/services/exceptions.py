from uuid import UUID


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
