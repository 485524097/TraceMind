import { afterEach, describe, expect, it, vi } from 'vitest'

import { getKnowledgeMap } from '@/services/knowledgeMap'

describe('knowledge map service', () => {
  afterEach(() => vi.restoreAllMocks())

  it('uses the scoped derived map endpoint', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(JSON.stringify({ nodes: [], edges: [] })))

    await expect(getKnowledgeMap('kb-id')).resolves.toEqual({ nodes: [], edges: [] })
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      '/api/v1/knowledge-bases/kb-id/knowledge-map',
    )
  })
})
