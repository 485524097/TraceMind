<script setup lang="ts">
import type { EvidenceSource } from '@/types/evidence'

defineProps<{
  sources: EvidenceSource[]
  identityPrefix?: string
}>()

const isCodeSource = (source: EvidenceSource) =>
  !!(source.symbol_kind || source.symbol_qualified_name || source.symbol_signature)

function sourceLocation(source: EvidenceSource): string {
  if (source.page_number !== null) return `Page ${source.page_number}`
  if (source.start_line !== null && source.end_line !== null) {
    return `L${source.start_line}–${source.end_line}`
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
        {{ isCodeSource(source) ? 'Code' : 'Document' }}
      </span>
      <div class="ev-src-id-row">
        <span class="ev-src-id">{{ source.source_id }}</span>
        <span class="ev-src-path">{{ source.relative_path || source.document_name }}</span>
      </div>
      <div class="ev-src-loc">
        {{ source.section_title || source.document_name }} · {{ sourceLocation(source) }}
      </div>
      <div v-if="source.symbol_signature" class="ev-src-sig-line">
        <span class="ev-src-sig">{{ source.symbol_signature }}</span>
      </div>
      <div class="ev-src-excerpt">{{ source.content }}</div>
    </div>
  </div>
</template>
