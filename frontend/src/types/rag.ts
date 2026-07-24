export interface RagStreamRequest {
  query: string
  language?: string | null
  document_id?: string | null
}

export interface RagSource {
  source_id: string
  score: number
  content: string
  knowledge_base_id: string
  document_id: string
  document_version_id: string
  chunk_id: string
  index_generation: string
  document_name: string
  version_number: number
  chunk_index: number
  content_hash: string
  chunk_type: string
  language: string | null
  section_title: string | null
  page_number: number | null
  start_line: number | null
  end_line: number | null
}

export interface RagRetrievalEvent {
  trace_id: string
  source_count: number
  sources: RagSource[]
}

export interface RagTokenEvent {
  trace_id: string
  text: string
}

export interface RagNoAnswerEvent {
  trace_id: string
  message: string
}

export interface RagDoneEvent {
  trace_id: string
  finish_reason: string
  grounded: boolean
  valid_citation_count: number
  invalid_citation_count: number
  retrieval_latency_ms: number
  llm_latency_ms: number
  total_latency_ms: number
}

export interface RagErrorEvent {
  trace_id: string
  code: string
  message: string
}
