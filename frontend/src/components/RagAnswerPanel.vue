<script setup lang="ts">
import { ElButton, ElEmpty } from 'element-plus'
import { computed, onBeforeUnmount, ref } from 'vue'

import { streamRagAnswer } from '@/services/rag'
import { parseAnswerSegments } from '@/services/ragCitations'
import type { RagDoneEvent, RagSource } from '@/types/rag'
import { symbolScopeLabel } from '@/utils/symbolScope'

const props = defineProps<{ knowledgeBaseId: string }>()
const query = ref('')
const language = ref('')
const loading = ref(false)
const answer = ref('')
const sources = ref<RagSource[]>([])
const traceId = ref('')
const noAnswerMessage = ref('')
const errorMessage = ref('')
const doneMetadata = ref<RagDoneEvent | null>(null)
const executionMetadata = ref<Record<string, unknown>>({})
let controller: AbortController | null = null

const sourceIds = computed(() => new Set(sources.value.map((source) => source.source_id)))
const answerSegments = computed(() => parseAnswerSegments(answer.value, sourceIds.value))

function location(source: RagSource): string {
  if (source.page_number !== null) return `第 ${source.page_number} 页`
  if (source.start_line !== null && source.end_line !== null) {
    return `第 ${source.start_line}-${source.end_line} 行`
  }
  return `Chunk ${source.chunk_index}`
}

function sourceScore(source: RagSource): string {
  if (source.ranking_mode === 'symbol_exact') return '精确符号命中'
  if (source.ranking_mode === 'reranker') {
    return `Reranker 原始分数 ${source.score.toFixed(4)}`
  }
  return `RRF 分数 ${source.score.toFixed(4)}`
}

function sourceIdentity(source: RagSource): string {
  return (
    source.symbol_signature ||
    source.symbol_qualified_name ||
    source.symbol_name ||
    source.section_title ||
    source.document_name
  )
}

function reset(): void {
  answer.value = ''
  sources.value = []
  traceId.value = ''
  noAnswerMessage.value = ''
  errorMessage.value = ''
  doneMetadata.value = null
  executionMetadata.value = {}
}

async function generate(): Promise<void> {
  if (!query.value.trim() || loading.value) return
  reset()
  loading.value = true
  controller = new AbortController()
  try {
    await streamRagAnswer(
      props.knowledgeBaseId,
      { query: query.value.trim(), language: language.value.trim() || null },
      {
        onRetrieval(event) {
          traceId.value = event.trace_id
          sources.value = event.sources
          executionMetadata.value = { ...executionMetadata.value, ...event }
        },
        onToken(event) {
          answer.value += event.text
        },
        onNoAnswer(event) {
          noAnswerMessage.value = event.message
          executionMetadata.value = { ...executionMetadata.value, ...event }
        },
        onDone(event) {
          doneMetadata.value = event
          executionMetadata.value = { ...executionMetadata.value, ...event }
        },
        onError(event) {
          errorMessage.value = event.message
          executionMetadata.value = { ...executionMetadata.value, ...event }
        },
      },
      controller.signal,
    )
  } catch (error) {
    if (!(error instanceof DOMException && error.name === 'AbortError')) {
      errorMessage.value = '回答生成服务暂时不可用，请稍后重试。'
    }
  } finally {
    loading.value = false
    controller = null
  }
}

function stop(): void {
  controller?.abort()
}

function focusSource(sourceId: string): void {
  document.getElementById(`rag-source-${sourceId}`)?.scrollIntoView({
    behavior: 'smooth',
    block: 'center',
  })
}

onBeforeUnmount(stop)
</script>

<template>
  <section class="knowledge-panel rag-answer-panel">
    <div class="rag-answer-content">
      <header>
        <p class="eyebrow">CITATION-GROUNDED RAG</p>
        <h2>知识库问答</h2>
        <p>基于当前知识库检索结果生成带引用的回答。</p>
      </header>
      <form class="rag-answer-form" @submit.prevent="generate">
        <label>
          <span class="sr-only">知识库问题</span>
          <input v-model="query" maxlength="2000" aria-label="知识库问题" placeholder="输入你的问题" />
        </label>
        <label>
          <span class="sr-only">语言过滤</span>
          <input v-model="language" maxlength="32" aria-label="问答语言过滤" placeholder="语言（可选）" />
        </label>
        <div class="rag-answer-actions">
          <ElButton native-type="submit" type="primary" :disabled="!query.trim() || loading">
            生成回答
          </ElButton>
          <ElButton v-if="loading" type="danger" plain @click="stop">停止生成</ElButton>
        </div>
      </form>

      <p v-if="loading" class="rag-streaming-status">正在检索并生成回答…</p>
      <div v-if="errorMessage" class="form-error" role="alert">{{ errorMessage }}</div>
      <div v-if="noAnswerMessage" class="rag-empty-state">
        <ElEmpty :description="noAnswerMessage" />
      </div>
      <dl
        v-if="executionMetadata.path_scope_mode === 'exact' || symbolScopeLabel(executionMetadata)"
        class="rag-execution-scope"
        data-testid="rag-execution-scope"
      >
        <template v-if="executionMetadata.path_scope_mode === 'exact'">
          <dt>路径限定</dt>
          <dd data-testid="rag-path-scope">{{ executionMetadata.scoped_relative_path }}</dd>
        </template>
        <template v-if="symbolScopeLabel(executionMetadata)">
          <dt>符号限定</dt>
          <dd>{{ symbolScopeLabel(executionMetadata) }}</dd>
        </template>
      </dl>
      <article v-if="answer" class="rag-answer-card">
        <h3>回答</h3>
        <p class="rag-answer-text">
          <template v-for="(segment, index) in answerSegments" :key="index">
            <button
              v-if="segment.type === 'citation'"
              class="rag-citation"
              type="button"
              @click="focusSource(segment.sourceId)"
            >{{ segment.text }}</button>
            <template v-else>{{ segment.text }}</template>
          </template>
        </p>
        <p v-if="doneMetadata && !doneMetadata.grounded" class="rag-grounding-warning">
          该回答未包含有效引用，请结合原始来源核对。
        </p>
      </article>

      <section v-if="sources.length" class="rag-source-list" aria-label="引用来源">
        <h3>原始来源</h3>
        <article
          v-for="source in sources"
          :id="`rag-source-${source.source_id}`"
          :key="source.source_id"
          class="rag-source-card"
        >
          <header class="rag-source-header">
            <strong>[{{ source.source_id }}] {{ source.relative_path || source.document_name }} · V{{ source.version_number }}</strong>
            <span>{{ sourceScore(source) }}</span>
          </header>
          <p v-if="source.ranking_mode === 'reranker'" class="rag-source-ranking">
            原 RRF 分数 {{ source.retrieval_score?.toFixed(4) ?? '—' }} · 原 RRF 排名
            {{ source.retrieval_rank ?? '—' }}
          </p>
          <p>
            <span :title="source.symbol_signature || undefined">{{ sourceIdentity(source) }}</span>
            · {{ location(source) }} ·
            {{ source.chunk_type }}<template v-if="source.language"> · {{ source.language }}</template>
          </p>
          <pre class="rag-source-content">{{ source.content }}</pre>
        </article>
      </section>
      <small v-if="traceId" class="rag-trace-id">Trace: {{ traceId }}</small>
    </div>
  </section>
</template>
