export interface EvidenceSource {
  source_id: string
  source_type?: 'document' | 'knowledge_entry'
  knowledge_base_id?: string
  document_id?: string | null
  document_version_id?: string | null
  chunk_id: string
  document_name?: string | null
  relative_path?: string | null
  version_number?: number | null
  chunk_index: number
  content: string
  content_hash: string
  chunk_type: string
  language: string | null
  section_title: string | null
  page_number: number | null
  start_line: number | null
  end_line: number | null
  knowledge_entry_id?: string | null
  knowledge_question?: string | null
  knowledge_updated_at?: string | null
}
