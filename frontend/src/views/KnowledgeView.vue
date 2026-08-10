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
    error.value = 'Knowledge entries could not be loaded.'
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
    error.value = 'Knowledge base could not be loaded.'
  }
  await load()
})
</script>

<template>
  <main class="knowledge-page">
    <header class="page-header">
      <div>
        <h1>Knowledge</h1>
        <p>Verified problem-solving knowledge saved from conversations.</p>
      </div>
      <RouterLink :to="`/knowledge-bases/${knowledgeBaseId}/chat`" class="text-action">
        Ask a question →
      </RouterLink>
    </header>

    <div class="knowledge-filters">
      <input
        v-model="query"
        aria-label="Search knowledge"
        placeholder="Search problems and solutions…"
      />
      <ElSelect
        v-model="validationStatus"
        aria-label="Validation status"
        placeholder="All statuses"
        clearable
      >
        <ElOption label="Unverified" value="unverified" />
        <ElOption label="Verified" value="verified" />
        <ElOption label="Outdated" value="outdated" />
      </ElSelect>
      <ElSelect v-model="tag" aria-label="Tag" placeholder="All tags" clearable>
        <ElOption v-for="item in availableTags" :key="item" :label="item" :value="item" />
      </ElSelect>
    </div>

    <div v-if="error" class="conv-error" role="alert">{{ error }}</div>
    <div v-if="loading" class="loading-state">Loading…</div>
    <div
      v-else-if="entries.length"
      class="knowledge-list"
      :aria-label="`${total} knowledge entries`"
    >
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
            {{ entry.validation_status }}
          </span>
          <time>{{ new Date(entry.updated_at).toLocaleDateString() }}</time>
        </div>
      </button>
    </div>
    <ElEmpty v-else description="No knowledge entries yet. Save a completed answer from Ask." />
  </main>
</template>
