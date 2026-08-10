<script setup lang="ts">
import {
  ElButton,
  ElDropdown,
  ElDropdownItem,
  ElDropdownMenu,
  ElEmpty,
  ElMessage,
  ElMessageBox,
} from 'element-plus'
import {
  computed,
  inject,
  nextTick,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
  type Ref,
} from 'vue'
import { useRoute, useRouter } from 'vue-router'

import EvidenceSourceList from '@/components/EvidenceSourceList.vue'
import KnowledgeEntryFormDialog from '@/components/KnowledgeEntryFormDialog.vue'

import {
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  renameConversation,
} from '@/services/conversations'
import { streamRagAnswer } from '@/services/rag'
import { parseAnswerSegments } from '@/services/ragCitations'
import { getKnowledgeBase } from '@/services/knowledgeBases'
import { createKnowledgeEntry } from '@/services/knowledgeEntries'
import type {
  Conversation,
  ConversationMessage,
  ConversationMessageStatus,
} from '@/types/conversation'
import type { RagDoneEvent } from '@/types/rag'
import type { KnowledgeEntryInput } from '@/types/knowledgeEntry'
import { symbolScopeLabel } from '@/utils/symbolScope'

const route = useRoute()
const router = useRouter()
const knowledgeBaseId = String(route.params.knowledgeBaseId)
const knowledgeBaseName = ref('')

const shellKbName = inject<Ref<string>>('shellKbName', ref(''))
watch(knowledgeBaseName, (name) => {
  shellKbName.value = name || ''
})
const conversations = ref<Conversation[]>([])
const selectedId = ref('')
const messages = ref<ConversationMessage[]>([])
const query = ref('')
const language = ref('')
const loadingList = ref(false)
const loadingMessages = ref(false)
const generating = ref(false)
const pageError = ref('')
type ProgressState =
  | 'preparing'
  | 'retrieved'
  | 'generating'
  | 'finalizing'
  | 'completed'
  | 'failed'
  | 'cancelled'
const progressState = ref<ProgressState | null>(null)
const elapsedSeconds = ref(0)
const activeSourceCount = ref(0)
const evidenceVisible = ref(true)
const evidenceMessageId = ref<string | null>(null)
const knowledgeDialogVisible = ref(false)
const knowledgeSubmitting = ref(false)
const knowledgeSourceMessage = ref<ConversationMessage | null>(null)
const knowledgeInitial = ref<KnowledgeEntryInput>({
  question: '',
  background: null,
  root_cause: null,
  solution: '',
  failed_attempts: [],
  validation_status: 'unverified',
  tags: [],
})
const showRetrievalQuery = import.meta.env.DEV
let controller: AbortController | null = null
let streamVersion = 0
let elapsedTimer: number | null = null
let progressStartedAt = 0

const selectedConversation = computed(() =>
  conversations.value.find(({ id }) => id === selectedId.value),
)
const evidenceMessage = computed(
  () =>
    messages.value.find(({ id, role }) => role === 'assistant' && id === evidenceMessageId.value) ??
    null,
)
const evidenceSources = computed(() => evidenceMessage.value?.sources ?? [])
const evidenceMetadata = computed(() => evidenceMessage.value?.generation_metadata ?? null)

const progressMessage = computed(() => {
  switch (progressState.value) {
    case 'preparing':
      return 'Retrieving…'
    case 'retrieved':
      return `Found ${activeSourceCount.value} sources`
    case 'generating':
      return 'Generating answer…'
    case 'finalizing':
      return 'Finalizing…'
    case 'completed':
      return 'Done'
    case 'failed':
      return 'Failed'
    case 'cancelled':
      return 'Cancelled'
    default:
      return ''
  }
})

