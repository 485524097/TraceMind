import type { EvidenceSource } from '@/types/evidence'

export type ValidationStatus = 'unverified' | 'verified' | 'outdated'

export interface KnowledgeEntryInput {
  question: string
  background: string | null
  root_cause: string | null
  solution: string
  failed_attempts: string[]
  validation_status: ValidationStatus
  tags: string[]
}

export interface KnowledgeEntryCreate extends KnowledgeEntryInput {
  source_assistant_message_id: string
}

export interface KnowledgeEntry extends KnowledgeEntryInput {
  id: string
  knowledge_base_id: string
  source_conversation_id: string | null
  source_user_message_id: string | null
  source_assistant_message_id: string | null
  question_snapshot: string
  answer_snapshot: string
  sources_snapshot: EvidenceSource[]
  generation_metadata_snapshot: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

export interface KnowledgeEntryListResponse {
  items: KnowledgeEntry[]
  total: number
  offset: number
  limit: number
  available_tags: string[]
}
