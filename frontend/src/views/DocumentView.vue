<script setup lang="ts">
import { ElAlert, ElButton, ElEmpty, ElMessage, ElMessageBox } from 'element-plus'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import DocumentUploadPanel from '@/components/DocumentUploadPanel.vue'
import DocumentChunkDialog from '@/components/DocumentChunkDialog.vue'
import DocumentVersionDialog from '@/components/DocumentVersionDialog.vue'
import RagAnswerPanel from '@/components/RagAnswerPanel.vue'
import SemanticSearchPanel from '@/components/SemanticSearchPanel.vue'
import { ApiError } from '@/services/api'
import {
  deleteDocument,
  downloadCurrentDocument,
  listDocuments,
  requestDocumentParse,
  requestDocumentIndex,
} from '@/services/documents'
import { getKnowledgeBase } from '@/services/knowledgeBases'
import type { DocumentItem } from '@/types/document'

const route = useRoute()
const knowledgeBaseId = String(route.params.knowledgeBaseId)
const knowledgeBaseName = ref('')
const items = ref<DocumentItem[]>([])
const query = ref('')
const loading = ref(false)
const errorMessage = ref('')
const deletingId = ref<string | null>(null)
const versionDialogVisible = ref(false)
const selectedDocument = ref<DocumentItem | null>(null)
const chunkDialogVisible = ref(false)
const parsingId = ref<string | null>(null)
const indexingId = ref<string | null>(null)
let pollingTimer: ReturnType<typeof setInterval> | undefined

const parseLabels = {
  pending: '等待解析',
  processing: '解析中',
  succeeded: '解析完成',
  failed: '解析失败',
} as const

const indexLabels = {
  pending: '等待索引',
  processing: '索引中',
  succeeded: '索引完成',
  failed: '索引失败',
} as const

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(
    new Date(value),
  )
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

async function loadDocuments(): Promise<void> {
  if (loading.value) return
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await listDocuments(knowledgeBaseId, query.value)
    items.value = response.items
    updatePolling()
  } catch {
    errorMessage.value = '文档列表加载失败，请检查知识库或后端服务后重试'
  } finally {
    loading.value = false
  }
}

function updatePolling(): void {
  const needsPolling = items.value.some(({ latest_version }) =>
    ['pending', 'processing'].includes(latest_version.parse_status) ||
    (latest_version.parse_status === 'succeeded' &&
      ['pending', 'processing'].includes(latest_version.index_status)),
  )
  if (needsPolling && pollingTimer === undefined) {
    pollingTimer = setInterval(() => void loadDocuments(), 2500)
  } else if (!needsPolling && pollingTimer !== undefined) {
    clearInterval(pollingTimer)
    pollingTimer = undefined
  }
}

async function requestIndex(document: DocumentItem, force: boolean): Promise<void> {
  if (indexingId.value) return
  indexingId.value = document.id
  try {
    const result = await requestDocumentIndex(
      knowledgeBaseId,
      document.id,
      document.latest_version.id,
      force,
    )
    ElMessage.success(result.queued ? '已进入索引队列' : '当前状态无需重复入队')
    await loadDocuments()
  } catch {
    ElMessage.error('索引请求失败，请确认文档已解析完成')
  } finally {
    indexingId.value = null
  }
}

function showChunks(document: DocumentItem): void {
  selectedDocument.value = document
  chunkDialogVisible.value = true
}

function parseErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 503) {
    return '解析队列暂时不可用，请稍后重试'
  }
  return '解析请求失败，请稍后重试'
}

function parseErrorSummary(document: DocumentItem): string {
  const version = document.latest_version
  if (version.parse_error_code === 'no_extractable_text') {
    return '未提取到文本；扫描型 PDF 当前不支持 OCR'
  }
  if (version.parse_error_code === 'invalid_encoding') {
    return '文本解析仅支持 UTF-8 编码'
  }
  return version.parse_error_message ?? ''
}

async function requestParse(document: DocumentItem, force: boolean): Promise<void> {
  if (parsingId.value) return
  parsingId.value = document.id
  try {
    const result = await requestDocumentParse(
      knowledgeBaseId,
      document.id,
      document.latest_version.id,
      force,
    )
    ElMessage.success(result.queued ? '已进入解析队列' : '当前状态无需重复入队')
    await loadDocuments()
  } catch (error) {
    ElMessage.error(parseErrorMessage(error))
  } finally {
    parsingId.value = null
  }
}

async function loadPage(): Promise<void> {
  try {
    knowledgeBaseName.value = (await getKnowledgeBase(knowledgeBaseId)).name
  } catch {
    errorMessage.value = '知识库不存在或加载失败'
  }
  await loadDocuments()
}

function showVersions(document: DocumentItem): void {
  selectedDocument.value = document
  versionDialogVisible.value = true
}

