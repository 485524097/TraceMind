import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiRequest } from '@/services/api'
import { hybridSearch, rerankedSearch, semanticSearch, uploadDocument } from '@/services/documents'

vi.mock('@/services/api', () => ({
  apiRequest: vi.fn(),
  apiUrl: vi.fn(),
}))

const mockedApiRequest = vi.mocked(apiRequest)

describe('document search services', () => {
  beforeEach(() => mockedApiRequest.mockReset())

  it('posts hybrid search to the hybrid endpoint with the expected body', async () => {
    mockedApiRequest.mockResolvedValue({ items: [] })

    await hybridSearch('kb-id', 'DiscoveryClient', 'java', 5)

    expect(mockedApiRequest).toHaveBeenCalledWith('/api/v1/knowledge-bases/kb-id/search/hybrid', {
      method: 'POST',
      body: JSON.stringify({ query: 'DiscoveryClient', language: 'java', limit: 5 }),
    })
  })

  it('keeps dense search on the semantic endpoint', async () => {
    mockedApiRequest.mockResolvedValue({ items: [] })

    await semanticSearch('kb-id', '配置中心', null, 5)

    expect(mockedApiRequest).toHaveBeenCalledWith('/api/v1/knowledge-bases/kb-id/search/semantic', {
      method: 'POST',
      body: JSON.stringify({ query: '配置中心', language: null, limit: 5 }),
    })
  })

  it('posts reranked search to the dedicated endpoint', async () => {
    mockedApiRequest.mockResolvedValue({ items: [] })

    await rerankedSearch('kb-id', 'DiscoveryClient', 'java', 5)

    expect(mockedApiRequest).toHaveBeenCalledWith('/api/v1/knowledge-bases/kb-id/search/reranked', {
      method: 'POST',
      body: JSON.stringify({ query: 'DiscoveryClient', language: 'java', limit: 5 }),
    })
  })

  it('adds an optional relative path to the existing multipart upload request', async () => {
    mockedApiRequest.mockResolvedValue({})
    const file = new File(['content'], 'main.py')
    const controller = new AbortController()

    await uploadDocument('kb-id', file, 'backend/main.py', controller.signal)

    const [path, init] = mockedApiRequest.mock.calls[0] ?? []
    expect(path).toBe('/api/v1/knowledge-bases/kb-id/documents')
    expect(init?.method).toBe('POST')
    expect(init?.signal).toBe(controller.signal)
    const body = init?.body as FormData
    expect(body.get('file')).toBe(file)
    expect(body.get('relative_path')).toBe('backend/main.py')
  })
})
