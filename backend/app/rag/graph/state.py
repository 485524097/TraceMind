from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict
from uuid import UUID

from langchain_core.language_models.chat_models import BaseChatModel

from app.core.config import Settings
from app.services.conversation import ConversationTurn
from app.services.query_router import RouteMode
from app.services.rag_retrieval import RagRetrievalServiceProtocol
from app.services.retrieval_query import PreparedRetrievalQuery

TerminalStatus = Literal["completed", "rag_pending"]
QueryRewriteMode = Literal["not_applicable", "skipped", "rewritten", "fallback"]
QueryRewriteFallbackReason = Literal["timeout", "model_error", "invalid_response"]


class RagState(TypedDict):
    trace_id: UUID
    knowledge_base_id: UUID
    query: str
    language: str | None
    document_id: UUID | None
    conversation_history: NotRequired[tuple[ConversationTurn, ...]]
    route_mode: NotRequired[RouteMode]
    prepared_retrieval_query: NotRequired[PreparedRetrievalQuery]
    retrieval_query: NotRequired[str]
    query_rewrite_mode: NotRequired[QueryRewriteMode]
    query_rewrite_latency_ms: NotRequired[int]
    query_rewrite_fallback_reason: NotRequired[QueryRewriteFallbackReason | None]
    answer: NotRequired[str]
    terminal_status: NotRequired[TerminalStatus]


@dataclass(frozen=True, slots=True)
class RagRuntimeContext:
    model: BaseChatModel
    settings: Settings
    retrieval_service: RagRetrievalServiceProtocol