function clearElapsedTimer() {
  if (elapsedTimer !== null) {
    window.clearInterval(elapsedTimer)
    elapsedTimer = null
  }
}
function startProgress() {
  clearElapsedTimer()
  progressState.value = 'preparing'
  activeSourceCount.value = 0
  elapsedSeconds.value = 0
  progressStartedAt = Date.now()
  elapsedTimer = window.setInterval(() => {
    elapsedSeconds.value = Math.floor((Date.now() - progressStartedAt) / 1000)
  }, 250)
}
function finishProgress(state: Extract<ProgressState, 'completed' | 'failed' | 'cancelled'>) {
  progressState.value = state
  clearElapsedTimer()
}
function resetProgress() {
  progressState.value = null
  activeSourceCount.value = 0
  elapsedSeconds.value = 0
  clearElapsedTimer()
}

function selectDefaultEvidenceMessage(): void {
  evidenceMessageId.value = null
  for (let i = messages.value.length - 1; i >= 0; i--) {
    const candidate = messages.value[i]
    if (candidate?.role === 'assistant') {
      evidenceMessageId.value = candidate.id
      return
    }
  }
}

async function showEvidence(messageId: string, sourceId?: string): Promise<void> {
  evidenceMessageId.value = messageId
  evidenceVisible.value = true
  if (!sourceId) return
  await nextTick()
  document
    .getElementById(`evidence-source-${messageId}-${sourceId}`)
    ?.scrollIntoView({ block: 'nearest' })
}

function temporaryMessage(
  role: 'user' | 'assistant',
  content: string,
  status: ConversationMessageStatus = 'completed',
): ConversationMessage {
  return {
    id: `temporary-${crypto.randomUUID()}`,
    conversation_id: selectedId.value,
    role,
    status,
    content,
    trace_id: null,
    sources: null,
    generation_metadata: null,
    created_at: new Date().toISOString(),
    knowledge_entry_id: null,
  }
}

function pairedQuestion(message: ConversationMessage): string {
  const index = messages.value.findIndex(({ id }) => id === message.id)
  for (let i = index - 1; i >= 0; i--) {
    const candidate = messages.value[i]
    if (candidate?.role === 'user' && candidate.status === 'completed') return candidate.content
  }
  return ''
}

function openKnowledgeDialog(message: ConversationMessage): void {
  knowledgeSourceMessage.value = message
  knowledgeInitial.value = {
    question: pairedQuestion(message),
    background: null,
    root_cause: null,
    solution: message.content,
    failed_attempts: [],
    validation_status: 'unverified',
    tags: [],
  }
  knowledgeDialogVisible.value = true
}

async function saveKnowledge(value: KnowledgeEntryInput): Promise<void> {
  const message = knowledgeSourceMessage.value
  if (!message) return
  knowledgeSubmitting.value = true
  try {
    const entry = await createKnowledgeEntry(knowledgeBaseId, {
      ...value,
      source_assistant_message_id: message.id,
    })
    message.knowledge_entry_id = entry.id
    knowledgeDialogVisible.value = false
    ElMessage.success('Saved as knowledge')
  } catch {
    ElMessage.error('Could not save this answer as knowledge')
  } finally {
    knowledgeSubmitting.value = false
  }
}

function viewKnowledge(entryId: string): void {
  void router.push(`/knowledge-bases/${knowledgeBaseId}/knowledge/${entryId}`)
}

async function loadList(preferredId?: string): Promise<void> {
  loadingList.value = true
  pageError.value = ''
  try {
    const result = await listConversations(knowledgeBaseId)
    conversations.value = result.items
    const nextId =
      preferredId && result.items.some(({ id }) => id === preferredId)
        ? preferredId
        : selectedId.value && result.items.some(({ id }) => id === selectedId.value)
          ? selectedId.value
          : (result.items[0]?.id ?? '')
    if (nextId && nextId !== selectedId.value) await selectConversation(nextId)
    else if (nextId) await loadMessages(nextId)
    else {
      messages.value = []
      evidenceMessageId.value = null
    }
  } catch {
    pageError.value = '会话列表加载失败，请稍后重试'
  } finally {
    loadingList.value = false
  }
}

