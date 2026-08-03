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
      relative_path: 'sample.md',
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
        index_status: 'pending',
        active_index_generation: null,
        index_started_at: null,
        indexed_at: null,
        last_index_attempt_at: null,
        indexed_chunk_count: 0,
        embedding_model: null,
        embedding_dimension: null,
        index_error_code: null,
        index_error_message: null,
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

async function selectDirectory(
  wrapper: ReturnType<typeof mountPanel>,
  files: File[],
): Promise<void> {
  const input = wrapper.get('[data-testid="document-directory"]')
  Object.defineProperty(input.element, 'files', { configurable: true, value: files })
  await input.trigger('change')
}

function directoryFile(path: string): File {
  const parts = path.split('/')
  const file = new File(['content'], parts[parts.length - 1] ?? 'file.txt')
  Object.defineProperty(file, 'webkitRelativePath', { value: path })
  return file
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

  it('shows a directory preview with ready, ignored and unsupported totals', async () => {
    const wrapper = mountPanel()
    await selectDirectory(wrapper, [
      directoryFile('project/src/main.py'),
      directoryFile('project/node_modules/pkg/index.js'),
      directoryFile('project/assets/logo.png'),
    ])

    expect(wrapper.get('[data-testid="document-directory"]').attributes()).toHaveProperty(
      'webkitdirectory',
    )
    expect(wrapper.text()).toContain('src/main.py')
    expect(wrapper.text()).toContain('准备 1')
    expect(wrapper.text()).toContain('忽略 1')
    expect(wrapper.text()).toContain('不支持 1')
  })

  it('uploads directory files with no more than three concurrent requests', async () => {
    let active = 0
    let maximum = 0
    mockedUpload.mockImplementation(async () => {
      active += 1
      maximum = Math.max(maximum, active)
      await new Promise((resolve) => setTimeout(resolve, 5))
      active -= 1
      return response('created')
    })
    const wrapper = mountPanel()
    await selectDirectory(
      wrapper,
      Array.from({ length: 7 }, (_, index) => directoryFile(`project/src/file${index}.py`)),
    )

    await wrapper.get('[data-testid="upload-documents"]').trigger('click')
    await vi.waitFor(() => expect(mockedUpload).toHaveBeenCalledTimes(7))
    await vi.waitFor(() => {
      expect(wrapper.text()).toContain('完成 7')
      expect(wrapper.text()).toContain('成功 7')
    })

    expect(maximum).toBe(3)
    expect(mockedUpload.mock.calls[0]?.[2]).toBe('src/file0.py')
  })

  it('cancels active and not-yet-started directory uploads', async () => {
    mockedUpload.mockImplementation(
      async (_knowledgeBaseId, _file, _relativePath, signal) =>
        await new Promise((_resolve, reject) => {
          signal?.addEventListener('abort', () =>
            reject(new DOMException('cancelled', 'AbortError')),
          )
        }),
    )
    const wrapper = mountPanel()
    await selectDirectory(
      wrapper,
      Array.from({ length: 5 }, (_, index) => directoryFile(`project/src/file${index}.py`)),
    )
    void wrapper.get('[data-testid="upload-documents"]').trigger('click')
    await vi.waitFor(() => expect(mockedUpload).toHaveBeenCalledTimes(3))

    await wrapper.get('[data-testid="cancel-document-upload"]').trigger('click')
    await flushPromises()

    expect(mockedUpload).toHaveBeenCalledTimes(3)
    expect(wrapper.text()).toContain('已取消')
    expect(wrapper.text()).toContain('跳过 5')
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
