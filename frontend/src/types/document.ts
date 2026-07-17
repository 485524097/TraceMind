export type DocumentImportAction = 'created' | 'version_created' | 'unchanged'

export interface DocumentVersion {
  id: string
  version_number: number
  content_hash: string
  file_size: number
  mime_type: string | null
  extension: string
  created_at: string
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
  document: DocumentItem
}
