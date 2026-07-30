<script setup lang="ts">
import { ElButton, ElEmpty, ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

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
import type {
  Conversation,
  ConversationMessage,
  ConversationMessageStatus,
} from '@/types/conversation'
import type { RagDoneEvent, RagSource } from '@/types/rag'

const route = useRoute()
const knowledgeBaseId = String(route.params.knowledgeBaseId)
const knowledgeBaseName = ref('')
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
const showRetrievalQuery = import.meta.env.DEV
let controller: AbortController | null = null
let streamVersion = 0
let elapsedTimer: number | null = null
let progressStartedAt = 0

const selectedConversation = computed(() =>
  conversations.value.find(({ id }) => id === selectedId.value),
)

const statusLabels: Record<ConversationMessageStatus, string> = {
  completed: '已完成',
  no_answer: '无答案',
  failed: '生成失败',
  cancelled: '已取消',
}

const progressMessage = computed(() => {
  switch (progressState.value) {
    case 'preparing':
      return '正在理解问题并检索知识库'
    case 'retrieved':
      return `已找到 ${activeSourceCount.value} 条来源，正在生成回答`
    case 'generating':
      return '正在生成回答'
    case 'finalizing':
      return '正在校验引用并保存'
    case 'completed':
      return '已完成'
    case 'failed':
      return '生成失败'
    case 'cancelled':
      return '已取消'
    default:
      return ''
  }
})

function clearElapsedTimer(): void {
  if (elapsedTimer !== null) {
    window.clearInterval(elapsedTimer)
    elapsedTimer = null
  }
}

function startProgress(): void {
  clearElapsedTimer()
  progressState.value = 'preparing'
  activeSourceCount.value = 0
  elapsedSeconds.value = 0
  progressStartedAt = Date.now()
  elapsedTimer = window.setInterval(() => {
    elapsedSeconds.value = Math.floor((Date.now() - progressStartedAt) / 1000)
  }, 250)
}

function finishProgress(state: Extract<ProgressState, 'completed' | 'failed' | 'cancelled'>): void {
  progressState.value = state
  clearElapsedTimer()
}

function resetProgress(): void {
  progressState.value = null
  activeSourceCount.value = 0
  elapsedSeconds.value = 0
  clearElapsedTimer()
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
  }
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
          : result.items[0]?.id ?? ''
    if (nextId && nextId !== selectedId.value) await selectConversation(nextId)
    else if (nextId) await loadMessages(nextId)
    else messages.value = []
  } catch {
    pageError.value = '会话列表加载失败，请稍后重试'
  } finally {
    loadingList.value = false
  }
}

async function loadMessages(conversationId: string): Promise<void> {
  const requestedVersion = streamVersion
  loadingMessages.value = true
  try {
    const detail = await getConversation(knowledgeBaseId, conversationId)
    if (selectedId.value === conversationId && requestedVersion === streamVersion) {
      messages.value = detail.messages
    }
  } catch {
    if (selectedId.value === conversationId) pageError.value = '消息历史加载失败，请稍后重试'
  } finally {
    if (selectedId.value === conversationId) loadingMessages.value = false
  }
}

async function selectConversation(conversationId: string): Promise<void> {
  stopGeneration(false)
  resetProgress()
  generating.value = false
  controller = null
  streamVersion += 1
  selectedId.value = conversationId
  messages.value = []
  await loadMessages(conversationId)
}

async function addConversation(): Promise<void> {
  try {
    const created = await createConversation(knowledgeBaseId)
    conversations.value = [created, ...conversations.value]
    await selectConversation(created.id)
  } catch {
    ElMessage.error('新建会话失败')
  }
}

async function renameSelected(): Promise<void> {
  const conversation = selectedConversation.value
  if (!conversation) return
  try {
    const result = await ElMessageBox.prompt('输入新的会话标题', '重命名会话', {
      inputValue: conversation.title,
      inputPattern: /\S+/,
      inputErrorMessage: '标题不能为空',
    })
    const updated = await renameConversation(knowledgeBaseId, conversation.id, result.value.trim())
    conversations.value = conversations.value.map((item) =>
      item.id === updated.id ? updated : item,
    )
  } catch {
    // Cancelled prompts do not need an error message.
  }
}

