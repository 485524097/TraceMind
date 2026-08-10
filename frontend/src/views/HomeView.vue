<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { fetchHealth } from '@/services/health'
import { listKnowledgeBases } from '@/services/knowledgeBases'
import type { KnowledgeBase } from '@/types/knowledgeBase'

type ServiceStatus = 'checking' | 'available' | 'unavailable'
const serviceStatus = ref<ServiceStatus>('checking')
const backendVersion = ref('')
const recentKbs = ref<KnowledgeBase[]>([])

async function checkBackend(): Promise<void> {
  serviceStatus.value = 'checking'
  try {
    const health = await fetchHealth()
    serviceStatus.value = 'available'
    backendVersion.value = health.version
    const kbs = await listKnowledgeBases()
    recentKbs.value = kbs.items.slice(0, 3)
  } catch {
    serviceStatus.value = 'unavailable'
  }
}

onMounted(checkBackend)
</script>

<template>
  <main class="home-view">
    <div class="home-center">
      <h1>TraceMind</h1>
      <p class="home-desc">Your local knowledge workspace.<br>Documents. Code. Answers with evidence.</p>
      <RouterLink to="/knowledge-bases" class="home-cta">Open Knowledge Bases →</RouterLink>
      <div v-if="recentKbs.length" class="home-recent">
        <div class="home-recent-label">Recent</div>
        <RouterLink
          v-for="kb in recentKbs"
          :key="kb.id"
          :to="`/knowledge-bases/${kb.id}/documents`"
          class="home-recent-item"
        >{{ kb.name }}<span>{{ new Date(kb.updated_at).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' }) }}</span></RouterLink>
      </div>
      <div v-if="serviceStatus === 'unavailable'" class="home-status">
        Backend unavailable — <button class="home-retry" @click="checkBackend">retry</button>
      </div>
    </div>
  </main>
</template>
