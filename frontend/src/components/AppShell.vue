<script setup lang="ts">
import { computed, inject, ref, watch, type Ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

const route = useRoute()

const kbId = computed(() => {
  const id = route.params.knowledgeBaseId
  return typeof id === 'string' && id ? id : null
})

const shellKbName = inject<Ref<string>>('shellKbName', ref(''))

watch(kbId, () => {
  shellKbName.value = ''
})
</script>

<template>
  <div class="app-shell">
    <header class="global-header">
      <RouterLink to="/" class="brand">TraceMind</RouterLink>
      <nav class="global-nav">
        <RouterLink to="/knowledge-bases" class="global-nav-link" active-class="active"
          >知识库</RouterLink
        >
      </nav>
    </header>

    <div v-if="kbId" class="kb-bar">
      <span class="kb-name">{{ shellKbName || '知识库' }}</span>
      <nav class="kb-tabs">
        <RouterLink :to="`/knowledge-bases/${kbId}/documents`" class="kb-tab" active-class="active"
          >文档</RouterLink
        >
        <RouterLink :to="`/knowledge-bases/${kbId}/chat`" class="kb-tab" active-class="active"
          >问答</RouterLink
        >
        <RouterLink :to="`/knowledge-bases/${kbId}/knowledge`" class="kb-tab" active-class="active"
          >知识</RouterLink
        >
        <RouterLink :to="`/knowledge-bases/${kbId}/map`" class="kb-tab" active-class="active"
          >知识图谱</RouterLink
        >
      </nav>
    </div>

    <main class="app-content">
      <slot />
    </main>
  </div>
</template>
