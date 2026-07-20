import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DocumentUploadPanel from '@/components/DocumentUploadPanel.vue'
import { ApiError } from '@/services/api'
import { uploadDocument } from '@/services/documents'
import type { DocumentImportAction, DocumentImportResponse } from '@/types/document'

vi.mock('@/services/documents', () => ({ uploadDocument: vi.fn() }))
const mockedUpload = vi.mocked(uploadDocument)

function response(action: DocumentImportAction): DocumentImportResponse {
  return {
    import_action: action,
    parsing_queued: true,
    document: {
      id: 'document-id',
      knowledge_base_id: 'kb-id',
      name: 'sample.md',
      source_type: 'upload',
      created_at: '2026-07-17T00:00:00Z',
      updated_at: '2026-07-17T00:00:00Z',
      version_count: 1,
      latest_version: {
        id: 'version-id',
        version_number: 1,
        content_hash: 'a'.repeat(64),
        file_size: 4,
        mime_type: 'text/markdown',
        extension: '.md',
        created_at: '2026-07-17T00:00:00Z',
        parse_status: 'pending',
        parser_name: null,
        parser_version: null,
        chunk_count: 0,
        parse_started_at: null,
        parsed_at: null,
        last_parse_attempt_at: null,
        parse_error_code: null,
        parse_error_message: null,
      },
    },
  }
}

function mountPanel() {
  return mount(DocumentUploadPanel, { props: { knowledgeBaseId: 'kb-id' } })
}

async function selectFiles(wrapper: ReturnType<typeof mountPanel>, files: File[]): Promise<void> {
  const input = wrapper.get('[data-testid="document-files"]')
  Object.defineProperty(input.element, 'files', { configurable: true, value: files })
  await input.trigger('change')
}

describe('DocumentUploadPanel', () => {
  beforeEach(() => {
    mockedUpload.mockReset()
  })

  it('selects multiple files and uploads them sequentially', async () => {
    mockedUpload.mockResolvedValue(response('created'))
    const wrapper = mountPanel()
    const files = [new File(['one'], 'one.md'), new File(['two'], 'two.md')]
    await selectFiles(wrapper, files)

    expect(wrapper.text()).toContain('one.md')
    expect(wrapper.text()).toContain('two.md')
    await wrapper.get('[data-testid="upload-documents"]').trigger('click')
    await flushPromises()

    expect(mockedUpload).toHaveBeenCalledTimes(2)
    expect(mockedUpload.mock.calls[0]?.[1]).toBe(files[0])
    expect(mockedUpload.mock.calls[1]?.[1]).toBe(files[1])
    expect(wrapper.emitted('completed')).toHaveLength(1)
  })

  it.each([
    ['created', '新建成功'],
    ['version_created', '新版本成功'],
    ['unchanged', '内容未变化'],
  ] as const)('shows the %s result', async (action, label) => {
    mockedUpload.mockResolvedValue(response(action))
    const wrapper = mountPanel()
    await selectFiles(wrapper, [new File(['content'], 'sample.md')])
    await wrapper.get('[data-testid="upload-documents"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain(label)
  })

  it('continues after one file fails', async () => {
    mockedUpload
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce(response('created'))
    const wrapper = mountPanel()
    await selectFiles(wrapper, [new File(['one'], 'one.md'), new File(['two'], 'two.md')])
    await wrapper.get('[data-testid="upload-documents"]').trigger('click')
    await flushPromises()

    expect(mockedUpload).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('上传失败，请稍后重试')
    expect(wrapper.text()).toContain('新建成功')
  })

  it.each([
    [413, '文件超过大小限制'],
    [415, '不支持该文件类型'],
  ])('maps HTTP %s to a clear message', async (status, message) => {
    mockedUpload.mockImplementationOnce(async () => {
      throw new ApiError(status, 'internal')
    })
    const wrapper = mountPanel()
    await selectFiles(wrapper, [new File(['content'], 'sample.md')])
    await wrapper.get('[data-testid="upload-documents"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain(message)
  })

  it('prevents duplicate submission while uploading', async () => {
    let resolveUpload: ((value: DocumentImportResponse) => void) | undefined
    mockedUpload.mockImplementation(
      () => new Promise((resolve) => (resolveUpload = resolve)),
    )
    const wrapper = mountPanel()
    await selectFiles(wrapper, [new File(['content'], 'sample.md')])
    const button = wrapper.get('[data-testid="upload-documents"]')
    const firstClick = button.trigger('click')
    await nextTick()
    await button.trigger('click')

    expect(mockedUpload).toHaveBeenCalledTimes(1)
    resolveUpload?.(response('created'))
    await firstClick
    await flushPromises()
  })

  it('shows that a saved upload still needs manual parsing when enqueue fails', async () => {
    const result = response('created')
    result.parsing_queued = false
    mockedUpload.mockResolvedValue(result)
    const wrapper = mountPanel()
    await selectFiles(wrapper, [new File(['content'], 'sample.md')])
    await wrapper.get('[data-testid="upload-documents"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('等待手动解析')
  })
})
