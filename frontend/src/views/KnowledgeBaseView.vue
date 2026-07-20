<script setup lang="ts">
import { ElAlert, ElButton, ElEmpty, ElMessage, ElMessageBox } from 'element-plus'
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
    dateStyle: 'medium',
    timeStyle: 'short',
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
      `确定删除知识库“${knowledgeBase.name}”吗？此操作无法撤销。`,
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
        <RouterLink to="/" class="back-link">← 返回首页</RouterLink>
        <p class="eyebrow">KNOWLEDGE BASES</p>
        <h1>知识库管理</h1>
        <p>建立资料边界，为后续文档导入和可追溯问答做好准备。</p>
      </div>
      <div class="header-actions">
        <ElButton :loading="loading" @click="loadKnowledgeBases">刷新</ElButton>
        <ElButton type="primary" @click="openCreateDialog">创建知识库</ElButton>
      </div>
    </header>

    <ElAlert v-if="errorMessage" :title="errorMessage" type="error" show-icon :closable="false" />

    <section class="knowledge-panel" :aria-busy="loading">
      <div v-if="loading && items.length === 0" class="loading-state">正在加载知识库…</div>
      <ElEmpty v-else-if="items.length === 0 && !errorMessage" description="暂无知识库" />
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>名称</th>
              <th>描述</th>
              <th>创建时间</th>
              <th>更新时间</th>
              <th><span class="sr-only">操作</span></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="knowledgeBase in items" :key="knowledgeBase.id">
              <td class="name-cell">{{ knowledgeBase.name }}</td>
              <td>{{ knowledgeBase.description || '暂无描述' }}</td>
              <td>{{ formatDate(knowledgeBase.created_at) }}</td>
              <td>{{ formatDate(knowledgeBase.updated_at) }}</td>
              <td class="row-actions">
                <RouterLink :to="`/knowledge-bases/${knowledgeBase.id}/documents`">
                  <ElButton size="small">文档</ElButton>
                </RouterLink>
                <ElButton size="small" @click="openEditDialog(knowledgeBase)">编辑</ElButton>
                <ElButton
                  :data-testid="`delete-${knowledgeBase.id}`"
                  size="small"
                  type="danger"
                  plain
                  :loading="deletingId === knowledgeBase.id"
                  :disabled="deletingId !== null"
                  @click="confirmDelete(knowledgeBase)"
                >
                  删除
                </ElButton>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <KnowledgeBaseFormDialog
      v-model="dialogVisible"
      :knowledge-base="editingKnowledgeBase"
      @saved="handleSaved"
    />
  </main>
</template>
