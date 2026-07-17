import type {
  KnowledgeBase,
  KnowledgeBaseCreate,
  KnowledgeBaseListResponse,
  KnowledgeBaseUpdate,
} from '@/types/knowledgeBase'

import { apiRequest } from './api'

const basePath = '/api/v1/knowledge-bases'

export function listKnowledgeBases(offset = 0, limit = 100): Promise<KnowledgeBaseListResponse> {
  return apiRequest(`${basePath}?offset=${offset}&limit=${limit}`)
}

export function createKnowledgeBase(payload: KnowledgeBaseCreate): Promise<KnowledgeBase> {
  return apiRequest(basePath, { method: 'POST', body: JSON.stringify(payload) })
}

export function updateKnowledgeBase(
  knowledgeBaseId: string,
  payload: KnowledgeBaseUpdate,
): Promise<KnowledgeBase> {
  return apiRequest(`${basePath}/${knowledgeBaseId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function deleteKnowledgeBase(knowledgeBaseId: string): Promise<void> {
  return apiRequest(`${basePath}/${knowledgeBaseId}`, { method: 'DELETE' })
}
