import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getKnowledgeBase } from '@/services/knowledgeBases'
import { getKnowledgeMap } from '@/services/knowledgeMap'
import KnowledgeMapView from '@/views/KnowledgeMapView.vue'

const mocks = vi.hoisted(() => ({
  cytoscape: vi.fn(),
  push: vi.fn(),
  on: vi.fn(),
  fit: vi.fn(),
  resize: vi.fn(),
  destroy: vi.fn(),
  entryStyle: vi.fn((property: string, value?: string) =>
    value === undefined && property === 'display' ? 'element' : undefined,
  ),
  tagStyle: vi.fn((property: string, value?: string) =>
    value === undefined && property === 'display' ? 'element' : undefined,
  ),
  edgeStyle: vi.fn(),
}))

const entryNode = {
  data: (name: string) => (name === 'nodeType' ? 'knowledge_entry' : undefined),
  style: mocks.entryStyle,
}
const tagNode = {
  data: (name: string) => (name === 'nodeType' ? 'tag' : undefined),
  style: mocks.tagStyle,
}
const edge = {
  source: () => entryNode,
  target: () => tagNode,
  style: mocks.edgeStyle,
}
const core = {
  on: mocks.on,
  fit: mocks.fit,
  resize: mocks.resize,
  destroy: mocks.destroy,
  batch: (callback: () => void) => callback(),
  nodes: () => [entryNode, tagNode],
  edges: () => [edge],
}

vi.mock('cytoscape', () => ({ default: mocks.cytoscape }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { knowledgeBaseId: 'kb' } }),
  useRouter: () => ({ push: mocks.push }),
}))
vi.mock('@/services/knowledgeBases', () => ({ getKnowledgeBase: vi.fn() }))
vi.mock('@/services/knowledgeMap', () => ({ getKnowledgeMap: vi.fn() }))

class ResizeObserverMock {
  observe = vi.fn()
  disconnect = vi.fn()
}

describe('KnowledgeMapView', () => {
  beforeEach(() => {
    mocks.cytoscape.mockReset().mockReturnValue(core)
    mocks.push.mockReset()
    mocks.on.mockReset()
    mocks.fit.mockReset()
    mocks.resize.mockReset()
    mocks.destroy.mockReset()
    mocks.entryStyle.mockClear()
    mocks.tagStyle.mockClear()
    mocks.edgeStyle.mockClear()
    vi.stubGlobal('ResizeObserver', ResizeObserverMock)
    vi.mocked(getKnowledgeBase).mockResolvedValue({
      id: 'kb',
      name: 'Engineering',
      description: null,
      created_at: '',
      updated_at: '',
    })
    vi.mocked(getKnowledgeMap).mockResolvedValue({
      nodes: [
        {
          id: 'entry:entry-id',
          type: 'knowledge_entry',
          entity_id: 'entry-id',
          label: 'Fix a transaction',
          metadata: { validation_status: 'verified', tags: ['postgres'] },
        },
        {
          id: 'tag:postgres',
          type: 'tag',
          entity_id: null,
          label: 'postgres',
          metadata: { entry_count: 1 },
        },
      ],
      edges: [
        {
          id: 'tagged:entry:entry-id:tag:postgres',
          type: 'tagged',
          source: 'entry:entry-id',
          target: 'tag:postgres',
          metadata: {},
        },
      ],
    })
  })

  afterEach(() => vi.unstubAllGlobals())

  it('initializes Cytoscape, fits, filters and destroys it', async () => {
    const wrapper = mount(KnowledgeMapView, {
      global: { provide: { shellKbName: ref('') } },
    })
    await flushPromises()

    expect(mocks.cytoscape).toHaveBeenCalledOnce()
    expect(mocks.cytoscape.mock.calls[0]?.[0]?.layout).toMatchObject({ name: 'cose' })
    await wrapper.get('button.secondary-button').trigger('click')
    expect(mocks.fit).toHaveBeenCalledWith(undefined, 32)

    const tagFilter = wrapper.findAll('.knowledge-map-filters input')[3]
    await tagFilter?.setValue(false)
    expect(mocks.tagStyle).toHaveBeenCalledWith('display', 'none')

    wrapper.unmount()
    expect(mocks.destroy).toHaveBeenCalled()
  })

  it('selects an entry node and navigates to knowledge detail', async () => {
    const wrapper = mount(KnowledgeMapView)
    await flushPromises()
    const selectionCall = mocks.on.mock.calls.find((call) => call[1] === 'node, edge')
    const select = selectionCall?.[2] as ((event: unknown) => void) | undefined
    select?.({
      target: {
        id: () => 'entry:entry-id',
        isNode: () => true,
      },
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.get('.knowledge-map-inspector').text()).toContain('Fix a transaction')
    await wrapper.get('.map-open-action').trigger('click')
    expect(mocks.push).toHaveBeenCalledWith('/knowledge-bases/kb/knowledge/entry-id')
  })

  it('navigates a document node to the focused Documents row', async () => {
    vi.mocked(getKnowledgeMap).mockResolvedValueOnce({
      nodes: [
        {
          id: 'document:document-id',
          type: 'document',
          entity_id: 'document-id',
          label: 'transactions.md',
          metadata: { relative_path: 'docs/transactions.md' },
        },
      ],
      edges: [],
    })
    const wrapper = mount(KnowledgeMapView)
    await flushPromises()
    const selectionCall = mocks.on.mock.calls.find((call) => call[1] === 'node, edge')
    const select = selectionCall?.[2] as ((event: unknown) => void) | undefined
    select?.({
      target: {
        id: () => 'document:document-id',
        isNode: () => true,
      },
    })
    await wrapper.vm.$nextTick()
    await wrapper.get('.map-open-action').trigger('click')

    expect(mocks.push).toHaveBeenCalledWith({
      path: '/knowledge-bases/kb/documents',
      query: { query: 'docs/transactions.md', focusDocument: 'document-id' },
    })
  })

  it('shows an empty-state prompt and a safe load error', async () => {
    vi.mocked(getKnowledgeMap).mockResolvedValueOnce({
      nodes: [
        {
          id: 'kb:kb',
          type: 'knowledge_base',
          entity_id: 'kb',
          label: 'Engineering',
          metadata: { entry_count: 0, document_count: 0 },
        },
      ],
      edges: [],
    })
    const emptyWrapper = mount(KnowledgeMapView)
    await flushPromises()
    expect(emptyWrapper.text()).toContain('请先把会话回答保存为知识')
    emptyWrapper.unmount()

    vi.mocked(getKnowledgeMap).mockRejectedValueOnce(new Error('private database detail'))
    const errorWrapper = mount(KnowledgeMapView)
    await flushPromises()
    expect(errorWrapper.get('[role="alert"]').text()).toBe('知识图谱加载失败，请稍后重试')
  })
})
