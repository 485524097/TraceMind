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
