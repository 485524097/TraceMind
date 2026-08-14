<script setup lang="ts">
import { inject, onMounted, ref, watch, type Ref } from 'vue'
import { ElEmpty, ElOption, ElSelect } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import { getKnowledgeBase } from '@/services/knowledgeBases'
import { listKnowledgeEntries } from '@/services/knowledgeEntries'
import type { KnowledgeEntry, ValidationStatus } from '@/types/knowledgeEntry'

const route = useRoute()
const router = useRouter()
const knowledgeBaseId = String(route.params.knowledgeBaseId)
const shellKbName = inject<Ref<string>>('shellKbName', ref(''))
const entries = ref<KnowledgeEntry[]>([])
const availableTags = ref<string[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref('')
const query = ref('')
const validationStatus = ref<ValidationStatus | ''>('')
const tag = ref('')
const statusLabels: Record<ValidationStatus, string> = {
  unverified: '未验证',
  verified: '已验证',
  outdated: '已过期',
}
const indexLabels = {
  not_indexed: '未进入检索',
  pending: '等待索引',
  processing: '正在索引',
  succeeded: '可检索',
  failed: '索引失败',
} as const

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const result = await listKnowledgeEntries(knowledgeBaseId, {
      query: query.value,
      validationStatus: validationStatus.value,
      tag: tag.value,
    })
    entries.value = result.items
    total.value = result.total
    availableTags.value = result.available_tags
  } catch {
    error.value = '知识列表加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

function openEntry(entry: KnowledgeEntry): void {
  void router.push(`/knowledge-bases/${knowledgeBaseId}/knowledge/${entry.id}`)
}

let filterTimer: number | undefined
watch(query, () => {
  window.clearTimeout(filterTimer)
  filterTimer = window.setTimeout(() => void load(), 250)
})
watch([validationStatus, tag], () => void load())

onMounted(async () => {
  try {
    shellKbName.value = (await getKnowledgeBase(knowledgeBaseId)).name
  } catch {
    error.value = '知识库不存在或加载失败'
  }
  await load()
})
</script>

<template>
  <main class="knowledge-page">
    <header class="page-header">
      <div>
        <h1>知识</h1>
        <p>从会话中沉淀的问题、根因、解决方案与证据。</p>
      </div>
      <RouterLink :to="`/knowledge-bases/${knowledgeBaseId}/chat`" class="text-action">
        去提问 →
      </RouterLink>
    </header>

    <div class="knowledge-filters">
      <input v-model="query" aria-label="搜索知识" placeholder="搜索问题和解决方案…" />
      <ElSelect v-model="validationStatus" aria-label="验证状态" placeholder="全部状态" clearable>
        <ElOption label="未验证" value="unverified" />
        <ElOption label="已验证" value="verified" />
        <ElOption label="已过期" value="outdated" />
      </ElSelect>
      <ElSelect v-model="tag" aria-label="标签" placeholder="全部标签" clearable>
        <ElOption v-for="item in availableTags" :key="item" :label="item" :value="item" />
      </ElSelect>
    </div>

    <div v-if="error" class="conv-error" role="alert">{{ error }}</div>
    <div v-if="loading" class="loading-state">正在加载…</div>
    <div v-else-if="entries.length" class="knowledge-list" :aria-label="`${total} 条知识`">
      <button
        v-for="entry in entries"
        :key="entry.id"
        class="knowledge-row"
        :data-testid="`knowledge-entry-${entry.id}`"
        @click="openEntry(entry)"
      >
        <div class="knowledge-row-main">
          <strong>{{ entry.question }}</strong>
          <p>{{ entry.solution }}</p>
          <div class="knowledge-row-tags">
            <span v-for="item in entry.tags" :key="item">{{ item }}</span>
          </div>
        </div>
        <div class="knowledge-row-meta">
          <span class="knowledge-status" :data-status="entry.validation_status">
            {{ statusLabels[entry.validation_status] }}
          </span>
          <span class="knowledge-index-status" :data-status="entry.index_status">
            {{ indexLabels[entry.index_status] }}
          </span>
          <time>{{ new Date(entry.updated_at).toLocaleDateString() }}</time>
        </div>
      </button>
    </div>
    <ElEmpty v-else description="暂无知识，请从已完成的回答中保存。" />
  </main>
</template>