async function loadMessages(conversationId: string): Promise<void> {
  const rv = streamVersion
  loadingMessages.value = true
  try {
    const d = await getConversation(knowledgeBaseId, conversationId)
    if (selectedId.value === conversationId && rv === streamVersion) {
      messages.value = d.messages
      selectDefaultEvidenceMessage()
    }
  } catch {
    if (selectedId.value === conversationId) pageError.value = '消息历史加载失败，请稍后重试'
  } finally {
    if (selectedId.value === conversationId) loadingMessages.value = false
  }
}

async function selectConversation(cid: string): Promise<void> {
  stopGeneration(false)
  resetProgress()
  generating.value = false
  controller = null
  streamVersion += 1
  selectedId.value = cid
  messages.value = []
  evidenceMessageId.value = null
  evidenceVisible.value = true
  await loadMessages(cid)
}

async function addConversation(): Promise<void> {
  try {
    const c = await createConversation(knowledgeBaseId)
    conversations.value = [c, ...conversations.value]
    await selectConversation(c.id)
  } catch {
    ElMessage.error('新建会话失败')
  }
}

async function renameSelected(): Promise<void> {
  const c = selectedConversation.value
  if (!c) return
  try {
    const r = await ElMessageBox.prompt('输入新的会话标题', '重命名会话', {
      inputValue: c.title,
      inputPattern: /\S+/,
      inputErrorMessage: '标题不能为空',
    })
    const u = await renameConversation(knowledgeBaseId, c.id, r.value.trim())
    conversations.value = conversations.value.map((i) => (i.id === u.id ? u : i))
  } catch {}
}

async function removeSelected(): Promise<void> {
  const c = selectedConversation.value
  if (!c) return
  try {
    await ElMessageBox.confirm(`确定删除会话"${c.title}"吗？`, '删除会话', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  stopGeneration()
  try {
    await deleteConversation(knowledgeBaseId, c.id)
    selectedId.value = ''
    await loadList()
  } catch {
    ElMessage.error('删除会话失败')
  }
}

async function generate(): Promise<void> {
  const prompt = query.value.trim()
  if (!prompt || generating.value) return
  if (!selectedId.value) {
    await addConversation()
    if (!selectedId.value) return
  }
  const cid = selectedId.value
  const cv = ++streamVersion
  let receivedDone = false
  const assistant = reactive(temporaryMessage('assistant', '', 'completed'))
  messages.value.push(temporaryMessage('user', prompt), assistant)
  evidenceMessageId.value = assistant.id
  evidenceVisible.value = true
  query.value = ''
  generating.value = true
  startProgress()
  controller = new AbortController()
  try {
    await streamRagAnswer(
      knowledgeBaseId,
      { query: prompt, language: language.value.trim() || null, conversation_id: cid },
      {
        onRetrieval(event) {
          if (selectedId.value !== cid || cv !== streamVersion) return
          const previousMessageId = assistant.id
          assistant.trace_id = event.trace_id
          assistant.sources = event.sources
          activeSourceCount.value = event.source_count
          progressState.value = 'retrieved'
          if (event.message_id) assistant.id = event.message_id
          if (evidenceMessageId.value === previousMessageId) evidenceMessageId.value = assistant.id
        },
        onToken(event) {
          if (selectedId.value !== cid || cv !== streamVersion) return
          progressState.value = 'generating'
          assistant.content += event.text
        },
        onNoAnswer(event) {
          if (selectedId.value !== cid || cv !== streamVersion) return
          assistant.status = 'no_answer'
          assistant.content = event.message
        },
        onDone(event) {
          if (selectedId.value !== cid || cv !== streamVersion) return
          receivedDone = true
          progressState.value = 'finalizing'
          assistant.status = event.finish_reason === 'no_answer' ? 'no_answer' : 'completed'
          assistant.generation_metadata = event
        },
        onError(event) {
          if (selectedId.value !== cid || cv !== streamVersion) return
          assistant.status = 'failed'
          assistant.content = event.message
          finishProgress('failed')
        },
      },
      controller.signal,
    )
    if (selectedId.value === cid && cv === streamVersion) {
      if (receivedDone) finishProgress('completed')
      else if (!['failed', 'cancelled'].includes(progressState.value ?? '')) {
        assistant.status = 'failed'
        assistant.content = '回答生成服务暂时不可用，请稍后重试。'
        finishProgress('failed')
      }
      await loadMessages(cid)
      await loadList(cid)
    }
  } catch (error) {
    if (selectedId.value === cid && cv === streamVersion) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        assistant.status = 'cancelled'
        finishProgress('cancelled')
      } else {
        assistant.status = 'failed'
        assistant.content = '回答生成服务暂时不可用，请稍后重试。'
        finishProgress('failed')
      }
    }
  } finally {
    if (cv === streamVersion) {
      generating.value = false
      controller = null
    }
  }
}

