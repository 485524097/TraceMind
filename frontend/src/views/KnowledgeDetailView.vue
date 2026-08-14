<script setup lang="ts">
import { inject, onMounted, onUnmounted, ref, type Ref } from 'vue'
import { ElButton, ElMessage, ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import EvidenceSourceList from '@/components/EvidenceSourceList.vue'
import KnowledgeEntryFormDialog from '@/components/KnowledgeEntryFormDialog.vue'
import MarkdownContent from '@/components/MarkdownContent.vue'
import { getKnowledgeBase } from '@/services/knowledgeBases'
import {
  deleteKnowledgeEntry,
  getKnowledgeEntry,
  requestKnowledgeEntryIndex,
  updateKnowledgeEntry,
} from '@/services/knowledgeEntries'
import type { KnowledgeEntry, KnowledgeEntryInput } from '@/types/knowledgeEntry'

const route = useRoute()
const router = useRouter()
const knowledgeBaseId = String(route.params.knowledgeBaseId)
const entryId = String(route.params.entryId)
const shellKbName = inject<Ref<string>>('shellKbName', ref(''))
const entry = ref<KnowledgeEntry | null>(null)
const error = ref('')
const editVisible = ref(false)
const submitting = ref(false)
const statusLabels = { unverified: '未验证', verified: '已验证', outdated: '已过期' } as const
const indexLabels = {
  not_indexed: '未进入检索',
  pending: '等待索引',
  processing: '正在索引',
  succeeded: '可被问答检索',
  failed: '索引失败',
} as const
let indexPollTimer: number | undefined

function scheduleIndexPoll(): void {
  window.clearTimeout(indexPollTimer)
  if (entry.value && ['pending', 'processing'].includes(entry.value.index_status)) {
    indexPollTimer = window.setTimeout(() => void load(), 2_000)
  }
}

async function load(): Promise<void> {
  try {
    entry.value = await getKnowledgeEntry(knowledgeBaseId, entryId)
    scheduleIndexPoll()
  } catch {
    error.value = '知识详情加载失败，请稍后重试'
  }
}

function editValue(): KnowledgeEntryInput {
  const value = entry.value
  return {
    question: value?.question ?? '',
    background: value?.background ?? null,
    root_cause: value?.root_cause ?? null,
    solution: value?.solution ?? '',
    failed_attempts: value?.failed_attempts ?? [],
    validation_status: value?.validation_status ?? 'unverified',
    tags: value?.tags ?? [],
  }
}

async function save(value: KnowledgeEntryInput): Promise<void> {
  submitting.value = true
  try {
    entry.value = await updateKnowledgeEntry(knowledgeBaseId, entryId, value)
    scheduleIndexPoll()
    editVisible.value = false
    ElMessage.success('知识已更新')
  } catch {
    ElMessage.error('知识更新失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}

async function remove(): Promise<void> {
  try {
    await ElMessageBox.confirm('确定删除这条知识吗？', '删除知识', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await deleteKnowledgeEntry(knowledgeBaseId, entryId)
    await router.push(`/knowledge-bases/${knowledgeBaseId}/knowledge`)
  } catch {
    ElMessage.error('知识删除失败，请稍后重试')
  }
}

async function retryIndex(): Promise<void> {
  if (!entry.value) return
  try {
    entry.value = await requestKnowledgeEntryIndex(knowledgeBaseId, entryId, true)
    scheduleIndexPoll()
    ElMessage.success('已重新提交知识索引')
  } catch {
    ElMessage.error('知识索引提交失败，请确认 Worker 状态后重试')
  }
}

onMounted(async () => {
  try {
    shellKbName.value = (await getKnowledgeBase(knowledgeBaseId)).name
  } catch {
    error.value = '知识库不存在或加载失败'
  }
  await load()
})

onUnmounted(() => window.clearTimeout(indexPollTimer))
</script>

<template>
  <main class="knowledge-detail-page">
    <RouterLink :to="`/knowledge-bases/${knowledgeBaseId}/knowledge`" class="back-link">
      ← 返回知识列表
    </RouterLink>
    <div v-if="error" class="conv-error" role="alert">{{ error }}</div>
    <template v-else-if="entry">
      <header class="knowledge-detail-head">
        <div>
          <span class="knowledge-status" :data-status="entry.validation_status">
            {{ statusLabels[entry.validation_status] }}
          </span>
          <span class="knowledge-index-status" :data-status="entry.index_status">
            {{ indexLabels[entry.index_status] }}
          </span>
          <h1>{{ entry.question }}</h1>
          <div class="knowledge-row-tags">
            <span v-for="item in entry.tags" :key="item">{{ item }}</span>
          </div>
        </div>
        <div class="knowledge-detail-actions">
          <ElButton
            v-if="entry.validation_status === 'verified' && entry.index_status === 'failed'"
            @click="retryIndex"
          >
            重试索引
          </ElButton>
          <ElButton @click="editVisible = true">编辑</ElButton>
          <ElButton type="danger" plain @click="remove">删除</ElButton>
        </div>
      </header>

      <div class="knowledge-detail-layout">
        <article class="knowledge-article">
          <section v-if="entry.background">
            <h2>背景</h2>
            <p>{{ entry.background }}</p>
          </section>
          <section v-if="entry.root_cause">
            <h2>根因</h2>
            <p>{{ entry.root_cause }}</p>
          </section>
          <section>
            <h2>解决方案</h2>
            <MarkdownContent class="knowledge-prose" :content="entry.solution" />
          </section>
          <section v-if="entry.failed_attempts.length">
            <h2>失败尝试</h2>
            <ul>
              <li v-for="attempt in entry.failed_attempts" :key="attempt">{{ attempt }}</li>
            </ul>
          </section>
          <section class="knowledge-origin">
            <h2>原始回答快照</h2>
            <p class="knowledge-question-snapshot">{{ entry.question_snapshot }}</p>
            <MarkdownContent class="knowledge-prose" :content="entry.answer_snapshot" />
            <RouterLink
              v-if="entry.source_conversation_id"
              :to="`/knowledge-bases/${knowledgeBaseId}/chat?conversation=${entry.source_conversation_id}`"
              class="text-action"
            >
              打开原会话 →
            </RouterLink>
            <span v-else class="muted-text">原会话已不可用，快照仍保留。</span>
          </section>
        </article>

        <aside class="knowledge-evidence">
          <h2>证据</h2>
          <EvidenceSourceList
            v-if="entry.sources_snapshot.length"
            :sources="entry.sources_snapshot"
            :identity-prefix="entry.id"
          />
          <p v-else class="muted-text">这条回答没有保存引用证据。</p>
        </aside>
      </div>

      <KnowledgeEntryFormDialog
        v-model="editVisible"
        title="编辑知识"
        :initial-value="editValue()"
        :submitting="submitting"
        @submit="save"
      />
    </template>
  </main>
</template>
