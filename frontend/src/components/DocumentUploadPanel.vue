<script setup lang="ts">
import { ElButton } from 'element-plus'
import { computed, ref } from 'vue'

import { ApiError } from '@/services/api'
import { uploadDocument } from '@/services/documents'

type UploadState =
  | 'waiting'
  | 'uploading'
  | 'created'
  | 'version_created'
  | 'unchanged'
  | 'failed'

interface UploadEntry {
  key: string
  file: File
  state: UploadState
  message: string
}

const props = defineProps<{ knowledgeBaseId: string }>()
const emit = defineEmits<{ completed: [] }>()
const entries = ref<UploadEntry[]>([])
const uploading = ref(false)
const hasWaiting = computed(() => entries.value.some((entry) => entry.state === 'waiting'))

const labels: Record<UploadState, string> = {
  waiting: '等待上传',
  uploading: '上传中',
  created: '新建成功',
  version_created: '新版本成功',
  unchanged: '内容未变化',
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
  let attempted = false
  try {
    for (const entry of entries.value) {
      if (entry.state !== 'waiting') continue
      attempted = true
      entry.state = 'uploading'
      try {
        const result = await uploadDocument(props.knowledgeBaseId, entry.file)
        entry.state = result.import_action
        entry.message = labels[result.import_action]
      } catch (error) {
        entry.state = 'failed'
        entry.message = errorMessage(error)
      }
    }
  } finally {
    uploading.value = false
    if (attempted) emit('completed')
  }
}
</script>

<template>
  <section class="upload-panel">
    <div>
      <h2>导入文件</h2>
      <p>文件会逐个上传；导入完成不代表已经解析或建立检索索引。</p>
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
    <ElButton
      data-testid="upload-documents"
      type="primary"
      :loading="uploading"
      :disabled="!hasWaiting || uploading"
      @click="uploadAll"
    >
      开始上传
    </ElButton>
    <ul v-if="entries.length" class="upload-list">
      <li v-for="entry in entries" :key="entry.key">
        <span>{{ entry.file.name }}</span>
        <span :data-state="entry.state">{{ entry.message || labels[entry.state] }}</span>
      </li>
    </ul>
  </section>
</template>
