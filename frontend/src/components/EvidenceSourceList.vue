<script setup lang="ts">
import type { EvidenceSource } from '@/types/evidence'

defineProps<{
  sources: EvidenceSource[]
  identityPrefix?: string
}>()

const isCodeSource = (source: EvidenceSource) =>
  source.chunk_type === 'code' ||
  (source.language !== null && source.start_line !== null && source.end_line !== null)

function sourceLocation(source: EvidenceSource): string {
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
      :class="{ code: isCodeSource(source) }"
      :data-testid="
        identityPrefix
          ? `evidence-source-${identityPrefix}-${source.source_id}`
          : `evidence-source-${source.source_id}`
      "
    >
      <span class="ev-type" :class="{ 'ev-type-code': isCodeSource(source) }">
        {{ isCodeSource(source) ? '代码' : '文档' }}
      </span>
      <div class="ev-src-id-row">
        <span class="ev-src-id">{{ source.source_id }}</span>
        <span class="ev-src-path">{{ source.relative_path || source.document_name }}</span>
      </div>
      <div class="ev-src-loc">
        {{ source.section_title || source.document_name }} · {{ sourceLocation(source) }}
      </div>
      <div class="ev-src-excerpt">{{ source.content }}</div>
    </div>
  </div>
</template>
