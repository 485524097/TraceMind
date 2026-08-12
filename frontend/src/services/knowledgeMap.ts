import { apiRequest } from './api'
import type { KnowledgeMapResponse } from '@/types/knowledgeMap'

export function getKnowledgeMap(knowledgeBaseId: string): Promise<KnowledgeMapResponse> {
  return apiRequest(`/api/v1/knowledge-bases/${knowledgeBaseId}/knowledge-map`)
}
