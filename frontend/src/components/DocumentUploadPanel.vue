<script setup lang="ts">
import { ElButton } from 'element-plus'
import { computed, ref } from 'vue'

import { ApiError } from '@/services/api'
import {
  DIRECTORY_IMPORT_CONCURRENCY,
  planDirectoryImport,
} from '@/services/directoryImport'
import { uploadDocument } from '@/services/documents'

type UploadState =
  | 'waiting'
  | 'uploading'
  | 'created'
  | 'version_created'
  | 'unchanged'
  | 'ignored'
  | 'unsupported'
  | 'cancelled'
  | 'failed'

interface UploadEntry {
  key: string
  file: File
  relativePath?: string
  state: UploadState
  message: string
}

const props = defineProps<{ knowledgeBaseId: string }>()
const emit = defineEmits<{ completed: [] }>()
const entries = ref<UploadEntry[]>([])
const uploading = ref(false)
const cancelled = ref(false)
const controllers = new Set<AbortController>()
const hasWaiting = computed(() => entries.value.some((entry) => entry.state === 'waiting'))
const stats = computed(() => {
  const count = (states: UploadState[]) =>
    entries.value.filter((entry) => states.includes(entry.state)).length
  return {
    total: entries.value.length,
    completed: count(['created', 'version_created', 'unchanged', 'failed', 'cancelled']),
    success: count(['created']),
    updated: count(['version_created']),
    skipped: count(['unchanged', 'ignored', 'unsupported', 'cancelled']),
    failed: count(['failed']),
    ready: count(['waiting']),
    ignored: count(['ignored']),
    unsupported: count(['unsupported']),
  }
})

const labels: Record<UploadState, string> = {
  waiting: '等待上传',
  uploading: '上传中',
  created: '新建成功',
  version_created: '新版本成功',
  unchanged: '内容未变化',
  ignored: '已忽略',
  unsupported: '不支持',
  cancelled: '已取消',
  failed: '上传失败',
}

function selectFiles(event: Event): void {
  const input = event.target as HTMLInputElement
  for (const file of Array.from(input.files ?? [])) {
    const key = `${file.name}:${file.size}:${file.lastModified}`
    if (!entries.value.some((entry) => entry.key === key && ['waiting', 'uploading'].includes(entry.state))) {
      entries.value.push({ key, file, state: 'waiting', message: '' })
    }
  }
  input.value = ''
}

function selectDirectory(event: Event): void {
  const input = event.target as HTMLInputElement
  const planned = planDirectoryImport(Array.from(input.files ?? []))
  for (const item of planned) {
    const state: UploadState =
      item.disposition === 'ready' ? 'waiting' : item.disposition
    const key = `${item.relativePath}:${item.file.size}:${item.file.lastModified}`
    entries.value.push({
      key,
      file: item.file,
      relativePath: item.relativePath,
      state,
      message: state === 'waiting' ? '准备导入' : labels[state],
    })
  }
  input.value = ''
}

function errorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return '上传失败，请稍后重试'
  if (error.status === 413) return '文件超过大小限制'
  if (error.status === 415) return '不支持该文件类型'
  if (error.status === 422) return '文件名或文件内容无效'
  if (error.status === 404) return '知识库不存在'
  if (error.status === 409) return '导入冲突，请重试'
  return '上传或存储失败，请稍后重试'
}

async function uploadAll(): Promise<void> {
  if (uploading.value) return
  uploading.value = true
  cancelled.value = false
  let attempted = false
  try {
    const queue = entries.value.filter((entry) => entry.state === 'waiting')
    let cursor = 0
    async function worker(): Promise<void> {
      while (!cancelled.value) {
        const entry = queue[cursor]
        cursor += 1
        if (!entry) return
        attempted = true
        entry.state = 'uploading'
        const controller = new AbortController()
        controllers.add(controller)
        try {
          const result = await uploadDocument(
            props.knowledgeBaseId,
            entry.file,
            entry.relativePath,
            controller.signal,
          )
          entry.state = result.import_action
          entry.message = result.parsing_queued
            ? `${labels[result.import_action]}，已进入解析队列`
            : `${labels[result.import_action]}，等待手动解析`
        } catch (error) {
          if (controller.signal.aborted) {
            entry.state = 'cancelled'
            entry.message = labels.cancelled
          } else {
            entry.state = 'failed'
            entry.message = errorMessage(error)
          }
        } finally {
          controllers.delete(controller)
        }
      }
    }
    await Promise.all(
      Array.from(
        { length: Math.min(DIRECTORY_IMPORT_CONCURRENCY, queue.length) },
        () => worker(),
      ),
    )
    if (cancelled.value) {
      for (const entry of entries.value) {
        if (entry.state === 'waiting') {
          entry.state = 'cancelled'
          entry.message = labels.cancelled
        }
      }
    }
  } finally {
    uploading.value = false
    if (attempted) emit('completed')
  }
}

function cancelUpload(): void {
  cancelled.value = true
  for (const controller of controllers) controller.abort()
}
</script>

<template>
  <section class="upload-panel">
    <div>
      <h2>导入文件</h2>
      <p>文件使用有限并发上传；导入完成不代表已经解析或建立检索索引。</p>
    </div>
    <label class="file-picker">
      选择文件
      <input
        data-testid="document-files"
        type="file"
        multiple
        accept=".md,.txt,.pdf,.docx,.java,.jsp,.js,.ts,.vue,.sql,.xml,.json,.yaml,.yml,.properties,.py"
        :disabled="uploading"
        @change="selectFiles"
      />
    </label>
    <label class="file-picker">
      导入代码目录
      <input
        data-testid="document-directory"
        type="file"
        multiple
        webkitdirectory
        :disabled="uploading"
        @change="selectDirectory"
      />
    </label>
    <ElButton
      data-testid="upload-documents"
      type="primary"
      :loading="uploading"
      :disabled="!hasWaiting || uploading"
      @click="uploadAll"
    >
      开始上传
    </ElButton>
    <ElButton
      v-if="uploading"
      data-testid="cancel-document-upload"
      type="danger"
      plain
      @click="cancelUpload"
    >
      取消导入
    </ElButton>
    <p v-if="entries.length" class="upload-summary" data-testid="directory-import-summary">
      总数 {{ stats.total }} · 完成 {{ stats.completed }} · 成功 {{ stats.success }} ·
      更新 {{ stats.updated }} · 跳过 {{ stats.skipped }} · 失败 {{ stats.failed }}
      <template v-if="stats.ready || stats.ignored || stats.unsupported">
        · 准备 {{ stats.ready }} · 忽略 {{ stats.ignored }} · 不支持 {{ stats.unsupported }}
      </template>
    </p>
    <ul v-if="entries.length" class="upload-list">
      <li v-for="entry in entries" :key="entry.key">
        <span>{{ entry.relativePath || entry.file.name }}</span>
        <span :data-state="entry.state">{{ entry.message || labels[entry.state] }}</span>
      </li>
    </ul>
  </section>
</template>
