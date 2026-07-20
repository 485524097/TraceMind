import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DocumentVersionDialog from '@/components/DocumentVersionDialog.vue'
import { downloadDocumentVersion, listDocumentVersions } from '@/services/documents'
import type { DocumentItem, DocumentVersion } from '@/types/document'

vi.mock('@/services/documents', () => ({
  listDocumentVersions: vi.fn(),
  downloadDocumentVersion: vi.fn(),
}))
const mockedList = vi.mocked(listDocumentVersions)
const mockedDownload = vi.mocked(downloadDocumentVersion)
const version: DocumentVersion = {
  id: 'version-id',
  version_number: 2,
  content_hash: 'a'.repeat(64),
  file_size: 12,
  mime_type: 'text/markdown',
  extension: '.md',
  created_at: '2026-07-17T00:00:00Z',
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
    await wrapper.get('button').trigger('click')
    expect(mockedDownload).toHaveBeenCalledWith('kb-id', 'document-id', 'version-id')
  })
})
