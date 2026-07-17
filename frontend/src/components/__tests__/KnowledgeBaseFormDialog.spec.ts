import { flushPromises, mount } from '@vue/test-utils'
import { ElMessage } from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import KnowledgeBaseFormDialog from '@/components/KnowledgeBaseFormDialog.vue'
import { ApiError } from '@/services/api'
import { createKnowledgeBase, updateKnowledgeBase } from '@/services/knowledgeBases'
import type { KnowledgeBase } from '@/types/knowledgeBase'

vi.mock('@/services/knowledgeBases', () => ({
  createKnowledgeBase: vi.fn(),
  updateKnowledgeBase: vi.fn(),
}))

const mockedCreate = vi.mocked(createKnowledgeBase)
const mockedUpdate = vi.mocked(updateKnowledgeBase)
const knowledgeBase: KnowledgeBase = {
  id: '8eaa2608-e968-4b59-b479-28ac92a71e48',
  name: 'Backend Notes',
  description: 'Original description',
  created_at: '2026-07-17T01:00:00Z',
  updated_at: '2026-07-17T01:00:00Z',
}

function mountDialog(editing: KnowledgeBase | null = null) {
  return mount(KnowledgeBaseFormDialog, {
    props: { modelValue: true, knowledgeBase: editing },
    global: {
      stubs: {
        ElDialog: {
          props: ['modelValue', 'title'],
          emits: ['update:modelValue'],
          template: '<section><h2>{{ title }}</h2><slot /><slot name="footer" /></section>',
        },
      },
    },
  })
}

describe('KnowledgeBaseFormDialog', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    mockedCreate.mockReset()
    mockedUpdate.mockReset()
    vi.spyOn(ElMessage, 'success').mockImplementation(() => ({ close: vi.fn() }))
  })

  it('creates a knowledge base and emits saved', async () => {
    mockedCreate.mockResolvedValue(knowledgeBase)
    const wrapper = mountDialog()
    await wrapper.get('input').setValue('  Backend Notes  ')
    await wrapper.get('textarea').setValue('Original description')

    await wrapper.get('[data-testid="submit-knowledge-base"]').trigger('click')
    await flushPromises()

    expect(mockedCreate).toHaveBeenCalledWith({
      name: 'Backend Notes',
      description: 'Original description',
    })
    expect(wrapper.emitted('saved')).toHaveLength(1)
  })

  it('shows a readable message for a name conflict', async () => {
    mockedCreate.mockRejectedValue(new ApiError(409, 'conflict'))
    const wrapper = mountDialog()
    await wrapper.get('input').setValue('Existing')

    await wrapper.get('[data-testid="submit-knowledge-base"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('已存在同名知识库')
    expect(wrapper.emitted('saved')).toBeUndefined()
  })

  it('updates an existing knowledge base and clears its description', async () => {
    mockedUpdate.mockResolvedValue({ ...knowledgeBase, name: 'Updated', description: null })
    const wrapper = mountDialog(knowledgeBase)
    await wrapper.get('input').setValue('Updated')
    await wrapper.get('textarea').setValue('')

    await wrapper.get('[data-testid="submit-knowledge-base"]').trigger('click')
    await flushPromises()

    expect(mockedUpdate).toHaveBeenCalledWith(knowledgeBase.id, {
      name: 'Updated',
      description: null,
    })
    expect(wrapper.emitted('saved')).toHaveLength(1)
  })

  it('prevents duplicate submissions while a request is pending', async () => {
    let resolveRequest: ((value: KnowledgeBase) => void) | undefined
    mockedCreate.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRequest = resolve
        }),
    )
    const wrapper = mountDialog()
    await wrapper.get('input').setValue('Backend Notes')
    const submitButton = wrapper.get('[data-testid="submit-knowledge-base"]')

    await submitButton.trigger('click')
    await submitButton.trigger('click')

    expect(mockedCreate).toHaveBeenCalledTimes(1)
    resolveRequest?.(knowledgeBase)
    await flushPromises()
  })
})