function stopGeneration(sc = true) {
  if (sc && generating.value) finishProgress('cancelled')
  controller?.abort()
}

function answerSegments(message: ConversationMessage) {
  return parseAnswerSegments(
    message.content,
    new Set((message.sources ?? []).map((s) => s.source_id)),
  )
}
function doneMetadata(message: ConversationMessage): Partial<RagDoneEvent> | null {
  return message.generation_metadata
}
function formatLatency(v: number | undefined): string {
  return v === undefined ? '—' : `${v} ms`
}

onMounted(async () => {
  try {
    knowledgeBaseName.value = (await getKnowledgeBase(knowledgeBaseId)).name
  } catch {
    pageError.value = '知识库不存在或加载失败'
  }
  await loadList()
})
onBeforeUnmount(() => {
  stopGeneration(false)
  clearElapsedTimer()
  streamVersion += 1
})
</script>

<template>
  <main class="conv-page">
    <div v-if="pageError" class="conv-error" role="alert">{{ pageError }}</div>
    <div class="conv-layout">
      <!-- Sidebar -->
      <aside class="conv-sidebar">
        <div class="conv-sidebar-head">Conversations</div>
        <div class="conv-sidebar-list">
          <button
            v-for="c in conversations"
            :key="c.id"
            class="conv-sidebar-item"
            :class="{ on: c.id === selectedId }"
            :data-testid="`conversation-${c.id}`"
            @click="selectConversation(c.id)"
          >
            <span class="conv-sidebar-title">{{ c.title }}</span>
            <span class="conv-sidebar-time">{{
              new Date(c.updated_at).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
            }}</span>
          </button>
          <ElEmpty
            v-if="!loadingList && conversations.length === 0"
            description="No conversations"
          />
        </div>
        <div class="conv-sidebar-foot">
          <button
            class="conv-sidebar-new"
            data-testid="new-conversation-sidebar"
            @click="addConversation"
          >
            + New
          </button>
          <ElDropdown v-if="selectedConversation" trigger="click" :hide-on-click="true">
            <button class="conv-sidebar-more" aria-label="Conversation actions">···</button>
            <template #dropdown>
              <ElDropdownMenu>
                <ElDropdownItem @click="renameSelected">Rename</ElDropdownItem>
                <ElDropdownItem divided style="color: var(--color-error)" @click="removeSelected"
                  >Delete</ElDropdownItem
                >
              </ElDropdownMenu>
            </template>
          </ElDropdown>
        </div>
      </aside>

      <!-- Thread -->
      <div class="conv-thread" data-testid="conversation-thread">
        <div v-if="loadingMessages" class="loading-state">Loading…</div>
        <div v-else-if="!selectedId" class="conv-empty">
          <ElEmpty description="Select or create a conversation" />
          <ElButton type="primary" data-testid="new-conversation-empty" @click="addConversation"
            >New Conversation</ElButton
          >
        </div>
        <template v-else>
          <ElEmpty v-if="messages.length === 0" description="Ask a question to start" />

          <div
            v-for="msg in messages"
            :key="msg.id"
            class="msg"
            :class="msg.role"
            :data-message-id="msg.id"
          >
            <div class="msg-who">{{ msg.role === 'user' ? 'You' : 'TraceMind' }}</div>
            <div v-if="msg.role === 'user'" class="msg-body user-body">{{ msg.content }}</div>
            <div v-else class="msg-body">
              <template v-for="(seg, i) in answerSegments(msg)" :key="i">
                <button
                  v-if="seg.type === 'citation'"
                  type="button"
                  class="cite-btn"
                  @click="showEvidence(msg.id, seg.sourceId)"
                >
                  {{ seg.text }}
                </button>
                <template v-else>{{ seg.text }}</template>
              </template>
              <div
                v-if="msg.status === 'no_answer' && !msg.content"
                style="color: var(--color-text-secondary)"
              >
                No sufficiently relevant information found in the knowledge base.
              </div>
            </div>

            <!-- Provenance row -->
            <div v-if="msg.role === 'assistant' && msg.sources?.length" class="msg-prov">
              <span
                >Cited from <strong>{{ msg.sources.length }}</strong> source{{
                  msg.sources.length > 1 ? 's' : ''
                }}</span
              >
              <button
                v-if="!evidenceVisible || evidenceMessageId !== msg.id"
                class="msg-prov-link"
                @click="showEvidence(msg.id)"
              >
                View evidence →
              </button>
            </div>
            <div
              v-else-if="
                msg.role === 'assistant' &&
                msg.status === 'completed' &&
                !msg.sources?.length &&
                doneMetadata(msg) &&
                !doneMetadata(msg)?.grounded
              "
              class="msg-ungrounded"
            >
              This answer contains no verified citations. Verify against original sources.
            </div>

            <div
              v-if="
                msg.role === 'assistant' &&
                msg.status === 'completed' &&
                !msg.id.startsWith('temporary-')
              "
              class="msg-knowledge-action"
            >
              <button
                v-if="!msg.knowledge_entry_id"
                type="button"
                class="msg-prov-link"
                @click="openKnowledgeDialog(msg)"
              >
                Save as knowledge
              </button>
              <button
                v-else
                type="button"
                class="msg-prov-link"
                @click="viewKnowledge(msg.knowledge_entry_id)"
              >
                View knowledge →
              </button>
            </div>

            <!-- Execution -->
            <details v-if="msg.role === 'assistant' && doneMetadata(msg)" class="exec-details">
              <summary>Execution details</summary>
              <dl class="exec-grid">
                <dt>Rewrite</dt>
                <dd>
                  {{ doneMetadata(msg)?.query_rewrite_mode ?? '—' }} ·
                  {{ formatLatency(doneMetadata(msg)?.query_rewrite_latency_ms) }}
                </dd>
                <dt>Retrieval</dt>
                <dd>
                  {{ doneMetadata(msg)?.retrieval_mode ?? '—' }} ·
                  {{ formatLatency(doneMetadata(msg)?.retrieval_latency_ms) }}
                </dd>
                <dt>Reranker</dt>
                <dd>
                  {{ doneMetadata(msg)?.reranker_fallback ? 'fallback' : 'no fallback' }} ·
                  {{ formatLatency(doneMetadata(msg)?.rerank_latency_ms) }}
                </dd>
                <dt>LLM</dt>
                <dd>
                  {{ formatLatency(doneMetadata(msg)?.llm_latency_ms) }} ·
                  {{
                    doneMetadata(msg)?.llm_first_token_latency_ms
                      ? doneMetadata(msg)?.llm_first_token_latency_ms + 'ms TTFT'
                      : '—'
                  }}
                </dd>
                <template v-if="doneMetadata(msg)?.path_scope_mode === 'exact'">
                  <dt>Path scope</dt>
                  <dd>{{ doneMetadata(msg)?.scoped_relative_path }}</dd>
                </template>
                <template v-if="symbolScopeLabel(doneMetadata(msg))">
                  <dt>Symbol scope</dt>
                  <dd>{{ symbolScopeLabel(doneMetadata(msg)) }}</dd>
                </template>
                <template v-if="showRetrievalQuery && doneMetadata(msg)?.retrieval_query">
                  <dt>Retrieval query</dt>
                  <dd>{{ doneMetadata(msg)?.retrieval_query }}</dd>
                </template>
              </dl>
            </details>
          </div>

          <!-- Progress -->
          <div
            v-if="selectedId && progressState"
            class="conv-progress"
            :data-state="progressState"
            role="status"
            aria-live="polite"
          >
            <strong>{{ progressMessage }}</strong
            ><span>{{ elapsedSeconds }}s</span>
          </div>

          <!-- Composer -->
          <form
            v-if="selectedId"
            class="conv-composer"
            data-testid="conversation-composer"
            @submit.prevent="generate"
          >
            <input
              v-model="query"
              maxlength="2000"
              aria-label="Your question"
              placeholder="Ask a question…"
            />
            <ElButton native-type="submit" type="primary" :disabled="!query.trim() || generating"
              >Send</ElButton
            >
            <ElButton
              v-if="generating"
              type="danger"
              plain
              data-testid="stop-generation"
              @click="stopGeneration()"
              >Stop</ElButton
            >
          </form>
        </template>
      </div>

      <!-- Evidence Inspector -->
      <aside class="conv-evidence" :class="{ off: !evidenceVisible }">
        <div class="ev-head">
          <span>Evidence</span>
          <button @click="evidenceVisible = false" aria-label="Close evidence">×</button>
        </div>
        <div class="ev-body">
          <EvidenceSourceList
            v-if="evidenceSources.length"
            :sources="evidenceSources"
            :identity-prefix="evidenceMessage?.id"
          />
          <div
            v-else
            style="
              font-size: var(--font-size-sm);
              color: var(--color-text-tertiary);
              padding: var(--space-xl) 0;
              text-align: center;
            "
          >
            No sources available
          </div>

          <hr v-if="evidenceMetadata" class="ev-divider" />
          <div v-if="evidenceMetadata" class="ev-sec-title">Execution</div>
          <dl v-if="evidenceMetadata" class="ev-exec-grid">
            <dt>Rewrite</dt>
            <dd>
              {{ evidenceMetadata.query_rewrite_mode ?? '—' }} ·
              {{ formatLatency(evidenceMetadata.query_rewrite_latency_ms) }}
            </dd>
            <dt>Retrieval</dt>
            <dd>
              {{ evidenceMetadata.retrieval_mode ?? '—' }} ·
              {{ formatLatency(evidenceMetadata.retrieval_latency_ms) }}
            </dd>
            <dt>Reranker</dt>
            <dd>{{ evidenceMetadata.reranker_fallback ? 'fallback' : 'no fallback' }}</dd>
            <dt>LLM</dt>
            <dd>{{ formatLatency(evidenceMetadata.llm_latency_ms) }}</dd>
            <template v-if="evidenceMetadata.path_scope_mode === 'exact'">
              <dt>Path scope</dt>
              <dd>{{ evidenceMetadata.scoped_relative_path }}</dd>
            </template>
            <template v-if="symbolScopeLabel(evidenceMetadata)">
              <dt>Symbol scope</dt>
              <dd>{{ symbolScopeLabel(evidenceMetadata) }}</dd>
            </template>
          </dl>
        </div>
      </aside>
    </div>
    <KnowledgeEntryFormDialog
      v-model="knowledgeDialogVisible"
      title="Save as knowledge"
      :initial-value="knowledgeInitial"
      :submitting="knowledgeSubmitting"
      @submit="saveKnowledge"
    />
  </main>
</template>
