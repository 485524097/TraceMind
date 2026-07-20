import { apiRequest, apiUrl } from './api'
import type {
  DocumentImportResponse,
  DocumentItem,
  DocumentListResponse,
  DocumentVersion,
} from '@/types/document'

function basePath(knowledgeBaseId: string): string {
  return `/api/v1/knowledge-bases/${knowledgeBaseId}/documents`
}

export function listDocuments(
  knowledgeBaseId: string,
  query = '',
  offset = 0,
  limit = 100,
): Promise<DocumentListResponse> {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) })
  if (query.trim()) params.set('query', query.trim())
  return apiRequest(`${basePath(knowledgeBaseId)}?${params}`)
}

export function getDocument(
  knowledgeBaseId: string,
  documentId: string,
): Promise<DocumentItem> {
  return apiRequest(`${basePath(knowledgeBaseId)}/${documentId}`)
}

export function uploadDocument(
  knowledgeBaseId: string,
  file: File,
): Promise<DocumentImportResponse> {
  const body = new FormData()
  body.append('file', file)
  return apiRequest(basePath(knowledgeBaseId), { method: 'POST', body })
}

export function listDocumentVersions(
  knowledgeBaseId: string,
  documentId: string,
): Promise<DocumentVersion[]> {
  return apiRequest(`${basePath(knowledgeBaseId)}/${documentId}/versions`)
}

export function deleteDocument(knowledgeBaseId: string, documentId: string): Promise<void> {
  return apiRequest(`${basePath(knowledgeBaseId)}/${documentId}`, { method: 'DELETE' })
}

export function downloadCurrentDocument(knowledgeBaseId: string, documentId: string): void {
  triggerDownload(`${basePath(knowledgeBaseId)}/${documentId}/download`)
}

export function downloadDocumentVersion(
  knowledgeBaseId: string,
  documentId: string,
  versionId: string,
): void {
  triggerDownload(`${basePath(knowledgeBaseId)}/${documentId}/versions/${versionId}/download`)
}

function triggerDownload(path: string): void {
  const link = window.document.createElement('a')
  link.href = apiUrl(path)
  link.style.display = 'none'
  window.document.body.append(link)
  link.click()
  link.remove()
}
