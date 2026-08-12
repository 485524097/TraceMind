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

export type RagPipelinePhase =
  | 'analyzing'
  | 'routing'
  | 'query_rewrite'
  | 'query_embedding'
  | 'hybrid_retrieval'
  | 'candidates'
  | 'reranking'
  | 'generating'
  | 'completed'

export type RagPipelineStatus = 'started' | 'completed' | 'skipped' | 'fallback' | 'failed'

export interface RagPipelineEvent extends ConversationEventFields {
  trace_id: string
  phase: RagPipelinePhase
  status: RagPipelineStatus
  elapsed_ms?: number
  candidate_count?: number
  route_mode?: 'direct' | 'rag'
  fallback_reason?: string
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

export interface RagRetrievalEvent extends ConversationEventFields {
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
  finish_reason: string
  grounded: boolean
  valid_citation_count: number
  invalid_citation_count: number
  retrieval_latency_ms: number
  llm_first_token_latency_ms?: number
  llm_latency_ms: number
  llm_generation_latency_ms?: number
  local_pre_llm_latency_ms?: number
  conversation_persistence_latency_ms?: number
  response_total_latency_ms?: number
  total_latency_ms: number
  route_mode?: 'direct' | 'rag'
  routing_latency_ms?: number
  embedding_latency_ms?: number
  qdrant_latency_ms?: number
  fusion_latency_ms?: number
  dense_candidate_count?: number
  sparse_candidate_count?: number
  source_count?: number
  retrieval_mode?: string
  rerank_latency_ms?: number
  reranker_fallback?: boolean
  query_rewrite_mode?: 'not_applicable' | 'skipped' | 'rewritten' | 'fallback'
  query_rewrite_latency_ms?: number
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
