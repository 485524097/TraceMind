import { flushPromises, mount } from '@vue/test-utils'
import { ElMessage, ElMessageBox } from 'element-plus'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  deleteDocument,
  downloadCurrentDocument,
  listDocuments,
  requestDocumentParse,
  requestDocumentIndex,
} from '@/services/documents'
import { getKnowledgeBase } from '@/services/knowledgeBases'
import type { DocumentItem, DocumentListResponse } from '@/types/document'
import DocumentView from '@/views/DocumentView.vue'
import { ApiError } from '@/services/api'

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { knowledgeBaseId: 'kb-id' } }),
  RouterLink: { template: '<a><slot /></a>' },
}))
vi.mock('@/services/documents', () => ({
  listDocuments: vi.fn(),
  deleteDocument: vi.fn(),
  downloadCurrentDocument: vi.fn(),
  downloadDocumentVersion: vi.fn(),
  listDocumentVersions: vi.fn(),
  requestDocumentParse: vi.fn(),
  requestDocumentIndex: vi.fn(),
  semanticSearch: vi.fn(),
  listDocumentChunks: vi.fn(),
  uploadDocument: vi.fn(),
}))
vi.mock('@/services/knowledgeBases', () => ({ getKnowledgeBase: vi.fn() }))

const mockedList = vi.mocked(listDocuments)
const mockedDelete = vi.mocked(deleteDocument)
const mockedDownload = vi.mocked(downloadCurrentDocument)
const mockedParse = vi.mocked(requestDocumentParse)
const mockedIndex = vi.mocked(requestDocumentIndex)
const mockedKnowledgeBase = vi.mocked(getKnowledgeBase)
const document: DocumentItem = {
  id: 'document-id',
  knowledge_base_id: 'kb-id',
  name: 'sample.md',
  source_type: 'upload',
  created_at: '2026-07-17T00:00:00Z',
  updated_at: '2026-07-17T01:00:00Z',
  version_count: 2,
  latest_version: {
    id: 'version-id',
    version_number: 2,
    content_hash: 'a'.repeat(64),
    file_size: 2048,
    mime_type: 'text/markdown',
    extension: '.md',
    created_at: '2026-07-17T01:00:00Z',
    parse_status: 'succeeded',
    parser_name: 'markdown',
    parser_version: '1',
    chunk_count: 2,
    parse_started_at: '2026-07-17T01:00:00Z',
    parsed_at: '2026-07-17T01:00:30Z',
    last_parse_attempt_at: '2026-07-17T01:00:00Z',
    parse_error_code: null,
    parse_error_message: null,
    index_status: 'succeeded',
    active_index_generation: 'generation-id',
    index_started_at: '2026-07-17T01:00:30Z',
    indexed_at: '2026-07-17T01:01:00Z',
    last_index_attempt_at: '2026-07-17T01:00:30Z',
    indexed_chunk_count: 2,
    embedding_model: 'fake',
    embedding_dimension: 3,
    index_error_code: null,
    index_error_message: null,
  },
}

function response(items: DocumentItem[]): DocumentListResponse {
  return { items, total: items.length, offset: 0, limit: 100 }
}

function mountView() {
  return mount(DocumentView, {
    global: {
      stubs: {
        DocumentUploadPanel: { template: '<button data-testid="upload-completed" @click="$emit(\'completed\')">upload</button>' },
        DocumentVersionDialog: true,
        DocumentChunkDialog: {
          props: ['modelValue'],
          template: '<div v-if="modelValue" data-testid="chunk-dialog" />',
        },
      },
    },
  })
}

