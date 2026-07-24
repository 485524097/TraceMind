<script setup lang="ts">
import { ElButton, ElEmpty, ElMessage } from 'element-plus'
import { ref, watch } from 'vue'

import { hybridSearch, rerankedSearch, semanticSearch } from '@/services/documents'
import type { SemanticSearchResult } from '@/types/document'

const props = defineProps<{ knowledgeBaseId: string }>()
const query = ref('')
const language = ref('')
const loading = ref(false)
const searched = ref(false)
const results = ref<SemanticSearchResult[]>([])
const mode = ref<'reranker' | 'hybrid' | 'dense'>('reranker')

watch(mode, () => {
  results.value = []
  searched.value = false
})

function reference(result: SemanticSearchResult): string {
  if (result.page_number) return `第 ${result.page_number} 页`
  if (result.start_line && result.end_line) return `第 ${result.start_line}-${result.end_line} 行`
  return `Chunk ${result.chunk_index}`
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
      <div v-if="searched && results.length === 0" class="semantic-search-empty">
        <ElEmpty description="未找到足够相关的内容" />
        <p>请换个问法，或确认文档中包含相关信息。</p>
      </div>
      <div v-else-if="results.length" class="semantic-search-results">
        <article v-for="result in results" :key="result.chunk_id" class="search-result-card">
          <header class="search-result-header">
            <strong>{{ result.document_name }} · V{{ result.version_number }}</strong>
            <span v-if="mode === 'reranker'" class="search-result-score">
              Reranker 原始分数 {{ result.score.toFixed(4) }}
            </span>
            <span v-else class="search-result-score">
              {{ mode === 'hybrid' ? 'RRF 分数' : '余弦分数' }} {{ result.score.toFixed(4) }}
            </span>
          </header>
          <p v-if="mode === 'reranker'" class="search-result-ranking">
            原 RRF 分数 {{ result.retrieval_score?.toFixed(4) ?? '—' }} · 原 RRF 排名
            {{ result.retrieval_rank ?? '—' }}
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
