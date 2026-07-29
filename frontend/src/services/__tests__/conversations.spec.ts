import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  renameConversation,
} from '@/services/conversations'

describe('conversation service', () => {
  afterEach(() => vi.restoreAllMocks())

  it('uses knowledge-base-scoped CRUD endpoints', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
    fetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], total: 0 })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 'conversation' })))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ id: 'conversation', messages: [] })),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 'conversation' })))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))

    await listConversations('kb')
    await createConversation('kb')
    await getConversation('kb', 'conversation')
    await renameConversation('kb', 'conversation', '新标题')
    await deleteConversation('kb', 'conversation')

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      expect.stringContaining('/api/v1/knowledge-bases/kb/conversations?offset=0&limit=100'),
      expect.stringContaining('/api/v1/knowledge-bases/kb/conversations'),
      expect.stringContaining('/api/v1/knowledge-bases/kb/conversations/conversation'),
      expect.stringContaining('/api/v1/knowledge-bases/kb/conversations/conversation'),
      expect.stringContaining('/api/v1/knowledge-bases/kb/conversations/conversation'),
    ])
    expect(fetchMock.mock.calls[1]?.[1]?.body).toBe('{}')
    expect(fetchMock.mock.calls[3]?.[1]?.body).toBe(JSON.stringify({ title: '新标题' }))
    expect(fetchMock.mock.calls[4]?.[1]?.method).toBe('DELETE')
  })
})
