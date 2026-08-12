<script setup lang="ts">
import {
  ElAlert,
  ElButton,
  ElDropdown,
  ElDropdownItem,
  ElDropdownMenu,
  ElEmpty,
  ElMessage,
  ElMessageBox,
} from 'element-plus'
import { inject, nextTick, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { useRoute } from 'vue-router'

import DocumentUploadPanel from '@/components/DocumentUploadPanel.vue'
import DocumentChunkDialog from '@/components/DocumentChunkDialog.vue'
import DocumentVersionDialog from '@/components/DocumentVersionDialog.vue'
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

const shellKbName = inject<Ref<string>>('shellKbName', ref(''))
watch(knowledgeBaseName, (name) => {
  shellKbName.value = name || ''
})
const items = ref<DocumentItem[]>([])
const query = ref('')
const focusedDocumentId = ref('')
const loading = ref(false)
const errorMessage = ref('')
const deletingId = ref<string | null>(null)
const versionDialogVisible = ref(false)
const selectedDocument = ref<DocumentItem | null>(null)
const chunkDialogVisible = ref(false)
const parsingId = ref<string | null>(null)
const indexingId = ref<string | null>(null)
const showUpload = ref(false)
const showRetrievalDebug = ref(false)
let pollingTimer: ReturnType<typeof setInterval> | undefined

function elapsedLabel(value: string | null): string {
  if (!value) return ''
  const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000))
  return ` · ${seconds}s`
}

function processingSummary(document: DocumentItem): string {
  const version = document.latest_version
  if (version.parse_status === 'failed') {
    return `处理失败 · ${parseErrorSummary(document)}`
  }
  if (version.parse_status === 'pending') return '等待处理'
  if (version.parse_status === 'processing') {
    return `解析中${elapsedLabel(version.parse_started_at)}`
  }
  if (version.index_status === 'failed') {
    return `处理失败 · ${version.index_error_message || '索引失败，请重试'}`
  }
  if (version.index_status === 'pending') {
    return `已解析 · ${version.chunk_count} 个 Chunk · 等待建立索引`
  }
  if (version.index_status === 'processing') {
    return `正在建立索引${elapsedLabel(version.index_started_at)}`
  }
  return 'Ready'
}

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
    await focusRequestedDocument()
  } catch {
    errorMessage.value = '文档列表加载失败，请检查知识库或后端服务后重试'
  } finally {
    loading.value = false
  }
}

async function focusRequestedDocument(): Promise<void> {
  if (!focusedDocumentId.value) return
  await nextTick()
  document.getElementById(`document-${focusedDocumentId.value}`)?.scrollIntoView?.({
    block: 'center',
  })
}

