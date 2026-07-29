import { apiRequest } from '@/services/api'
import type {
  Conversation,
  ConversationDetail,
  ConversationListResponse,
} from '@/types/conversation'

function basePath(knowledgeBaseId: string): string {
  return `/api/v1/knowledge-bases/${knowledgeBaseId}/conversations`
}

export function listConversations(
  knowledgeBaseId: string,
  offset = 0,
  limit = 100,
): Promise<ConversationListResponse> {
  return apiRequest(`${basePath(knowledgeBaseId)}?offset=${offset}&limit=${limit}`)
}

export function createConversation(
  knowledgeBaseId: string,
  title?: string,
): Promise<Conversation> {
  return apiRequest(basePath(knowledgeBaseId), {
    method: 'POST',
    body: JSON.stringify(title ? { title } : {}),
  })
}

export function getConversation(
  knowledgeBaseId: string,
  conversationId: string,
): Promise<ConversationDetail> {
  return apiRequest(`${basePath(knowledgeBaseId)}/${conversationId}`)
}

export function renameConversation(
  knowledgeBaseId: string,
  conversationId: string,
  title: string,
): Promise<Conversation> {
  return apiRequest(`${basePath(knowledgeBaseId)}/${conversationId}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  })
}

export function deleteConversation(
  knowledgeBaseId: string,
  conversationId: string,
): Promise<void> {
  return apiRequest(`${basePath(knowledgeBaseId)}/${conversationId}`, { method: 'DELETE' })
}
