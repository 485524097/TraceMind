<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import { ElButton, ElDialog, ElInput, ElOption, ElSelect } from 'element-plus'

import type { KnowledgeEntryInput, ValidationStatus } from '@/types/knowledgeEntry'

const props = defineProps<{
  modelValue: boolean
  initialValue: KnowledgeEntryInput
  title: string
  submitting?: boolean
}>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [value: KnowledgeEntryInput]
}>()

const form = reactive({
  question: '',
  background: '',
  rootCause: '',
  solution: '',
  failedAttempts: '',
  validationStatus: 'unverified' as ValidationStatus,
  tags: '',
})

watch(
  () => [props.modelValue, props.initialValue] as const,
  () => {
    if (!props.modelValue) return
    form.question = props.initialValue.question
    form.background = props.initialValue.background ?? ''
    form.rootCause = props.initialValue.root_cause ?? ''
    form.solution = props.initialValue.solution
    form.failedAttempts = props.initialValue.failed_attempts.join('\n')
    form.validationStatus = props.initialValue.validation_status
    form.tags = props.initialValue.tags.join(', ')
  },
  { immediate: true },
)

const valid = computed(() => !!form.question.trim() && !!form.solution.trim())

function submit(): void {
  if (!valid.value) return
  emit('submit', {
    question: form.question.trim(),
    background: form.background.trim() || null,
    root_cause: form.rootCause.trim() || null,
    solution: form.solution.trim(),
    failed_attempts: form.failedAttempts
      .split('\n')
      .map((item) => item.trim())
      .filter(Boolean),
    validation_status: form.validationStatus,
    tags: form.tags
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean),
  })
}
</script>

<template>
  <ElDialog
    :model-value="modelValue"
    :title="title"
    width="min(680px, 94vw)"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <form class="knowledge-form" @submit.prevent="submit">
      <label>
        <span>Question</span>
        <ElInput v-model="form.question" maxlength="4000" show-word-limit />
      </label>
      <label>
        <span>Background</span>
        <ElInput v-model="form.background" type="textarea" :rows="3" maxlength="20000" />
      </label>
      <label>
        <span>Root cause</span>
        <ElInput v-model="form.rootCause" type="textarea" :rows="3" maxlength="20000" />
      </label>
      <label>
        <span>Solution</span>
        <ElInput v-model="form.solution" type="textarea" :rows="7" maxlength="50000" />
      </label>
      <label>
        <span>Failed attempts (one per line)</span>
        <ElInput v-model="form.failedAttempts" type="textarea" :rows="3" />
      </label>
      <div class="knowledge-form-row">
        <label>
          <span>Validation</span>
          <ElSelect v-model="form.validationStatus">
            <ElOption label="Unverified" value="unverified" />
            <ElOption label="Verified" value="verified" />
            <ElOption label="Outdated" value="outdated" />
          </ElSelect>
        </label>
        <label>
          <span>Tags (comma separated)</span>
          <ElInput v-model="form.tags" placeholder="java, postgres" />
        </label>
      </div>
    </form>
    <template #footer>
      <ElButton @click="emit('update:modelValue', false)">Cancel</ElButton>
      <ElButton type="primary" :disabled="!valid" :loading="submitting" @click="submit">
        Save knowledge
      </ElButton>
    </template>
  </ElDialog>
</template>
