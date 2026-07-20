import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DocumentChunkDialog from '@/components/DocumentChunkDialog.vue'
import { listDocumentChunks } from '@/services/documents'
import type { DocumentItem } from '@/types/document'

vi.mock('@/services/documents', () => ({ listDocumentChunks: vi.fn() }))
const mockedList = vi.mocked(listDocumentChunks)

const document: DocumentItem = {
  id: 'document-id',
  knowledge_base_id: 'kb-id',
  name: 'sample.md',
  source_type: 'upload',
  created_at: '2026-07-20T00:00:00Z',
  updated_at: '2026-07-20T00:00:00Z',
  version_count: 1,
  latest_version: {
    id: 'version-id',
    version_number: 1,
    content_hash: 'a'.repeat(64),
    file_size: 10,
    mime_type: 'text/markdown',
    extension: '.md',
    created_at: '2026-07-20T00:00:00Z',
    parse_status: 'succeeded',
    parser_name: 'markdown',
    parser_version: '1',
    chunk_count: 1,
    parse_started_at: '2026-07-20T00:00:00Z',
    parsed_at: '2026-07-20T00:00:01Z',
    last_parse_attempt_at: '2026-07-20T00:00:00Z',
    parse_error_code: null,
    parse_error_message: null,
  },
}

describe('DocumentChunkDialog', () => {
  beforeEach(() => mockedList.mockReset())

  it('shows content and exact citation metadata', async () => {
    mockedList.mockResolvedValue({
      items: [
        {
          id: 'chunk-id',
          chunk_index: 0,
          content: '正文',
          content_hash: 'b'.repeat(64),
          char_count: 2,
          page_number: 3,
          start_line: 12,
          end_line: 28,
          section_title: '安装说明',
          chunk_type: 'paragraph',
          language: 'markdown',
          created_at: '2026-07-20T00:00:01Z',
        },
      ],
      total: 1,
      offset: 0,
      limit: 20,
      version: {
        version_id: 'version-id',
        ...document.latest_version,
      },
    })

    const wrapper = mount(DocumentChunkDialog, {
      props: { modelValue: true, knowledgeBaseId: 'kb-id', document },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('正文')
    expect(wrapper.text()).toContain('安装说明，第 3 页，第 12–28 行')
    expect(wrapper.text()).toContain('2 字符')
  })

  it('shows a safe failed-state message', async () => {
    mockedList.mockResolvedValue({
      items: [],
      total: 0,
      offset: 0,
      limit: 20,
      version: {
        version_id: 'version-id',
        ...document.latest_version,
        parse_status: 'failed',
        chunk_count: 0,
        parse_error_code: 'no_extractable_text',
        parse_error_message: 'Document contains no extractable text',
      },
    })
    const wrapper = mount(DocumentChunkDialog, {
      props: { modelValue: true, knowledgeBaseId: 'kb-id', document },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('扫描型 PDF 当前不支持 OCR')
  })

  it('loads the next chunk page', async () => {
    mockedList
      .mockResolvedValueOnce({
        items: [],
        total: 21,
        offset: 0,
        limit: 20,
        version: { version_id: 'version-id', ...document.latest_version },
      })
      .mockResolvedValueOnce({
        items: [],
        total: 21,
        offset: 20,
        limit: 20,
        version: { version_id: 'version-id', ...document.latest_version },
      })
    const wrapper = mount(DocumentChunkDialog, {
      props: { modelValue: true, knowledgeBaseId: 'kb-id', document },
    })
    await flushPromises()

    const next = wrapper.findAll('button').find((button) => button.text() === '下一页')
    await next?.trigger('click')
    await flushPromises()

    expect(mockedList).toHaveBeenLastCalledWith('kb-id', 'document-id', 'version-id', 20, 20)
  })
})
