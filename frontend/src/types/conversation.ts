import type { RagDoneEvent, RagSource } from '@/types/rag'

export type ConversationMessageStatus = 'completed' | 'no_answer' | 'failed' | 'cancelled'

export interface Conversation {
  id: string
  knowledge_base_id: string
  title: string
  created_at: string
  updated_at: string
}

export interface ConversationMessage {
  id: string
  conversation_id: string
  role: 'user' | 'assistant'
  status: ConversationMessageStatus
  content: string
  trace_id: string | null
  sources: RagSource[] | null
  generation_metadata: Partial<RagDoneEvent> | null
  created_at: string
}

export interface ConversationDetail extends Conversation {
  messages: ConversationMessage[]
}

export interface ConversationListResponse {
  items: Conversation[]
  total: number
  offset: number
  limit: number
}
