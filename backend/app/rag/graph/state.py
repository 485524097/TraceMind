from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict
from uuid import UUID

from langchain_core.language_models.chat_models import BaseChatModel

from app.core.config import Settings
from app.services.conversation import ConversationTurn
from app.services.document_reranking import DocumentRerankingService
from app.services.query_router import RouteMode
from app.services.rag_retrieval import (
    RagRetrievalServiceProtocol,
    RetrievalSearchResult,
)
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
    retrieval_candidates: NotRequired[list[RetrievalSearchResult]]
    embedding_latency_ms: NotRequired[int]
    qdrant_latency_ms: NotRequired[int]
    fusion_latency_ms: NotRequired[int]
    dense_candidate_count: NotRequired[int]
    sparse_candidate_count: NotRequired[int]
    ranked_results: NotRequired[list[RetrievalSearchResult]]
    retrieval_mode: NotRequired[str]
    rerank_latency_ms: NotRequired[int]
    reranker_fallback: NotRequired[bool]
    reranker_fallback_reason: NotRequired[str | None]
    answer: NotRequired[str]
    terminal_status: NotRequired[TerminalStatus]


@dataclass(frozen=True, slots=True)
class RagRuntimeContext:
    model: BaseChatModel
    settings: Settings
    retrieval_service: RagRetrievalServiceProtocol
    reranking_service: DocumentRerankingService | None
