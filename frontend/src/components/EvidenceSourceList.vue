<script setup lang="ts">
import type { EvidenceSource } from '@/types/evidence'

defineProps<{
  sources: EvidenceSource[]
  identityPrefix?: string
}>()

const isCodeSource = (source: EvidenceSource) =>
  source.chunk_type === 'code' ||
  (source.language !== null && source.start_line !== null && source.end_line !== null)

const isKnowledgeSource = (source: EvidenceSource) => source.source_type === 'knowledge_entry'

function sourceType(source: EvidenceSource): string {
  if (isKnowledgeSource(source)) return '知识'
  return isCodeSource(source) ? '代码' : '文档'
}

function sourceTitle(source: EvidenceSource): string {
  return source.knowledge_question || source.relative_path || source.document_name || '未知来源'
}

function sourceLocation(source: EvidenceSource): string {
  if (isKnowledgeSource(source)) {
    return source.section_title || `知识片段 ${source.chunk_index + 1}`
  }
  if (source.page_number !== null) return `第 ${source.page_number} 页`
  if (source.start_line !== null && source.end_line !== null) {
    return `第 ${source.start_line}–${source.end_line} 行`
  }
  return `Chunk ${source.chunk_index}`
}
</script>

<template>
  <div class="evidence-source-list">
    <div
      v-for="source in sources"
      :id="identityPrefix ? `evidence-source-${identityPrefix}-${source.source_id}` : undefined"
      :key="source.source_id"
      class="ev-src"
      :class="{ code: isCodeSource(source), knowledge: isKnowledgeSource(source) }"
      :data-testid="
        identityPrefix
          ? `evidence-source-${identityPrefix}-${source.source_id}`
          : `evidence-source-${source.source_id}`
      "
    >
      <span class="ev-type" :class="{ 'ev-type-code': isCodeSource(source) }">
        {{ sourceType(source) }}
      </span>
      <div class="ev-src-id-row">
        <span class="ev-src-id">{{ source.source_id }}</span>
        <RouterLink
          v-if="isKnowledgeSource(source) && source.knowledge_base_id && source.knowledge_entry_id"
          class="ev-src-path text-action"
          :to="`/knowledge-bases/${source.knowledge_base_id}/knowledge/${source.knowledge_entry_id}`"
        >
          {{ sourceTitle(source) }}
        </RouterLink>
        <span v-else class="ev-src-path">{{ sourceTitle(source) }}</span>
      </div>
      <div class="ev-src-loc">
        {{
          isKnowledgeSource(source) ? '已验证知识' : source.section_title || source.document_name
        }}
        ·
        {{ sourceLocation(source) }}
      </div>
      <div class="ev-src-excerpt">{{ source.content }}</div>
    </div>
  </div>
</template>
