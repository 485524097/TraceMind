export type KnowledgeMapNodeType = 'knowledge_base' | 'knowledge_entry' | 'document' | 'tag'
export type KnowledgeMapEdgeType = 'contains' | 'cites' | 'tagged' | 'related'

export interface KnowledgeMapNode {
  id: string
  type: KnowledgeMapNodeType
  entity_id: string | null
  label: string
  metadata: Record<string, unknown>
}

export interface KnowledgeMapEdge {
  id: string
  type: KnowledgeMapEdgeType
  source: string
  target: string
  metadata: Record<string, unknown>
}

export interface KnowledgeMapResponse {
  nodes: KnowledgeMapNode[]
  edges: KnowledgeMapEdge[]
}
