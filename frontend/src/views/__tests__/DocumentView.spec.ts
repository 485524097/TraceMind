import { flushPromises, mount } from '@vue/test-utils'
import { ElMessage, ElMessageBox } from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  deleteDocument,
  downloadCurrentDocument,
  listDocuments,
} from '@/services/documents'
import { getKnowledgeBase } from '@/services/knowledgeBases'
import type { DocumentItem, DocumentListResponse } from '@/types/document'
import DocumentView from '@/views/DocumentView.vue'

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
  uploadDocument: vi.fn(),
}))
vi.mock('@/services/knowledgeBases', () => ({ getKnowledgeBase: vi.fn() }))

const mockedList = vi.mocked(listDocuments)
const mockedDelete = vi.mocked(deleteDocument)
const mockedDownload = vi.mocked(downloadCurrentDocument)
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

  it('loads knowledge base and document list', async () => {
    mockedList.mockResolvedValue(response([document]))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('技术资料')
    expect(wrapper.text()).toContain('sample.md')
    expect(wrapper.text()).toContain('V2')
    expect(wrapper.text()).toContain('2.0 KB')
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
})
