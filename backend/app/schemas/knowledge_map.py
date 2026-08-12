from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

KnowledgeMapNodeType = Literal["knowledge_base", "knowledge_entry", "document", "tag"]
KnowledgeMapEdgeType = Literal["contains", "cites", "tagged", "related"]


class KnowledgeMapNode(BaseModel):
    id: str
    type: KnowledgeMapNodeType
    entity_id: UUID | None
    label: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeMapEdge(BaseModel):
    id: str
    type: KnowledgeMapEdgeType
    source: str
    target: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeMapResponse(BaseModel):
    nodes: list[KnowledgeMapNode]
    edges: list[KnowledgeMapEdge]
