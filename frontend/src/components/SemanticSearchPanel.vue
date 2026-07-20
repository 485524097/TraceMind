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
    <h2>语义检索</h2>
    <p>使用 Dense Embedding 查询当前版本已激活的索引。</p>
    <form class="document-toolbar" @submit.prevent="search">
      <input v-model="query" aria-label="语义查询" maxlength="2000" placeholder="输入代码或文档问题" />
      <input v-model="language" aria-label="语言过滤" maxlength="32" placeholder="语言（可选）" />
      <ElButton native-type="submit" :loading="loading" :disabled="!query.trim()">检索</ElButton>
    </form>
    <ElEmpty v-if="searched && results.length === 0" description="没有找到相关内容" />
    <article v-for="result in results" :key="result.chunk_id" class="search-result">
      <header>
        <strong>{{ result.document_name }} · V{{ result.version_number }}</strong>
        <span>{{ result.score.toFixed(4) }}</span>
      </header>
      <p>{{ result.section_title || '未命名章节' }} · {{ reference(result) }}</p>
      <pre>{{ result.content }}</pre>
    </article>
  </section>
</template>
