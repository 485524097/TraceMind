import { flushPromises, mount } from '@vue/test-utils'
import { ElMessage, ElMessageBox } from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import KnowledgeBaseFormDialog from '@/components/KnowledgeBaseFormDialog.vue'
import { deleteKnowledgeBase, listKnowledgeBases } from '@/services/knowledgeBases'
import type { KnowledgeBase, KnowledgeBaseListResponse } from '@/types/knowledgeBase'
import KnowledgeBaseView from '@/views/KnowledgeBaseView.vue'

vi.mock('@/services/knowledgeBases', () => ({
  listKnowledgeBases: vi.fn(),
  deleteKnowledgeBase: vi.fn(),
  createKnowledgeBase: vi.fn(),
  updateKnowledgeBase: vi.fn(),
}))

const mockedList = vi.mocked(listKnowledgeBases)
const mockedDelete = vi.mocked(deleteKnowledgeBase)
const knowledgeBase: KnowledgeBase = {
  id: '8eaa2608-e968-4b59-b479-28ac92a71e48',
  name: 'Backend Notes',
  description: 'Architecture records',
  created_at: '2026-07-17T01:00:00Z',
  updated_at: '2026-07-17T02:00:00Z',
}

function listResponse(items: KnowledgeBase[]): KnowledgeBaseListResponse {
  return { items, total: items.length, offset: 0, limit: 100 }
}

function mountView() {
  return mount(KnowledgeBaseView, {
    global: {
      stubs: {
        RouterLink: { template: '<a><slot /></a>' },
        KnowledgeBaseFormDialog: true,
      },
    },
  })
}

describe('KnowledgeBaseView', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    mockedList.mockReset()
    mockedDelete.mockReset()
    vi.spyOn(ElMessage, 'success').mockImplementation(() => ({ close: vi.fn() }))
    vi.spyOn(ElMessage, 'error').mockImplementation(() => ({ close: vi.fn() }))
  })

  it('loads and displays the knowledge base list', async () => {
    mockedList.mockResolvedValue(listResponse([knowledgeBase]))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('Backend Notes')
    expect(wrapper.text()).toContain('Architecture records')
  })

  it('displays an empty state', async () => {
    mockedList.mockResolvedValue(listResponse([]))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('暂无知识库')
  })

  it('displays a clear list error', async () => {
    mockedList.mockRejectedValue(new Error('network'))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('知识库列表加载失败')
  })

  it('requires confirmation before deleting and refreshes after success', async () => {
    mockedList.mockResolvedValue(listResponse([knowledgeBase]))
    mockedDelete.mockResolvedValue(undefined)
    const confirm = vi.spyOn(ElMessageBox, 'confirm').mockRejectedValueOnce(new Error('cancelled'))
    const wrapper = mountView()
    await flushPromises()
    const deleteButton = wrapper.get(`[data-testid="delete-${knowledgeBase.id}"]`)

    await deleteButton.trigger('click')
    await flushPromises()
    expect(confirm).toHaveBeenCalledOnce()
    expect(mockedDelete).not.toHaveBeenCalled()

    confirm.mockResolvedValueOnce('confirm' as never)
    await deleteButton.trigger('click')
    await flushPromises()
    expect(mockedDelete).toHaveBeenCalledWith(knowledgeBase.id)
    expect(mockedList).toHaveBeenCalledTimes(2)
  })

  it('refreshes the list after the form reports a successful save', async () => {
    mockedList.mockResolvedValue(listResponse([knowledgeBase]))
    const wrapper = mountView()
    await flushPromises()

    wrapper.findComponent(KnowledgeBaseFormDialog).vm.$emit('saved')
    await flushPromises()

    expect(mockedList).toHaveBeenCalledTimes(2)
  })
})
