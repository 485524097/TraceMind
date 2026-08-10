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

export interface SymbolScopeEventFields {
  symbol_scope_mode?: 'none' | 'exact' | 'fallback'
  symbol_scope_reason?: 'not_found' | 'ambiguous' | 'unsupported' | null
  scoped_symbol_kind?: string | null
  scoped_symbol_qualified_name?: string | null
  scoped_symbol_signature?: string | null
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

export interface RagRetrievalEvent extends ConversationEventFields, SymbolScopeEventFields {
  trace_id: string
  source_count: number
  sources: RagSource[]
}

export interface RagTokenEvent extends ConversationEventFields {
  trace_id: string
  text: string
}

export interface RagNoAnswerEvent extends ConversationEventFields, SymbolScopeEventFields {
  trace_id: string
  message: string
}

export interface RagDoneEvent extends ConversationEventFields, SymbolScopeEventFields {
  trace_id: string
  finish_reason: string
  grounded: boolean
  valid_citation_count: number
  invalid_citation_count: number
  retrieval_latency_ms: number
  llm_first_token_latency_ms?: number
  llm_latency_ms: number
  total_latency_ms: number
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

export interface RagErrorEvent extends ConversationEventFields, SymbolScopeEventFields {
  trace_id: string
  code: string
  message: string
}
