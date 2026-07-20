import { flushPromises, mount } from '@vue/test-utils'
import { ElMessage } from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SemanticSearchPanel from '@/components/SemanticSearchPanel.vue'
import { semanticSearch } from '@/services/documents'

vi.mock('@/services/documents', () => ({ semanticSearch: vi.fn() }))
const mockedSearch = vi.mocked(semanticSearch)

describe('SemanticSearchPanel', () => {
  beforeEach(() => {
    mockedSearch.mockReset()
    vi.spyOn(ElMessage, 'error').mockImplementation(() => ({ close: vi.fn() }))
  })

  it('searches with a language filter and renders traceable metadata', async () => {
    mockedSearch.mockResolvedValue({
      items: [
        {
          score: 0.91234,
          content: 'class DocumentService',
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
        },
      ],
    })
    const wrapper = mount(SemanticSearchPanel, { props: { knowledgeBaseId: 'kb-id' } })

    await wrapper.get('input[aria-label="语义查询"]').setValue('service layer')
    await wrapper.get('input[aria-label="语言过滤"]').setValue('python')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(mockedSearch).toHaveBeenCalledWith('kb-id', 'service layer', 'python')
    expect(wrapper.text()).toContain('service.py · V2')
    expect(wrapper.text()).toContain('0.9123')
    expect(wrapper.text()).toContain('第 10-14 行')
    expect(wrapper.text()).toContain('class DocumentService')
  })
})
