import { apiRequest, apiUrl, ApiError } from './api'
import type {
  DocumentChunkListResponse,
  DocumentImportResponse,
  DocumentIndexRequestResponse,
  DocumentItem,
  DocumentListResponse,
  DocumentParseRequestResponse,
  DocumentVersion,
  SemanticSearchResponse,
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

export function getDocument(knowledgeBaseId: string, documentId: string): Promise<DocumentItem> {
  return apiRequest(`${basePath(knowledgeBaseId)}/${documentId}`)
}

export function uploadDocument(
  knowledgeBaseId: string,
  file: File,
  relativePath?: string,
  signal?: AbortSignal,
  onProgress?: (transferred: number, total: number) => void,
): Promise<DocumentImportResponse> {
  const body = new FormData()
  body.append('file', file)
  if (relativePath) body.append('relative_path', relativePath)
  if (!onProgress) {
    return apiRequest(basePath(knowledgeBaseId), { method: 'POST', body, signal })
  }
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest()
    const abort = () => request.abort()
    request.open('POST', apiUrl(basePath(knowledgeBaseId)))
    request.setRequestHeader('Accept', 'application/json')
    request.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable && event.total > 0) onProgress?.(event.loaded, event.total)
    })
    request.addEventListener('load', () => {
      signal?.removeEventListener('abort', abort)
      let payload: { detail?: string } | DocumentImportResponse | null = null
      try {
        payload = JSON.parse(request.responseText) as
          | { detail?: string }
          | DocumentImportResponse
          | null
      } catch {
        payload = null
      }
      if (request.status < 200 || request.status >= 300) {
        reject(
          new ApiError(
            request.status,
            payload && 'detail' in payload
              ? (payload.detail ?? '请求失败，请稍后重试')
              : '请求失败，请稍后重试',
          ),
        )
        return
      }
      resolve(payload as DocumentImportResponse)
    })
    request.addEventListener('error', () => {
      signal?.removeEventListener('abort', abort)
      reject(new ApiError(0, '网络连接失败'))
    })
    request.addEventListener('abort', () => {
      signal?.removeEventListener('abort', abort)
      reject(new DOMException('Upload cancelled', 'AbortError'))
    })
    if (signal?.aborted) {
      reject(new DOMException('Upload cancelled', 'AbortError'))
      return
    }
    signal?.addEventListener('abort', abort, { once: true })
    request.send(body)
  })
}

export function listDocumentVersions(
  knowledgeBaseId: string,
  documentId: string,
): Promise<DocumentVersion[]> {
  return apiRequest(`${basePath(knowledgeBaseId)}/${documentId}/versions`)
}

export function requestDocumentParse(
  knowledgeBaseId: string,
  documentId: string,
  versionId: string,
  force = false,
): Promise<DocumentParseRequestResponse> {
  const params = new URLSearchParams({ force: String(force) })
  return apiRequest(
    `${basePath(knowledgeBaseId)}/${documentId}/versions/${versionId}/parse?${params}`,
    { method: 'POST' },
  )
}

export function requestDocumentIndex(
  knowledgeBaseId: string,
  documentId: string,
  versionId: string,
  force = false,
): Promise<DocumentIndexRequestResponse> {
  return apiRequest(`${basePath(knowledgeBaseId)}/${documentId}/versions/${versionId}/index`, {
    method: 'POST',
    body: JSON.stringify({ force }),
  })
}

export function semanticSearch(
  knowledgeBaseId: string,
  query: string,
  language: string | null,
  limit = 5,
): Promise<SemanticSearchResponse> {
  return apiRequest(`/api/v1/knowledge-bases/${knowledgeBaseId}/search/semantic`, {
    method: 'POST',
    body: JSON.stringify({ query, language: language || null, limit }),
  })
}

export function hybridSearch(
  knowledgeBaseId: string,
  query: string,
  language: string | null,
  limit = 5,
): Promise<SemanticSearchResponse> {
  return apiRequest(`/api/v1/knowledge-bases/${knowledgeBaseId}/search/hybrid`, {
    method: 'POST',
    body: JSON.stringify({ query, language: language || null, limit }),
  })
}

export function rerankedSearch(
  knowledgeBaseId: string,
  query: string,
  language: string | null,
  limit = 5,
): Promise<SemanticSearchResponse> {
  return apiRequest(`/api/v1/knowledge-bases/${knowledgeBaseId}/search/reranked`, {
    method: 'POST',
    body: JSON.stringify({ query, language: language || null, limit }),
  })
}

export function listDocumentChunks(
  knowledgeBaseId: string,
  documentId: string,
  versionId: string,
  offset = 0,
  limit = 20,
): Promise<DocumentChunkListResponse> {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) })
  return apiRequest(
    `${basePath(knowledgeBaseId)}/${documentId}/versions/${versionId}/chunks?${params}`,
  )
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