async function confirmDelete(document: DocumentItem): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除文档“${document.name}”及全部历史版本吗？此操作无法撤销。`,
      '删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  if (deletingId.value) return
  deletingId.value = document.id
  try {
    await deleteDocument(knowledgeBaseId, document.id)
    ElMessage.success('文档删除成功')
    await loadDocuments()
  } catch {
    ElMessage.error('文档删除失败，请稍后重试')
  } finally {
    deletingId.value = null
  }
}

onMounted(loadPage)
onBeforeUnmount(() => {
  if (pollingTimer !== undefined) clearInterval(pollingTimer)
})
</script>

<template>
  <main class="management-page document-page">
    <header class="management-header">
      <div>
        <RouterLink to="/knowledge-bases" class="back-link">← 返回知识库列表</RouterLink>
        <p class="eyebrow">DOCUMENT INGESTION</p>
        <h1>{{ knowledgeBaseName || '文档管理' }}</h1>
        <p>管理原始文件、解析状态与历史版本；解析完成仍不代表已建立检索索引。</p>
      </div>
      <ElButton :loading="loading" @click="loadDocuments">刷新</ElButton>
    </header>

    <DocumentUploadPanel :knowledge-base-id="knowledgeBaseId" @completed="loadDocuments" />
    <RagAnswerPanel :knowledge-base-id="knowledgeBaseId" />
    <p class="panel-section-label">Dense 检索调试</p>
    <SemanticSearchPanel :knowledge-base-id="knowledgeBaseId" />

    <section class="document-toolbar">
      <form @submit.prevent="loadDocuments">
        <input v-model="query" aria-label="文档名称搜索" placeholder="按名称搜索文档" />
        <ElButton native-type="submit" :loading="loading">搜索</ElButton>
      </form>
    </section>

    <ElAlert v-if="errorMessage" :title="errorMessage" type="error" show-icon :closable="false" />

    <section class="knowledge-panel" :aria-busy="loading">
      <div v-if="loading && items.length === 0" class="loading-state">正在加载文档…</div>
      <ElEmpty v-else-if="items.length === 0 && !errorMessage" description="暂无文档" />
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr><th>文件名</th><th>版本</th><th>大小</th><th>解析状态</th><th>索引状态</th><th>Chunk</th><th>最近解析</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="document in items" :key="document.id">
              <td class="name-cell">{{ document.name }}</td>
              <td>{{ document.latest_version.extension }} · V{{ document.latest_version.version_number }}</td>
              <td>{{ formatSize(document.latest_version.file_size) }}</td>
              <td>
                <span :data-status="document.latest_version.parse_status">{{ parseLabels[document.latest_version.parse_status] }}</span>
                <small v-if="document.latest_version.parse_error_message" class="parse-error-summary">{{ parseErrorSummary(document) }}</small>
              </td>
              <td>
                <span :data-index-status="document.latest_version.index_status">{{ indexLabels[document.latest_version.index_status] }}</span>
                <small v-if="document.latest_version.index_error_message" class="parse-error-summary">{{ document.latest_version.index_error_message }}</small>
              </td>
              <td>{{ document.latest_version.chunk_count }}</td>
              <td>{{ document.latest_version.parsed_at ? formatDate(document.latest_version.parsed_at) : '—' }}</td>
              <td class="row-actions document-actions">
                <ElButton size="small" :disabled="document.latest_version.chunk_count === 0" @click="showChunks(document)">Chunk</ElButton>
                <ElButton
                  size="small"
                  :loading="parsingId === document.id"
                  :disabled="parsingId !== null || document.latest_version.parse_status === 'processing'"
                  @click="requestParse(document, document.latest_version.parse_status === 'succeeded')"
                >{{ document.latest_version.parse_status === 'succeeded' ? '重新解析' : '重试解析' }}</ElButton>
                <ElButton
                  size="small"
                  :loading="indexingId === document.id"
                  :disabled="indexingId !== null || document.latest_version.parse_status !== 'succeeded' || document.latest_version.index_status === 'processing'"
                  @click="requestIndex(document, document.latest_version.index_status === 'succeeded')"
                >{{ document.latest_version.index_status === 'succeeded' ? '重新索引' : '索引' }}</ElButton>
                <ElButton size="small" @click="downloadCurrentDocument(knowledgeBaseId, document.id)">下载</ElButton>
                <ElButton size="small" @click="showVersions(document)">版本</ElButton>
                <ElButton
                  :data-testid="`delete-document-${document.id}`"
                  size="small"
                  type="danger"
                  plain
                  :loading="deletingId === document.id"
                  :disabled="deletingId !== null"
                  @click="confirmDelete(document)"
                >删除</ElButton>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <DocumentVersionDialog
      v-model="versionDialogVisible"
      :knowledge-base-id="knowledgeBaseId"
      :document="selectedDocument"
    />
    <DocumentChunkDialog
      v-model="chunkDialogVisible"
      :knowledge-base-id="knowledgeBaseId"
      :document="selectedDocument"
    />
  </main>
</template>
