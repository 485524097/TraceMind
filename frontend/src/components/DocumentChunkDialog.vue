<script setup lang="ts">
import { ElButton, ElDialog } from 'element-plus'
import { computed, ref, watch } from 'vue'

import { ApiError } from '@/services/api'
import { listDocumentChunks } from '@/services/documents'
import type { DocumentChunk, DocumentItem } from '@/types/document'

const props = defineProps<{
  modelValue: boolean
  knowledgeBaseId: string
  document: DocumentItem | null
}>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
const chunks = ref<DocumentChunk[]>([])
const total = ref(0)
const offset = ref(0)
const limit = 20
const loading = ref(false)
const errorMessage = ref('')
const canPrevious = computed(() => offset.value > 0)
const canNext = computed(() => offset.value + limit < total.value)

function citation(chunk: DocumentChunk): string {
  const parts: string[] = []
  if (chunk.section_title) parts.push(chunk.section_title)
  if (chunk.page_number !== null) parts.push(`第 ${chunk.page_number} 页`)
  if (chunk.start_line !== null && chunk.end_line !== null) {
    parts.push(
      chunk.start_line === chunk.end_line
        ? `第 ${chunk.start_line} 行`
        : `第 ${chunk.start_line}–${chunk.end_line} 行`,
    )
  }
  return parts.join('，') || '无页码或行号元数据'
}

function failedMessage(code: string | null, fallback: string | null): string {
  if (code === 'no_extractable_text') return '未提取到文本；扫描型 PDF 当前不支持 OCR'
  if (code === 'invalid_encoding') return '文本解析仅支持 UTF-8 编码'
  return fallback ?? '解析失败，暂无可用 Chunk'
}

async function loadChunks(newOffset = offset.value): Promise<void> {
  if (!props.document || loading.value) return
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await listDocumentChunks(
      props.knowledgeBaseId,
      props.document.id,
      props.document.latest_version.id,
      newOffset,
      limit,
    )
    chunks.value = response.items
    total.value = response.total
    offset.value = newOffset
    if (response.version.parse_status === 'failed' && response.total === 0) {
      errorMessage.value = failedMessage(
        response.version.parse_error_code,
        response.version.parse_error_message,
      )
    }
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      errorMessage.value = '文档版本不存在'
    } else {
      errorMessage.value = 'Chunk 加载失败，请稍后重试'
    }
  } finally {
    loading.value = false
  }
}

watch(
  () => props.modelValue,
  (visible) => {
    if (!visible) return
    offset.value = 0
    void loadChunks(0)
  },
  { immediate: true },
)
</script>

<template>
  <ElDialog
    :model-value="modelValue"
    :title="`Chunk 预览 · ${document?.name ?? ''}`"
    width="min(900px, 94vw)"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <p v-if="loading">正在加载 Chunk…</p>
    <p v-else-if="errorMessage" class="form-error">{{ errorMessage }}</p>
    <p v-else-if="chunks.length === 0">当前版本暂无 Chunk。</p>
    <ol v-else class="chunk-list" :start="offset + 1">
      <li v-for="chunk in chunks" :key="chunk.id">
        <header>
          <strong>Chunk {{ chunk.chunk_index }}</strong>
          <span>{{ citation(chunk) }}</span>
          <span>{{ chunk.char_count }} 字符 · {{ chunk.language ?? chunk.chunk_type }}</span>
        </header>
        <pre>{{ chunk.content }}</pre>
      </li>
    </ol>
    <footer class="chunk-pagination">
      <span>共 {{ total }} 个 Chunk</span>
      <div>
        <ElButton :disabled="!canPrevious || loading" @click="loadChunks(Math.max(0, offset - limit))">上一页</ElButton>
        <ElButton :disabled="!canNext || loading" @click="loadChunks(offset + limit)">下一页</ElButton>
      </div>
    </footer>
  </ElDialog>
</template>
