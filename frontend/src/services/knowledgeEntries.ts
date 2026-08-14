import { apiRequest } from '@/services/api'
import type {
  KnowledgeEntry,
  KnowledgeEntryCreate,
  KnowledgeEntryInput,
  KnowledgeEntryListResponse,
  ValidationStatus,
} from '@/types/knowledgeEntry'

function basePath(knowledgeBaseId: string): string {
  return `/api/v1/knowledge-bases/${knowledgeBaseId}/knowledge-entries`
}

export function createKnowledgeEntry(
  knowledgeBaseId: string,
  payload: KnowledgeEntryCreate,
): Promise<KnowledgeEntry> {
  return apiRequest(basePath(knowledgeBaseId), {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function listKnowledgeEntries(
  knowledgeBaseId: string,
  filters: {
    query?: string
    validationStatus?: ValidationStatus | ''
    tag?: string
    offset?: number
    limit?: number
  } = {},
): Promise<KnowledgeEntryListResponse> {
  const params = new URLSearchParams({
    offset: String(filters.offset ?? 0),
    limit: String(filters.limit ?? 100),
  })
  if (filters.query?.trim()) params.set('query', filters.query.trim())
  if (filters.validationStatus) params.set('validation_status', filters.validationStatus)
  if (filters.tag?.trim()) params.set('tag', filters.tag.trim())
  return apiRequest(`${basePath(knowledgeBaseId)}?${params}`)
}

export function getKnowledgeEntry(
  knowledgeBaseId: string,
  entryId: string,
): Promise<KnowledgeEntry> {
  return apiRequest(`${basePath(knowledgeBaseId)}/${entryId}`)
}

export function updateKnowledgeEntry(
  knowledgeBaseId: string,
  entryId: string,
  payload: Partial<KnowledgeEntryInput>,
): Promise<KnowledgeEntry> {
  return apiRequest(`${basePath(knowledgeBaseId)}/${entryId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function deleteKnowledgeEntry(knowledgeBaseId: string, entryId: string): Promise<void> {
  return apiRequest(`${basePath(knowledgeBaseId)}/${entryId}`, { method: 'DELETE' })
}

export function requestKnowledgeEntryIndex(
  knowledgeBaseId: string,
  entryId: string,
  force = false,
): Promise<KnowledgeEntry> {
  return apiRequest(`${basePath(knowledgeBaseId)}/${entryId}/index`, {
    method: 'POST',
    body: JSON.stringify({ force }),
  })
}
