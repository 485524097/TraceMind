<script setup lang="ts">
import { ElButton, ElEmpty, ElMessage } from 'element-plus'
import { ref, watch } from 'vue'

import { hybridSearch, rerankedSearch, semanticSearch } from '@/services/documents'
import type { SemanticSearchResponse, SemanticSearchResult } from '@/types/document'
import { normalizeSymbolScope, symbolScopeLabel } from '@/utils/symbolScope'

const props = defineProps<{ knowledgeBaseId: string }>()
const query = ref('')
const language = ref('')
const loading = ref(false)
const searched = ref(false)
const results = ref<SemanticSearchResult[]>([])
const queryMetadata = ref<Pick<
  SemanticSearchResponse,
  | 'path_scope_mode'
  | 'scoped_relative_path'
  | 'semantic_query'
  | 'symbol_scope_mode'
  | 'symbol_scope_reason'
  | 'scoped_symbol_kind'
  | 'scoped_symbol_qualified_name'
  | 'scoped_symbol_signature'
>>({})
const mode = ref<'reranker' | 'hybrid' | 'dense'>('reranker')

watch(mode, () => {
  results.value = []
  queryMetadata.value = {}
  searched.value = false
})

function reference(result: SemanticSearchResult): string {
  if (result.page_number) return `第 ${result.page_number} 页`
  if (result.start_line && result.end_line) return `第 ${result.start_line}-${result.end_line} 行`
  return `Chunk ${result.chunk_index}`
}

function symbolIdentity(result: SemanticSearchResult): string {
  return (
    result.symbol_signature ||
    result.symbol_qualified_name ||
    result.symbol_name ||
    result.section_title ||
    '未命名章节'
  )
}

function resultScore(result: SemanticSearchResult): string {
  if (result.ranking_mode === 'symbol_exact') return '精确符号命中'
  if (mode.value === 'reranker') return `Reranker 原始分数 ${result.score.toFixed(4)}`
  return `${mode.value === 'hybrid' ? 'RRF 分数' : '余弦分数'} ${result.score.toFixed(4)}`
}

async function search(): Promise<void> {
  if (!query.value.trim() || loading.value) return
  loading.value = true
  try {
    const searchFunction =
      mode.value === 'reranker'
        ? rerankedSearch
        : mode.value === 'hybrid'
          ? hybridSearch
          : semanticSearch
    const response = await searchFunction(
      props.knowledgeBaseId,
      query.value.trim(),
      language.value.trim() || null,
      5,
    )
    results.value = response.items
    queryMetadata.value = {
      path_scope_mode: response.path_scope_mode,
      scoped_relative_path: response.scoped_relative_path,
      semantic_query: response.semantic_query,
      symbol_scope_mode: response.symbol_scope_mode,
      symbol_scope_reason: response.symbol_scope_reason,
      scoped_symbol_kind: response.scoped_symbol_kind,
      scoped_symbol_qualified_name: response.scoped_symbol_qualified_name,
      scoped_symbol_signature: response.scoped_symbol_signature,
    }
    searched.value = true
  } catch {
    ElMessage.error(
      mode.value === 'reranker'
        ? 'Reranker 暂时不可用，可切换到混合检索'
        : '检索暂时不可用，请稍后重试',
    )
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <section class="knowledge-panel semantic-search-panel">
    <div class="semantic-search-content">
      <header class="semantic-search-heading">
        <div>
          <p class="eyebrow">RETRIEVAL DEBUG</p>
          <h2>检索调试</h2>
          <p>对比 Dense、Dense + BM25 RRF 与本地 Cross-Encoder 二阶段重排。</p>
        </div>
      </header>
      <label class="retrieval-mode-control">
        <span>检索模式</span>
        <select v-model="mode" aria-label="检索模式">
          <option value="reranker">Reranker</option>
          <option value="hybrid">混合检索</option>
          <option value="dense">Dense 检索</option>
        </select>
      </label>
      <form class="semantic-search-form" @submit.prevent="search">
        <label>
          <span class="sr-only">语义查询</span>
          <input v-model="query" aria-label="语义查询" maxlength="2000" placeholder="输入代码或文档问题" />
        </label>
        <label>
          <span class="sr-only">语言过滤</span>
          <input v-model="language" aria-label="语言过滤" maxlength="32" placeholder="语言（可选）" />
        </label>
        <ElButton native-type="submit" :loading="loading" :disabled="!query.trim()">检索</ElButton>
      </form>
      <dl
        v-if="queryMetadata.path_scope_mode === 'exact' || symbolScopeLabel(queryMetadata)"
        class="semantic-search-scope"
        data-testid="semantic-search-scope"
      >
        <template v-if="queryMetadata.path_scope_mode === 'exact'">
          <dt>路径限定</dt>
          <dd>{{ queryMetadata.scoped_relative_path }}</dd>
          <dt>语义查询</dt>
          <dd>{{ queryMetadata.semantic_query }}</dd>
        </template>
        <template v-if="symbolScopeLabel(queryMetadata)">
          <dt>符号限定</dt>
          <dd>{{ symbolScopeLabel(queryMetadata) }}</dd>
          <template v-if="normalizeSymbolScope(queryMetadata).mode === 'exact'">
            <dt>符号类型</dt>
            <dd>{{ normalizeSymbolScope(queryMetadata).kind || '未记录' }}</dd>
            <dt>限定名称</dt>
            <dd>{{ normalizeSymbolScope(queryMetadata).qualifiedName || '未记录' }}</dd>
            <template v-if="normalizeSymbolScope(queryMetadata).signature">
              <dt>签名</dt>
              <dd>{{ normalizeSymbolScope(queryMetadata).signature }}</dd>
            </template>
          </template>
        </template>
      </dl>
      <div v-if="searched && results.length === 0" class="semantic-search-empty">
        <ElEmpty description="未找到足够相关的内容" />
        <p>请换个问法，或确认文档中包含相关信息。</p>
      </div>
      <div v-else-if="results.length" class="semantic-search-results">
        <article v-for="result in results" :key="result.chunk_id" class="search-result-card">
          <header class="search-result-header">
            <strong>{{ result.relative_path || result.document_name }} · V{{ result.version_number }}</strong>
            <span class="search-result-score">{{ resultScore(result) }}</span>
          </header>
          <p v-if="mode === 'reranker' && result.ranking_mode !== 'symbol_exact'" class="search-result-ranking">
            原 RRF 分数 {{ result.retrieval_score?.toFixed(4) ?? '—' }} · 原 RRF 排名
            {{ result.retrieval_rank ?? '—' }}
          </p>
          <p
            v-if="result.symbol_signature || result.symbol_qualified_name || result.symbol_name"
            class="search-result-symbol"
            :title="result.symbol_signature || undefined"
          >
            {{ symbolIdentity(result) }}
          </p>
          <p class="search-result-reference">
            {{ result.section_title || '未命名章节' }} · {{ reference(result) }}
          </p>
          <pre class="search-result-content">{{ result.content }}</pre>
        </article>
      </div>
    </div>
  </section>
</template>
