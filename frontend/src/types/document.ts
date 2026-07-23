export type DocumentImportAction = 'created' | 'version_created' | 'unchanged'
export type DocumentParseStatus = 'pending' | 'processing' | 'succeeded' | 'failed'
export type DocumentIndexStatus = 'pending' | 'processing' | 'succeeded' | 'failed'

export interface DocumentVersion {
  id: string
  version_number: number
  content_hash: string
  file_size: number
  mime_type: string | null
  extension: string
  created_at: string
  parse_status: DocumentParseStatus
  parser_name: string | null
  parser_version: string | null
  chunk_count: number
  parse_started_at: string | null
  parsed_at: string | null
  last_parse_attempt_at: string | null
  parse_error_code: string | null
  parse_error_message: string | null
  index_status: DocumentIndexStatus
  active_index_generation: string | null
  index_started_at: string | null
  indexed_at: string | null
  last_index_attempt_at: string | null
  indexed_chunk_count: number
  embedding_model: string | null
  embedding_dimension: number | null
  index_error_code: string | null
  index_error_message: string | null
}

export interface DocumentItem {
  id: string
  knowledge_base_id: string
  name: string
  source_type: string
  created_at: string
  updated_at: string
  version_count: number
  latest_version: DocumentVersion
}

export interface DocumentListResponse {
  items: DocumentItem[]
  total: number
  offset: number
  limit: number
}

export interface DocumentImportResponse {
  import_action: DocumentImportAction
  parsing_queued: boolean
  document: DocumentItem
}

export interface DocumentParseStatusResponse {
  version_id: string
  parse_status: DocumentParseStatus
  parser_name: string | null
  parser_version: string | null
  chunk_count: number
  parse_started_at: string | null
  parsed_at: string | null
  last_parse_attempt_at: string | null
  parse_error_code: string | null
  parse_error_message: string | null
}

export interface DocumentParseRequestResponse {
  queued: boolean
  version: DocumentParseStatusResponse
}

export interface DocumentChunk {
  id: string
  chunk_index: number
  content: string
  content_hash: string
  char_count: number
  page_number: number | null
  start_line: number | null
  end_line: number | null
  section_title: string | null
  chunk_type: string
  language: string | null
  created_at: string
}

export interface DocumentChunkListResponse {
  items: DocumentChunk[]
  total: number
  offset: number
  limit: number
  version: DocumentParseStatusResponse
}

export interface DocumentIndexStatusResponse {
  version_id: string
  index_status: DocumentIndexStatus
  active_index_generation: string | null
  index_started_at: string | null
  indexed_at: string | null
  last_index_attempt_at: string | null
  indexed_chunk_count: number
  embedding_model: string | null
  embedding_dimension: number | null
  index_error_code: string | null
  index_error_message: string | null
}

export interface DocumentIndexRequestResponse {
  queued: boolean
  version: DocumentIndexStatusResponse
}

export interface SemanticSearchResult {
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

export interface SemanticSearchResponse {
  items: SemanticSearchResult[]
}
