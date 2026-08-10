<script setup lang="ts">
import { inject, onMounted, ref, type Ref } from 'vue'
import { ElButton, ElMessage, ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import EvidenceSourceList from '@/components/EvidenceSourceList.vue'
import KnowledgeEntryFormDialog from '@/components/KnowledgeEntryFormDialog.vue'
import { getKnowledgeBase } from '@/services/knowledgeBases'
import {
  deleteKnowledgeEntry,
  getKnowledgeEntry,
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

async function load(): Promise<void> {
  try {
    entry.value = await getKnowledgeEntry(knowledgeBaseId, entryId)
  } catch {
    error.value = 'Knowledge entry could not be loaded.'
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
    editVisible.value = false
    ElMessage.success('Knowledge updated')
  } catch {
    ElMessage.error('Knowledge could not be updated')
  } finally {
    submitting.value = false
  }
}

async function remove(): Promise<void> {
  try {
    await ElMessageBox.confirm('Delete this knowledge entry?', 'Delete knowledge', {
      type: 'warning',
      confirmButtonText: 'Delete',
    })
  } catch {
    return
  }
  try {
    await deleteKnowledgeEntry(knowledgeBaseId, entryId)
    await router.push(`/knowledge-bases/${knowledgeBaseId}/knowledge`)
  } catch {
    ElMessage.error('Knowledge could not be deleted')
  }
}

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
  <main class="knowledge-detail-page">
    <RouterLink :to="`/knowledge-bases/${knowledgeBaseId}/knowledge`" class="back-link">
      ← Knowledge
    </RouterLink>
    <div v-if="error" class="conv-error" role="alert">{{ error }}</div>
    <template v-else-if="entry">
      <header class="knowledge-detail-head">
        <div>
          <span class="knowledge-status" :data-status="entry.validation_status">
            {{ entry.validation_status }}
          </span>
          <h1>{{ entry.question }}</h1>
          <div class="knowledge-row-tags">
            <span v-for="item in entry.tags" :key="item">{{ item }}</span>
          </div>
        </div>
        <div class="knowledge-detail-actions">
          <ElButton @click="editVisible = true">Edit</ElButton>
          <ElButton type="danger" plain @click="remove">Delete</ElButton>
        </div>
      </header>

      <div class="knowledge-detail-layout">
        <article class="knowledge-article">
          <section v-if="entry.background">
            <h2>Background</h2>
            <p>{{ entry.background }}</p>
          </section>
          <section v-if="entry.root_cause">
            <h2>Root cause</h2>
            <p>{{ entry.root_cause }}</p>
          </section>
          <section>
            <h2>Solution</h2>
            <div class="knowledge-prose">{{ entry.solution }}</div>
          </section>
          <section v-if="entry.failed_attempts.length">
            <h2>Failed attempts</h2>
            <ul>
              <li v-for="attempt in entry.failed_attempts" :key="attempt">{{ attempt }}</li>
            </ul>
          </section>
          <section class="knowledge-origin">
            <h2>Original answer</h2>
            <p class="knowledge-question-snapshot">{{ entry.question_snapshot }}</p>
            <div class="knowledge-prose">{{ entry.answer_snapshot }}</div>
            <RouterLink
              v-if="entry.source_conversation_id"
              :to="`/knowledge-bases/${knowledgeBaseId}/chat?conversation=${entry.source_conversation_id}`"
              class="text-action"
            >
              Open original conversation →
            </RouterLink>
            <span v-else class="muted-text">Original conversation is no longer available.</span>
          </section>
        </article>

        <aside class="knowledge-evidence">
          <h2>Evidence</h2>
          <EvidenceSourceList
            v-if="entry.sources_snapshot.length"
            :sources="entry.sources_snapshot"
            :identity-prefix="entry.id"
          />
          <p v-else class="muted-text">No cited evidence was captured for this answer.</p>
        </aside>
      </div>

      <KnowledgeEntryFormDialog
        v-model="editVisible"
        title="Edit knowledge"
        :initial-value="editValue()"
        :submitting="submitting"
        @submit="save"
      />
    </template>
  </main>
</template>
