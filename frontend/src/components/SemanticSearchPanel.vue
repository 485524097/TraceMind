<script setup lang="ts">
import { ElButton, ElEmpty, ElMessage } from 'element-plus'
import { ref } from 'vue'

import { semanticSearch } from '@/services/documents'
import type { SemanticSearchResult } from '@/types/document'

const props = defineProps<{ knowledgeBaseId: string }>()
const query = ref('')
const language = ref('')
const loading = ref(false)
const searched = ref(false)
const results = ref<SemanticSearchResult[]>([])

function reference(result: SemanticSearchResult): string {
  if (result.page_number) return `第 ${result.page_number} 页`
  if (result.start_line && result.end_line) return `第 ${result.start_line}-${result.end_line} 行`
  return `Chunk ${result.chunk_index}`
}

async function search(): Promise<void> {
  if (!query.value.trim() || loading.value) return
  loading.value = true
  try {
    const response = await semanticSearch(
      props.knowledgeBaseId,
      query.value.trim(),
      language.value.trim() || null,
      5,
    )
    results.value = response.items
    searched.value = true
  } catch {
    ElMessage.error('语义检索暂时不可用，请稍后重试')
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
          <p class="eyebrow">SEMANTIC SEARCH</p>
          <h2>语义检索</h2>
          <p>使用 Dense Embedding 查询当前版本已激活的索引。</p>
        </div>
      </header>
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
            <span class="search-result-score">{{ result.score.toFixed(4) }}</span>
          </header>
          <p class="search-result-reference">
            {{ result.section_title || '未命名章节' }} · {{ reference(result) }}
          </p>
          <pre class="search-result-content">{{ result.content }}</pre>
        </article>
      </div>
    </div>
  </section>
</template>
