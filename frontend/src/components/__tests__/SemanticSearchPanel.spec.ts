import { flushPromises, mount } from '@vue/test-utils'
import { ElMessage } from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SemanticSearchPanel from '@/components/SemanticSearchPanel.vue'
import { hybridSearch, semanticSearch } from '@/services/documents'
import type { SemanticSearchResult } from '@/types/document'

vi.mock('@/services/documents', () => ({ hybridSearch: vi.fn(), semanticSearch: vi.fn() }))
const mockedSearch = vi.mocked(semanticSearch)
const mockedHybridSearch = vi.mocked(hybridSearch)

function result(content = 'class DocumentService'): SemanticSearchResult {
  return {
    score: 0.91234,
    content,
    knowledge_base_id: 'kb-id',
    document_id: 'document-id',
    document_version_id: 'version-id',
    chunk_id: 'chunk-id',
    index_generation: 'generation-id',
    document_name: 'service.py',
    version_number: 2,
    chunk_index: 3,
    content_hash: 'a'.repeat(64),
    chunk_type: 'code',
    language: 'python',
    section_title: 'Document service',
    page_number: null,
    start_line: 10,
    end_line: 14,
  }
}

async function submit(wrapper: ReturnType<typeof mount>, query = 'service layer'): Promise<void> {
  await wrapper.get('input[aria-label="语义查询"]').setValue(query)
  await wrapper.get('form').trigger('submit')
  await flushPromises()
}

describe('SemanticSearchPanel', () => {
  beforeEach(() => {
    mockedSearch.mockReset()
    mockedHybridSearch.mockReset()
    vi.spyOn(ElMessage, 'error').mockImplementation(() => ({ close: vi.fn() }))
  })

  it('uses the dedicated layout, limit five, language filter, and traceable result cards', async () => {
    mockedHybridSearch.mockResolvedValue({ items: [result()] })
    const wrapper = mount(SemanticSearchPanel, { props: { knowledgeBaseId: 'kb-id' } })

    expect(wrapper.find('.semantic-search-content').exists()).toBe(true)
    expect(wrapper.get('form').classes()).toContain('semantic-search-form')
    expect(wrapper.get('form').classes()).not.toContain('document-toolbar')
    await wrapper.get('input[aria-label="语言过滤"]').setValue('python')
    await submit(wrapper)

    expect(mockedHybridSearch).toHaveBeenCalledWith('kb-id', 'service layer', 'python', 5)
    expect(wrapper.findAll('.search-result-card')).toHaveLength(1)
    expect(wrapper.text()).toContain('service.py · V2')
    expect(wrapper.text()).toContain('RRF 分数 0.9123')
    expect(wrapper.text()).toContain('Document service')
    expect(wrapper.text()).toContain('第 10-14 行')
    expect(wrapper.text()).toContain('class DocumentService')
  })

  it('shows a neutral empty state when no result clears the threshold', async () => {
    mockedHybridSearch.mockResolvedValue({ items: [] })
    const wrapper = mount(SemanticSearchPanel, { props: { knowledgeBaseId: 'kb-id' } })

    await submit(wrapper, 'unanswered question')

    expect(wrapper.text()).toContain('未找到足够相关的内容')
    expect(wrapper.text()).toContain('请换个问法，或确认文档中包含相关信息。')
    expect(wrapper.findAll('.search-result-card')).toHaveLength(0)
  })

  it('keeps API failures separate from an empty result', async () => {
    mockedHybridSearch.mockRejectedValue(new Error('unavailable'))
    const wrapper = mount(SemanticSearchPanel, { props: { knowledgeBaseId: 'kb-id' } })

    await submit(wrapper)

    expect(ElMessage.error).toHaveBeenCalledWith('检索暂时不可用，请稍后重试')
    expect(wrapper.text()).not.toContain('未找到足够相关的内容')
  })

  it('keeps long original content once in the DOM without rendering HTML', async () => {
    const longContent = `<strong>unsafe</strong>\n${'完整正文 '.repeat(400)}`
    mockedHybridSearch.mockResolvedValue({ items: [result(longContent)] })
    const wrapper = mount(SemanticSearchPanel, { props: { knowledgeBaseId: 'kb-id' } })

    await submit(wrapper)

    const content = wrapper.get('.search-result-content')
    expect(content.element.textContent).toBe(longContent)
    expect(content.find('strong').exists()).toBe(false)
    expect(wrapper.findAll('.search-result-card')).toHaveLength(1)
  })

  it('defaults to hybrid and switches to dense while clearing old results', async () => {
    mockedHybridSearch.mockResolvedValue({ items: [result()] })
    mockedSearch.mockResolvedValue({ items: [result('dense result')] })
    const wrapper = mount(SemanticSearchPanel, { props: { knowledgeBaseId: 'kb-id' } })

    const modeSelect = wrapper.get('select[aria-label="检索模式"]')
    expect((modeSelect.element as HTMLSelectElement).value).toBe('hybrid')
    await submit(wrapper)
    expect(wrapper.text()).toContain('RRF 分数')

    await modeSelect.setValue('dense')
    await flushPromises()
    expect(wrapper.findAll('.search-result-card')).toHaveLength(0)
    await submit(wrapper)

    expect(mockedSearch).toHaveBeenCalledWith('kb-id', 'service layer', null, 5)
    expect(wrapper.text()).toContain('余弦分数 0.9123')
  })
})