async function removeSelected(): Promise<void> {
  const conversation = selectedConversation.value
  if (!conversation) return
  try {
    await ElMessageBox.confirm(`确定删除会话“${conversation.title}”吗？`, '删除会话', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  stopGeneration()
  try {
    await deleteConversation(knowledgeBaseId, conversation.id)
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
  const conversationId = selectedId.value
  const currentVersion = ++streamVersion
  let receivedDone = false
  const assistant = reactive(temporaryMessage('assistant', '', 'completed'))
  messages.value.push(temporaryMessage('user', prompt), assistant)
  query.value = ''
  generating.value = true
  startProgress()
  controller = new AbortController()
  try {
    await streamRagAnswer(
      knowledgeBaseId,
      {
        query: prompt,
        language: language.value.trim() || null,
        conversation_id: conversationId,
      },
      {
        onRetrieval(event) {
          if (selectedId.value !== conversationId || currentVersion !== streamVersion) return
          assistant.trace_id = event.trace_id
          assistant.sources = event.sources
          activeSourceCount.value = event.source_count
          progressState.value = 'retrieved'
          if (event.message_id) assistant.id = event.message_id
        },
        onToken(event) {
          if (selectedId.value !== conversationId || currentVersion !== streamVersion) return
          progressState.value = 'generating'
          assistant.content += event.text
        },
        onNoAnswer(event) {
          if (selectedId.value !== conversationId || currentVersion !== streamVersion) return
          assistant.status = 'no_answer'
          assistant.content = event.message
        },
        onDone(event) {
          if (selectedId.value !== conversationId || currentVersion !== streamVersion) return
          receivedDone = true
          progressState.value = 'finalizing'
          assistant.status = event.finish_reason === 'no_answer' ? 'no_answer' : 'completed'
          assistant.generation_metadata = event
        },
        onError(event) {
          if (selectedId.value !== conversationId || currentVersion !== streamVersion) return
          assistant.status = 'failed'
          assistant.content = event.message
          finishProgress('failed')
        },
      },
      controller.signal,
    )
    if (selectedId.value === conversationId && currentVersion === streamVersion) {
      if (receivedDone) {
        finishProgress('completed')
      } else if (!['failed', 'cancelled'].includes(progressState.value ?? '')) {
        assistant.status = 'failed'
        assistant.content = '回答生成服务暂时不可用，请稍后重试。'
        finishProgress('failed')
      }
      await loadMessages(conversationId)
      await loadList(conversationId)
    }
  } catch (error) {
    if (selectedId.value === conversationId && currentVersion === streamVersion) {
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
    if (currentVersion === streamVersion) {
      generating.value = false
      controller = null
    }
  }
}

function stopGeneration(showCancelled = true): void {
  if (showCancelled && generating.value) finishProgress('cancelled')
  controller?.abort()
}

function sourceLocation(source: RagSource): string {
  if (source.page_number !== null) return `第 ${source.page_number} 页`
  if (source.start_line !== null && source.end_line !== null) {
    return `第 ${source.start_line}-${source.end_line} 行`
  }
  return `Chunk ${source.chunk_index}`
}

function answerSegments(message: ConversationMessage) {
  return parseAnswerSegments(
    message.content,
    new Set((message.sources ?? []).map((source) => source.source_id)),
  )
}

function focusSource(messageId: string, sourceId: string): void {
  document.getElementById(`conversation-source-${messageId}-${sourceId}`)?.scrollIntoView({
    behavior: 'smooth',
    block: 'center',
  })
}

function doneMetadata(message: ConversationMessage): Partial<RagDoneEvent> | null {
  return message.generation_metadata
}

function rewriteLabel(message: ConversationMessage): string {
  const metadata = doneMetadata(message)
  if (metadata?.query_rewrite_mode === 'rewritten') {
    return '已根据对话历史改写检索问题'
  }
  if (metadata?.query_rewrite_mode === 'fallback') {
    return '查询改写失败，已使用原问题'
  }
  return ''
}

function rewriteDetailLabel(message: ConversationMessage): string {
  switch (doneMetadata(message)?.query_rewrite_mode) {
    case 'skipped':
      return '独立问题，未改写'
    case 'rewritten':
      return '已根据对话历史改写检索问题'
    case 'fallback':
      return '查询改写失败，已使用原问题'
    case 'not_applicable':
      return '不适用'
    default:
      return ''
  }
}

function hasExecutionDetails(message: ConversationMessage): boolean {
  return message.role === 'assistant' && doneMetadata(message) !== null
}

function formatLatency(value: number | undefined): string {
  return value === undefined ? '未记录' : `${value} ms`
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
  <main class="management-page conversation-page" data-layout="viewport-grid">
    <header class="management-header">
      <div>
        <RouterLink :to="`/knowledge-bases/${knowledgeBaseId}/documents`" class="back-link">
          ← 返回文档管理
        </RouterLink>
        <p class="eyebrow">CONVERSATION HISTORY</p>
        <h1>{{ knowledgeBaseName || '知识库问答' }}</h1>
        <p class="conversation-description">会话、回答与生成时引用会持久保存。</p>
        <p v-if="pageError" class="form-error" role="alert">{{ pageError }}</p>
      </div>
    </header>

    <section
      class="conversation-layout"
      data-layout="stretch-columns"
      data-testid="conversation-layout"
    >
      <aside class="knowledge-panel conversation-sidebar" :aria-busy="loadingList">
        <div class="conversation-sidebar-actions">
          <ElButton
            type="primary"
            data-testid="new-conversation-sidebar"
            @click="addConversation"
          >
            新建会话
          </ElButton>
          <ElButton :disabled="!selectedConversation" @click="renameSelected">重命名</ElButton>
          <ElButton type="danger" plain :disabled="!selectedConversation" @click="removeSelected">
            删除
          </ElButton>
        </div>
        <div class="conversation-list-scroll">
          <ElEmpty v-if="!loadingList && conversations.length === 0" description="暂无会话" />
          <button
            v-for="conversation in conversations"
            :key="conversation.id"
            type="button"
            class="conversation-list-item"
            :class="{ active: conversation.id === selectedId }"
            @click="selectConversation(conversation.id)"
          >
            <strong>{{ conversation.title }}</strong>
            <small>{{ new Date(conversation.updated_at).toLocaleString('zh-CN') }}</small>
          </button>
        </div>
      </aside>

      <section class="knowledge-panel conversation-main">
        <div v-if="loadingMessages" class="loading-state">正在加载消息历史…</div>
        <div v-else-if="!selectedId" class="conversation-empty-state">
          <ElEmpty description="新建或选择一个会话后开始问答" />
          <ElButton type="primary" data-testid="new-conversation-empty" @click="addConversation">
            新建会话
          </ElButton>
        </div>
        <div
          v-else
          class="conversation-thread"
          data-scroll-region="true"
          data-testid="conversation-thread"
          aria-label="消息历史"
        >
          <ElEmpty v-if="messages.length === 0" description="还没有消息，输入问题开始问答" />
          <article
            v-for="message in messages"
            :key="message.id"
            class="conversation-message"
            :class="message.role"
            :data-status="message.status"
          >
            <header>
              <strong>{{ message.role === 'user' ? '你' : 'TraceMind' }}</strong>
              <span v-if="message.role === 'assistant'">{{ statusLabels[message.status] }}</span>
            </header>
            <p class="rag-answer-text">
              <template v-for="(segment, index) in answerSegments(message)" :key="index">
                <button
                  v-if="segment.type === 'citation'"
                  type="button"
                  class="rag-citation"
                  @click="focusSource(message.id, segment.sourceId)"
                >{{ segment.text }}</button>
                <template v-else>{{ segment.text }}</template>
              </template>
            </p>
            <p
              v-if="
                message.role === 'assistant' &&
                message.status === 'completed' &&
                doneMetadata(message) &&
                !doneMetadata(message)?.grounded
              "
              class="rag-grounding-warning"
            >
              该回答未包含有效引用，请结合原始来源核对。
            </p>
            <small v-if="rewriteLabel(message)" class="query-rewrite-label">
              {{ rewriteLabel(message) }}
            </small>
            <details
              v-if="hasExecutionDetails(message)"
              class="conversation-execution-details"
            >
              <summary>处理详情</summary>
              <dl>
                <template v-if="rewriteDetailLabel(message)">
                  <dt>查询改写</dt>
                  <dd>{{ rewriteDetailLabel(message) }}</dd>
                </template>
                <dt>历史轮数</dt>
                <dd>{{ doneMetadata(message)?.history_turn_count ?? 0 }}</dd>
                <template v-if="showRetrievalQuery && doneMetadata(message)?.retrieval_query">
                  <dt>检索问题</dt>
                  <dd>{{ doneMetadata(message)?.retrieval_query }}</dd>
                </template>
                <dt>检索模式</dt>
                <dd>{{ doneMetadata(message)?.retrieval_mode ?? '未记录' }}</dd>
                <dt>Reranker 降级</dt>
                <dd>{{ doneMetadata(message)?.reranker_fallback ? '是' : '否' }}</dd>
                <dt>来源数量</dt>
                <dd>{{ doneMetadata(message)?.source_count ?? message.sources?.length ?? 0 }}</dd>
                <template v-if="doneMetadata(message)?.path_scope_mode === 'exact'">
                  <dt>路径限定</dt>
                  <dd>{{ doneMetadata(message)?.scoped_relative_path }}</dd>
                </template>
                <dt>查询改写耗时</dt>
                <dd>{{ formatLatency(doneMetadata(message)?.query_rewrite_latency_ms) }}</dd>
                <dt>检索耗时</dt>
                <dd>{{ formatLatency(doneMetadata(message)?.retrieval_latency_ms) }}</dd>
                <dt>重排耗时</dt>
                <dd>{{ formatLatency(doneMetadata(message)?.rerank_latency_ms) }}</dd>
                <dt>首 Token 延迟</dt>
                <dd>{{ formatLatency(doneMetadata(message)?.llm_first_token_latency_ms) }}</dd>
                <dt>LLM 耗时</dt>
                <dd>{{ formatLatency(doneMetadata(message)?.llm_latency_ms) }}</dd>
                <dt>总耗时</dt>
                <dd>{{ formatLatency(doneMetadata(message)?.total_latency_ms) }}</dd>
              </dl>
            </details>
            <details
              v-if="message.sources?.length"
              class="conversation-sources"
              :data-message-id="message.id"
              data-testid="conversation-sources"
            >
              <summary>引用来源（{{ message.sources.length }}）</summary>
              <article
                v-for="source in message.sources"
                :id="`conversation-source-${message.id}-${source.source_id}`"
                :key="source.source_id"
                class="rag-source-card"
              >
                <strong>[{{ source.source_id }}] {{ source.relative_path || source.document_name }}</strong>
                <p>{{ source.section_title || '未命名章节' }} · {{ sourceLocation(source) }}</p>
                <pre class="rag-source-content">{{ source.content }}</pre>
              </article>
            </details>
          </article>
        </div>

        <div
          v-if="selectedId && progressState"
          class="conversation-progress"
          :data-state="progressState"
          data-testid="conversation-progress"
          role="status"
          aria-live="polite"
        >
          <strong>{{ progressMessage }}</strong>
          <span>已用时 {{ elapsedSeconds }} 秒</span>
        </div>

        <form
          v-if="selectedId"
          class="conversation-composer"
          data-position="panel-bottom"
          data-testid="conversation-composer"
          @submit.prevent="generate"
        >
          <input
            v-model="query"
            maxlength="2000"
            aria-label="知识库问题"
            placeholder="输入你的问题"
          />
          <input
            v-model="language"
            maxlength="32"
            aria-label="问答语言过滤"
            placeholder="语言（可选）"
          />
          <ElButton native-type="submit" type="primary" :disabled="!query.trim() || generating">
            发送
          </ElButton>
          <ElButton
            v-if="generating"
            type="danger"
            plain
            data-testid="stop-generation"
            @click="stopGeneration()"
          >
            停止生成
          </ElButton>
        </form>
      </section>
    </section>
  </main>
</template>
