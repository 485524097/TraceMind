<script setup lang="ts">
import { ElButton, ElDialog, ElForm, ElFormItem, ElInput, ElMessage } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'

import { ApiError } from '@/services/api'
import { createKnowledgeBase, updateKnowledgeBase } from '@/services/knowledgeBases'
import type { KnowledgeBase } from '@/types/knowledgeBase'

const props = defineProps<{
  modelValue: boolean
  knowledgeBase: KnowledgeBase | null
}>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  saved: []
}>()

const form = reactive({ name: '', description: '' })
const saving = ref(false)
const errorMessage = ref('')
const isEditing = computed(() => props.knowledgeBase !== null)
const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

function resetForm(): void {
  form.name = props.knowledgeBase?.name ?? ''
  form.description = props.knowledgeBase?.description ?? ''
  errorMessage.value = ''
}

watch(
  () => [props.modelValue, props.knowledgeBase] as const,
  ([isVisible]) => {
    if (isVisible) resetForm()
  },
  { immediate: true },
)

async function submit(): Promise<void> {
  if (saving.value) return
  const normalizedName = form.name.trim()
  if (!normalizedName) {
    errorMessage.value = '知识库名称不能为空'
    return
  }
  if (normalizedName.length > 100) {
    errorMessage.value = '知识库名称不能超过 100 个字符'
    return
  }

  saving.value = true
  errorMessage.value = ''
  const description = form.description.trim() || null
  try {
    if (props.knowledgeBase) {
      await updateKnowledgeBase(props.knowledgeBase.id, { name: normalizedName, description })
      ElMessage.success('知识库修改成功')
    } else {
      await createKnowledgeBase({ name: normalizedName, description })
      ElMessage.success('知识库创建成功')
    }
    emit('saved')
    visible.value = false
  } catch (error) {
    errorMessage.value =
      error instanceof ApiError && error.status === 409
        ? '已存在同名知识库，请使用其他名称'
        : '操作失败，请稍后重试'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <ElDialog v-model="visible" :title="isEditing ? '编辑知识库' : '创建知识库'" width="520px">
    <ElForm label-position="top" @submit.prevent="submit">
      <ElFormItem label="名称" required>
        <ElInput
          v-model="form.name"
          maxlength="100"
          show-word-limit
          placeholder="例如：项目技术文档"
        />
      </ElFormItem>
      <ElFormItem label="描述">
        <ElInput
          v-model="form.description"
          type="textarea"
          :rows="4"
          placeholder="简要说明这个知识库的用途"
        />
      </ElFormItem>
      <p v-if="errorMessage" class="form-error" role="alert">{{ errorMessage }}</p>
    </ElForm>
    <template #footer>
      <ElButton :disabled="saving" @click="visible = false">取消</ElButton>
      <ElButton
        data-testid="submit-knowledge-base"
        type="primary"
        :loading="saving"
        @click="submit"
      >
        {{ isEditing ? '保存修改' : '创建' }}
      </ElButton>
    </template>
  </ElDialog>
</template>
