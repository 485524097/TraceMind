export interface EvidenceSource {
  source_id: string
  document_id: string
  document_version_id: string
  chunk_id: string
  document_name: string
  relative_path?: string
  version_number: number
  chunk_index: number
  content: string
  content_hash: string
  chunk_type: string
  language: string | null
  section_title: string | null
  page_number: number | null
  start_line: number | null
  end_line: number | null
}
