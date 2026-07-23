<script setup lang="ts">
import { ElButton, ElDialog, ElMessage } from 'element-plus'
import { ref, watch } from 'vue'

import {
  downloadDocumentVersion,
  listDocumentVersions,
  requestDocumentIndex,
} from '@/services/documents'
import type { DocumentItem, DocumentVersion } from '@/types/document'

const props = defineProps<{
  modelValue: boolean
  knowledgeBaseId: string
  document: DocumentItem | null
}>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
const versions = ref<DocumentVersion[]>([])
const loading = ref(false)
const errorMessage = ref('')
const indexingId = ref<string | null>(null)
const indexLabels = {
  pending: '等待索引',
  processing: '索引中',
  succeeded: '索引完成',
  failed: '索引失败',
} as const

async function indexVersion(version: DocumentVersion): Promise<void> {
  if (!props.document || indexingId.value) return
  indexingId.value = version.id
  try {
    await requestDocumentIndex(
      props.knowledgeBaseId,
      props.document.id,
      version.id,
      version.index_status === 'succeeded',
    )
    ElMessage.success('已提交索引任务')
    versions.value = await listDocumentVersions(props.knowledgeBaseId, props.document.id)
  } catch {
    ElMessage.error('索引请求失败，请确认该版本已解析完成')
  } finally {
    indexingId.value = null
  }
}

watch(
  () => props.modelValue,
  async (visible) => {
    if (!visible || !props.document) return
    loading.value = true
    errorMessage.value = ''
    try {
      versions.value = await listDocumentVersions(props.knowledgeBaseId, props.document.id)
    } catch {
      errorMessage.value = '版本历史加载失败，请稍后重试'
    } finally {
      loading.value = false
    }
  },
)
</script>

<template>
  <ElDialog
    :model-value="modelValue"
    :title="`版本历史 · ${document?.name ?? ''}`"
    width="min(680px, 92vw)"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <p v-if="loading">正在加载版本历史…</p>
    <p v-else-if="errorMessage" class="form-error">{{ errorMessage }}</p>
    <table v-else>
      <thead><tr><th>版本</th><th>大小</th><th>索引状态</th><th>时间</th><th></th></tr></thead>
      <tbody>
        <tr v-for="version in versions" :key="version.id">
          <td>Version {{ version.version_number }}</td>
          <td>{{ version.file_size }} B</td>
          <td>{{ indexLabels[version.index_status] }}</td>
          <td>{{ new Date(version.created_at).toLocaleString('zh-CN') }}</td>
          <td class="row-actions">
            <ElButton
              size="small"
              :loading="indexingId === version.id"
              :disabled="version.parse_status !== 'succeeded' || version.index_status === 'processing'"
              @click="indexVersion(version)"
            >{{ version.index_status === 'succeeded' ? '重新索引' : '索引' }}</ElButton>
            <ElButton
              size="small"
              @click="downloadDocumentVersion(knowledgeBaseId, document!.id, version.id)"
            >下载</ElButton>
          </td>
        </tr>
      </tbody>
    </table>
  </ElDialog>
</template>
