<script setup lang="ts">
import { ElButton } from 'element-plus'
import { onMounted, ref } from 'vue'

import { fetchHealth } from '@/services/health'

type ServiceStatus = 'checking' | 'available' | 'unavailable'

const serviceStatus = ref<ServiceStatus>('checking')
const backendVersion = ref('')

async function checkBackend(): Promise<void> {
  serviceStatus.value = 'checking'
  backendVersion.value = ''
  try {
    const health = await fetchHealth()
    serviceStatus.value = 'available'
    backendVersion.value = health.version
  } catch {
    serviceStatus.value = 'unavailable'
  }
}

onMounted(checkBackend)
</script>

<template>
  <main class="page-shell">
    <section class="hero-card">
      <p class="eyebrow">LOCAL-FIRST · TRACEABLE</p>
      <h1>TraceMind</h1>
      <p class="description">面向中文开发者的、本地优先、答案可追溯的 AI 个人知识库。</p>
      <p class="phase">当前状态：基础工程搭建阶段</p>

      <div class="service-panel" aria-live="polite">
        <div>
          <span class="label">后端服务</span>
          <p v-if="serviceStatus === 'checking'" class="status checking">检查中</p>
          <p v-else-if="serviceStatus === 'available'" class="status available">
            服务正常<span v-if="backendVersion"> · v{{ backendVersion }}</span>
          </p>
          <p v-else class="status unavailable">服务不可用，请稍后重试</p>
        </div>
        <ElButton :loading="serviceStatus === 'checking'" type="primary" @click="checkBackend">
          重新检查
        </ElButton>
      </div>
    </section>
  </main>
</template>
