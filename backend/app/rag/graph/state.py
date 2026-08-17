from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict
from uuid import UUID

from langchain_core.language_models.chat_models import BaseChatModel

from app.services.query_router import RouteMode

TerminalStatus = Literal["completed", "rag_pending"]


class RagState(TypedDict):
    trace_id: UUID
    knowledge_base_id: UUID
    query: str
    language: str | None
    document_id: UUID | None
    route_mode: NotRequired[RouteMode]
    answer: NotRequired[str]
    terminal_status: NotRequired[TerminalStatus]


@dataclass(frozen=True, slots=True)
class RagRuntimeContext:
    model: BaseChatModel
