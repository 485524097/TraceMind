import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DocumentVersionDialog from '@/components/DocumentVersionDialog.vue'
import {
  downloadDocumentVersion,
  listDocumentVersions,
  requestDocumentIndex,
} from '@/services/documents'
import type { DocumentItem, DocumentVersion } from '@/types/document'

vi.mock('@/services/documents', () => ({
  listDocumentVersions: vi.fn(),
  downloadDocumentVersion: vi.fn(),
  requestDocumentIndex: vi.fn(),
}))
const mockedList = vi.mocked(listDocumentVersions)
const mockedDownload = vi.mocked(downloadDocumentVersion)
const mockedIndex = vi.mocked(requestDocumentIndex)
const version: DocumentVersion = {
  id: 'version-id',
  version_number: 2,
  content_hash: 'a'.repeat(64),
  file_size: 12,
  mime_type: 'text/markdown',
  extension: '.md',
  created_at: '2026-07-17T00:00:00Z',
  parse_status: 'succeeded',
  parser_name: 'markdown',
  parser_version: '1',
  chunk_count: 2,
  parse_started_at: '2026-07-17T00:00:00Z',
  parsed_at: '2026-07-17T00:01:00Z',
  last_parse_attempt_at: '2026-07-17T00:00:00Z',
  parse_error_code: null,
  parse_error_message: null,
  index_status: 'succeeded',
  active_index_generation: 'generation-id',
  index_started_at: '2026-07-17T00:01:00Z',
  indexed_at: '2026-07-17T00:02:00Z',
  last_index_attempt_at: '2026-07-17T00:01:00Z',
  indexed_chunk_count: 2,
  embedding_model: 'fake',
  embedding_dimension: 3,
  index_error_code: null,
  index_error_message: null,
}
const document: DocumentItem = {
  id: 'document-id',
  knowledge_base_id: 'kb-id',
  name: 'sample.md',
  source_type: 'upload',
  created_at: version.created_at,
  updated_at: version.created_at,
  version_count: 1,
  latest_version: version,
}

describe('DocumentVersionDialog', () => {
  beforeEach(() => {
    mockedList.mockReset()
    mockedDownload.mockReset()
    mockedIndex.mockReset()
  })

  it('loads versions and triggers a historical download', async () => {
    mockedList.mockResolvedValue([version])
    const wrapper = mount(DocumentVersionDialog, {
      props: { modelValue: false, knowledgeBaseId: 'kb-id', document },
      global: {
        stubs: {
          ElDialog: { props: ['modelValue', 'title'], template: '<section><h2>{{ title }}</h2><slot /></section>' },
        },
      },
    })

    await wrapper.setProps({ modelValue: true })
    await flushPromises()
    expect(wrapper.text()).toContain('Version 2')
    const download = wrapper.findAll('button').find((button) => button.text() === '下载')
    await download?.trigger('click')
    expect(mockedDownload).toHaveBeenCalledWith('kb-id', 'document-id', 'version-id')
  })

  it('requests a force index for an indexed historical version', async () => {
    mockedList.mockResolvedValue([version])
    mockedIndex.mockResolvedValue({
      queued: true,
      version: { version_id: version.id, ...version },
    })
    const wrapper = mount(DocumentVersionDialog, {
      props: { modelValue: false, knowledgeBaseId: 'kb-id', document },
      global: {
        stubs: {
          ElDialog: {
            props: ['modelValue', 'title'],
            template: '<section><slot /></section>',
          },
        },
      },
    })
    await wrapper.setProps({ modelValue: true })
    await flushPromises()

    const reindex = wrapper.findAll('button').find((button) => button.text() === '重新索引')
    await reindex?.trigger('click')
    await flushPromises()

    expect(mockedIndex).toHaveBeenCalledWith('kb-id', 'document-id', 'version-id', true)
  })
})
