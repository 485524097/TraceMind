import type { EvidenceSource } from '@/types/evidence'

export interface RagStreamRequest {
  query: string
  language?: string | null
  document_id?: string | null
  conversation_id?: string | null
}

export interface ConversationEventFields {
  conversation_id?: string
  message_id?: string
}

export interface RagSource extends EvidenceSource {
  score: number
  knowledge_base_id: string
  index_generation: string
  ranking_mode?: string | null
  retrieval_score?: number | null
  rerank_score?: number | null
  retrieval_rank?: number | null
}

export interface RagSourcesEvent extends ConversationEventFields {
  trace_id: string
  source_count: number
  sources: RagSource[]
}

export interface RagTokenEvent extends ConversationEventFields {
  trace_id: string
  text: string
}

export interface RagNoAnswerEvent extends ConversationEventFields {
  trace_id: string
  message: string
}

export interface RagDoneEvent extends ConversationEventFields {
  trace_id: string
  terminal_status: 'completed' | 'no_answer'
  grounded: boolean
  valid_citation_count: number
  invalid_citation_count: number
  conversation_persistence_latency_ms?: number
  response_total_latency_ms?: number
  route_mode?: 'direct' | 'rag'
  embedding_latency_ms?: number
  qdrant_latency_ms?: number
  fusion_latency_ms?: number
  dense_candidate_count?: number
  sparse_candidate_count?: number
  source_count?: number
  retrieval_mode?: string
  rerank_latency_ms?: number
  reranker_fallback?: boolean
  reranker_fallback_reason?: string | null
  query_rewrite_mode?: 'not_applicable' | 'skipped' | 'rewritten' | 'fallback'
  query_rewrite_latency_ms?: number
  query_rewrite_fallback_reason?: 'timeout' | 'model_error' | 'invalid_response' | null
  history_turn_count?: number
  retrieval_query?: string
  path_scope_mode?: 'none' | 'exact'
  scoped_relative_path?: string | null
}

export interface RagErrorEvent extends ConversationEventFields {
  trace_id: string
  code: string
  message: string
}
