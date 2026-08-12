<script setup lang="ts">
import {
  ElAlert,
  ElButton,
  ElDropdown,
  ElDropdownItem,
  ElDropdownMenu,
  ElEmpty,
  ElMessage,
  ElMessageBox,
} from 'element-plus'
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import KnowledgeBaseFormDialog from '@/components/KnowledgeBaseFormDialog.vue'
import { ApiError } from '@/services/api'
import { deleteKnowledgeBase, listKnowledgeBases } from '@/services/knowledgeBases'
import type { KnowledgeBase } from '@/types/knowledgeBase'

const items = ref<KnowledgeBase[]>([])
const loading = ref(false)
const errorMessage = ref('')
const dialogVisible = ref(false)
const editingKnowledgeBase = ref<KnowledgeBase | null>(null)
const deletingId = ref<string | null>(null)

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(new Date(value))
}

async function loadKnowledgeBases(): Promise<void> {
  if (loading.value) return
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await listKnowledgeBases()
    items.value = response.items
  } catch {
    errorMessage.value = '知识库列表加载失败，请检查后端服务后重试'
  } finally {
    loading.value = false
  }
}

function openCreateDialog(): void {
  editingKnowledgeBase.value = null
  dialogVisible.value = true
}

function openEditDialog(knowledgeBase: KnowledgeBase): void {
  editingKnowledgeBase.value = knowledgeBase
  dialogVisible.value = true
}

async function handleSaved(): Promise<void> {
  await loadKnowledgeBases()
}

async function confirmDelete(knowledgeBase: KnowledgeBase): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除知识库"${knowledgeBase.name}"吗？此操作无法撤销。`,
      '删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  if (deletingId.value) return
  deletingId.value = knowledgeBase.id
  try {
    await deleteKnowledgeBase(knowledgeBase.id)
    ElMessage.success('知识库删除成功')
    await loadKnowledgeBases()
  } catch (error) {
    ElMessage.error(
      error instanceof ApiError && error.status === 409
        ? '知识库中仍有文档，请先删除文档'
        : '知识库删除失败，请稍后重试',
    )
  } finally {
    deletingId.value = null
  }
}

onMounted(loadKnowledgeBases)
</script>

<template>
  <main class="management-page">
    <header class="management-header">
      <div>
        <h1>知识库</h1>
        <p>用于整理文档、检索知识并生成可追溯回答的本地空间。</p>
      </div>
      <div class="header-actions">
        <ElButton
          v-if="errorMessage"
          :loading="loading"
          size="small"
          text
          @click="loadKnowledgeBases"
          >重试</ElButton
        >
        <ElButton type="primary" @click="openCreateDialog">新建</ElButton>
      </div>
    </header>

    <ElAlert
      v-if="errorMessage"
      :title="errorMessage"
      type="error"
      show-icon
      :closable="false"
      style="max-width: 1160px; margin: 0 auto var(--space-lg)"
    />

    <section :aria-busy="loading">
      <div v-if="loading && items.length === 0" class="loading-state">正在加载…</div>
      <ElEmpty v-else-if="items.length === 0 && !errorMessage" description="暂无知识库" />

      <div v-else class="doc-list" style="max-width: 1160px; margin: 0 auto">
        <div v-for="kb in items" :key="kb.id" class="doc-item kb-item">
          <RouterLink :to="`/knowledge-bases/${kb.id}/documents`" class="doc-main kb-item-link">
            <div class="doc-name-row">
              <span class="doc-name">{{ kb.name }}</span>
            </div>
            <div
              v-if="kb.description"
              class="doc-path"
              style="
                font-family: var(--font-sans);
                font-size: var(--font-size-base);
                color: var(--color-text-secondary);
              "
            >
              {{ kb.description }}
            </div>
            <div class="doc-meta-row">
              <span class="doc-meta">更新于 {{ formatDate(kb.updated_at) }}</span>
            </div>
          </RouterLink>
          <ElDropdown trigger="click" :hide-on-click="true">
            <button class="doc-more" aria-label="知识库操作">···</button>
            <template #dropdown>
              <ElDropdownMenu>
                <ElDropdownItem @click="openEditDialog(kb)">编辑</ElDropdownItem>
                <ElDropdownItem
                  :data-testid="`delete-${kb.id}`"
                  divided
                  style="color: var(--color-error)"
                  @click="confirmDelete(kb)"
                  >删除</ElDropdownItem
                >
              </ElDropdownMenu>
            </template>
          </ElDropdown>
        </div>
      </div>
    </section>

    <KnowledgeBaseFormDialog
      v-model="dialogVisible"
      :knowledge-base="editingKnowledgeBase"
      @saved="handleSaved"
    />
  </main>
</template>