describe('DocumentView', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    mockedList.mockReset()
    mockedDelete.mockReset()
    mockedDownload.mockReset()
    mockedParse.mockReset()
    mockedIndex.mockReset()
    mockedKnowledgeBase.mockReset()
    mockedKnowledgeBase.mockResolvedValue({
      id: 'kb-id',
      name: '技术资料',
      description: null,
      created_at: '2026-07-17T00:00:00Z',
      updated_at: '2026-07-17T00:00:00Z',
    })
    vi.spyOn(ElMessage, 'success').mockImplementation(() => ({ close: vi.fn() }))
    vi.spyOn(ElMessage, 'error').mockImplementation(() => ({ close: vi.fn() }))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('loads knowledge base and document list', async () => {
    mockedList.mockResolvedValue(response([document]))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('技术资料')
    expect(wrapper.text()).toContain('sample.md')
    expect(wrapper.text()).toContain('V2')
    expect(wrapper.text()).toContain('2.0 KB')
    expect(wrapper.text()).toContain('解析完成')
    expect(wrapper.text()).toContain('2')
    const html = wrapper.html()
    expect(html.indexOf('rag-answer-panel')).toBeLessThan(html.indexOf('semantic-search-panel'))
  })

  it('shows an empty state', async () => {
    mockedList.mockResolvedValue(response([]))
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('暂无文档')
  })

  it('shows a list failure', async () => {
    mockedList.mockRejectedValue(new Error('network'))
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('文档列表加载失败')
  })

  it('searches by name', async () => {
    mockedList.mockResolvedValue(response([document]))
    const wrapper = mountView()
    await flushPromises()
    await wrapper.get('input[aria-label="文档名称搜索"]').setValue('sample')
    await wrapper.get('.document-toolbar form').trigger('submit')
    await flushPromises()
    expect(mockedList).toHaveBeenLastCalledWith('kb-id', 'sample')
  })

  it('refreshes after uploads complete', async () => {
    mockedList.mockResolvedValue(response([document]))
    const wrapper = mountView()
    await flushPromises()
    await wrapper.get('[data-testid="upload-completed"]').trigger('click')
    await flushPromises()
    expect(mockedList).toHaveBeenCalledTimes(2)
  })

  it('triggers current version download', async () => {
    mockedList.mockResolvedValue(response([document]))
    const wrapper = mountView()
    await flushPromises()
    const downloadButton = wrapper.findAll('button').find((button) => button.text() === '下载')
    await downloadButton?.trigger('click')
    expect(mockedDownload).toHaveBeenCalledWith('kb-id', 'document-id')
  })

  it('requires confirmation, deletes, and refreshes', async () => {
    mockedList.mockResolvedValue(response([document]))
    mockedDelete.mockResolvedValue(undefined)
    const confirm = vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as never)
    const wrapper = mountView()
    await flushPromises()
    await wrapper.get('[data-testid="delete-document-document-id"]').trigger('click')
    await flushPromises()

    expect(confirm).toHaveBeenCalledOnce()
    expect(mockedDelete).toHaveBeenCalledWith('kb-id', 'document-id')
    expect(mockedList).toHaveBeenCalledTimes(2)
  })

  it.each([
    ['pending', '等待解析'],
    ['processing', '解析中'],
    ['succeeded', '解析完成'],
    ['failed', '解析失败'],
  ] as const)('shows the %s parse state', async (parseStatus, label) => {
    mockedList.mockResolvedValue(
      response([{ ...document, latest_version: { ...document.latest_version, parse_status: parseStatus } }]),
    )
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain(label)
  })

  it('starts polling for pending documents and stops after a terminal state', async () => {
    vi.useFakeTimers()
    const clearIntervalSpy = vi.spyOn(globalThis, 'clearInterval')
    mockedList
      .mockResolvedValueOnce(
        response([{ ...document, latest_version: { ...document.latest_version, parse_status: 'pending' } }]),
      )
      .mockResolvedValueOnce(response([document]))
    const wrapper = mountView()
    await flushPromises()

    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2500)

    expect(mockedList).toHaveBeenCalledTimes(2)
    wrapper.unmount()
    expect(clearIntervalSpy).toHaveBeenCalled()
  })

  it('requests a force reparse and opens chunk preview', async () => {
    mockedList.mockResolvedValue(response([document]))
    mockedParse.mockResolvedValue({
      queued: true,
      version: {
        version_id: document.latest_version.id,
        ...document.latest_version,
      },
    })
    const wrapper = mountView()
    await flushPromises()

    const reparse = wrapper.findAll('button').find((button) => button.text() === '重新解析')
    await reparse?.trigger('click')
    await flushPromises()
    expect(mockedParse).toHaveBeenCalledWith('kb-id', 'document-id', 'version-id', true)

    const chunks = wrapper.findAll('button').find((button) => button.text() === 'Chunk')
    await chunks?.trigger('click')
    expect(wrapper.find('[data-testid="chunk-dialog"]').exists()).toBe(true)
  })

  it('shows index state and requests a force reindex', async () => {
    mockedList.mockResolvedValue(response([document]))
    mockedIndex.mockResolvedValue({
      queued: true,
      version: { version_id: document.latest_version.id, ...document.latest_version },
    })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('索引完成')
    const reindex = wrapper.findAll('button').find((button) => button.text() === '重新索引')
    await reindex?.trigger('click')
    await flushPromises()

    expect(mockedIndex).toHaveBeenCalledWith('kb-id', 'document-id', 'version-id', true)
  })

  it('shows a clear queue unavailable message', async () => {
    mockedList.mockResolvedValue(response([document]))
    mockedParse.mockRejectedValue(new ApiError(503, 'private broker'))
    const wrapper = mountView()
    await flushPromises()

    const reparse = wrapper.findAll('button').find((button) => button.text() === '重新解析')
    await reparse?.trigger('click')
    await flushPromises()

    expect(ElMessage.error).toHaveBeenCalledWith('解析队列暂时不可用，请稍后重试')
  })
})