function updatePolling(): void {
  const needsPolling = items.value.some(
    ({ latest_version }) =>
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
  query.value = typeof route.query?.query === 'string' ? route.query.query : ''
  focusedDocumentId.value =
    typeof route.query?.focusDocument === 'string' ? route.query.focusDocument : ''
  try {
    knowledgeBaseName.value = (await getKnowledgeBase(knowledgeBaseId)).name
  } catch {
    errorMessage.value = '知识库不存在或加载失败'
  }
  await loadDocuments()
}

async function handleUploadCompleted(): Promise<void> {
  await loadDocuments()
  showUpload.value = false
}

function showVersions(document: DocumentItem): void {
  selectedDocument.value = document
  versionDialogVisible.value = true
}

async function confirmDelete(document: DocumentItem): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除文档"${document.name}"及全部历史版本吗？此操作无法撤销。`,
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

function statusPillClass(status: string): string {
  if (status === 'succeeded') return 'st-pill st-pill-ok'
  if (status === 'failed') return 'st-pill st-pill-err'
  return 'st-pill st-pill-warn'
}

function fileNameOnly(document: DocumentItem): string {
  const name = document.relative_path || document.name
  const lastSlash = Math.max(name.lastIndexOf('/'), name.lastIndexOf('\\'))
  return lastSlash >= 0 ? name.slice(lastSlash + 1) : name
}

function fileExtension(document: DocumentItem): string {
  const name = document.relative_path || document.name
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot) : ''
}

function baseNameWithoutExt(document: DocumentItem): string {
  const full = fileNameOnly(document)
  const dot = full.lastIndexOf('.')
  return dot >= 0 ? full.slice(0, dot) : full
}

onMounted(loadPage)
onBeforeUnmount(() => {
  if (pollingTimer !== undefined) clearInterval(pollingTimer)
})
</script>

<template>
  <main class="management-page document-page">
    <!-- Page Header -->
    <header class="management-header">
      <div>
        <h1>文档</h1>
        <p>可用于检索、问答与可追溯引用的文档和代码资料</p>
      </div>
      <div class="header-actions">
        <ElButton type="primary" @click="showUpload = !showUpload">
          {{ showUpload ? '收起' : '导入文件' }}
        </ElButton>
      </div>
    </header>

    <!-- Upload Panel (collapsible) -->
    <DocumentUploadPanel
      v-if="showUpload"
      :knowledge-base-id="knowledgeBaseId"
      @completed="handleUploadCompleted"
    />

    <!-- Search -->
    <div class="doc-search-bar">
      <input
        v-model="query"
        aria-label="按名称或路径筛选文档"
        placeholder="按名称或路径筛选…"
        @keyup.enter="loadDocuments"
      />
      <ElButton :loading="loading" @click="loadDocuments" size="small">搜索</ElButton>
    </div>

    <ElAlert
      v-if="errorMessage"
      :title="errorMessage"
      type="error"
      show-icon
      :closable="false"
      style="max-width: 1160px; margin: 0 auto var(--space-lg)"
    />

    <!-- Document List -->
    <section :aria-busy="loading">
      <div v-if="loading && items.length === 0" class="loading-state">正在加载文档…</div>
      <ElEmpty v-else-if="items.length === 0 && !errorMessage" description="暂无文档" />

      <div v-else class="doc-list">
        <div
          v-for="document in items"
          :key="document.id"
          :id="`document-${document.id}`"
          class="doc-item"
          :class="{ 'doc-item-focused': document.id === focusedDocumentId }"
        >
          <div class="doc-main">
            <div class="doc-name-row">
              <span class="doc-name">{{ baseNameWithoutExt(document) }}</span>
              <span class="doc-ext">{{ fileExtension(document) }}</span>
            </div>
            <div class="doc-path">{{ document.relative_path || document.name }}</div>
            <div class="doc-meta-row">
              <span class="doc-meta">V{{ document.latest_version.version_number }}</span>
              <span class="doc-meta-sep">·</span>
              <span class="doc-meta">{{ formatSize(document.latest_version.file_size) }}</span>
              <span class="doc-meta-sep">·</span>
              <span class="doc-meta">{{ document.latest_version.chunk_count }} 个 Chunk</span>
              <span class="doc-meta-sep">·</span>
              <span class="doc-meta">{{
                document.latest_version.parsed_at
                  ? formatDate(document.latest_version.parsed_at)
                  : '—'
              }}</span>
              <span class="doc-statuses">
                <span
                  :class="
                    statusPillClass(
                      document.latest_version.parse_status === 'failed' ||
                        document.latest_version.index_status === 'failed'
                        ? 'failed'
                        : document.latest_version.parse_status === 'succeeded' &&
                            document.latest_version.index_status === 'succeeded'
                          ? 'succeeded'
                          : 'processing',
                    )
                  "
                  >{{ processingSummary(document) }}</span
                >
              </span>
            </div>
          </div>

          <!-- Overflow actions -->
          <ElDropdown trigger="click" :hide-on-click="true">
            <button class="doc-more" aria-label="文档操作">···</button>
            <template #dropdown>
              <ElDropdownMenu>
                <ElDropdownItem
                  :disabled="document.latest_version.chunk_count === 0"
                  @click="showChunks(document)"
                  >查看 Chunk</ElDropdownItem
                >
                <ElDropdownItem
                  :disabled="
                    parsingId !== null || document.latest_version.parse_status === 'processing'
                  "
                  @click="
                    requestParse(document, document.latest_version.parse_status === 'succeeded')
                  "
                  >{{
                    document.latest_version.parse_status === 'succeeded' ? '重新解析' : '重试解析'
                  }}</ElDropdownItem
                >
                <ElDropdownItem
                  :disabled="
                    indexingId !== null ||
                    document.latest_version.parse_status !== 'succeeded' ||
                    document.latest_version.index_status === 'processing'
                  "
                  @click="
                    requestIndex(document, document.latest_version.index_status === 'succeeded')
                  "
                  >{{
                    document.latest_version.index_status === 'succeeded' ? '重建索引' : '建立索引'
                  }}</ElDropdownItem
                >
                <ElDropdownItem @click="downloadCurrentDocument(knowledgeBaseId, document.id)"
                  >下载</ElDropdownItem
                >
                <ElDropdownItem @click="showVersions(document)">历史版本</ElDropdownItem>
                <ElDropdownItem
                  :data-testid="`delete-document-${document.id}`"
                  divided
                  style="color: var(--color-error)"
                  @click="confirmDelete(document)"
                  >删除</ElDropdownItem
                >
              </ElDropdownMenu>
            </template>
          </ElDropdown>
        </div>
      </div>
    </section>

    <!-- Retrieval Tools -->
    <div
      style="
        max-width: 1160px;
        margin: var(--space-2xl) auto 0;
        padding-top: var(--space-lg);
        border-top: 1px solid var(--color-border-light);
      "
    >
      <ElButton size="small" text @click="showRetrievalDebug = !showRetrievalDebug">
        {{ showRetrievalDebug ? '▾' : '▸' }} 检索调试
      </ElButton>
      <SemanticSearchPanel v-if="showRetrievalDebug" :knowledge-base-id="knowledgeBaseId" />
    </div>

    <!-- Dialogs -->
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
