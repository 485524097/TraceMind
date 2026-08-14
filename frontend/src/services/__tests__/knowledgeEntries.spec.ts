import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  createKnowledgeEntry,
  deleteKnowledgeEntry,
  getKnowledgeEntry,
  listKnowledgeEntries,
  requestKnowledgeEntryIndex,
  updateKnowledgeEntry,
} from '@/services/knowledgeEntries'

describe('knowledge entry service', () => {
  afterEach(() => vi.restoreAllMocks())

  it('uses scoped CRUD endpoints and serializes filters', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
    fetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 'entry' })))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [], total: 0, available_tags: [] })),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 'entry' })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 'entry' })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 'entry' })))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))

    await createKnowledgeEntry('kb', {
      source_assistant_message_id: 'answer',
      question: 'Question',
      background: null,
      root_cause: null,
      solution: 'Solution',
      failed_attempts: [],
      validation_status: 'unverified',
      tags: ['python'],
    })
    await listKnowledgeEntries('kb', {
      query: ' race ',
      validationStatus: 'verified',
      tag: 'python',
    })
    await getKnowledgeEntry('kb', 'entry')
    await updateKnowledgeEntry('kb', 'entry', { validation_status: 'outdated' })
    await requestKnowledgeEntryIndex('kb', 'entry', true)
    await deleteKnowledgeEntry('kb', 'entry')

    expect(String(fetchMock.mock.calls[1]?.[0])).toContain(
      'query=race&validation_status=verified&tag=python',
    )
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe('POST')
    expect(fetchMock.mock.calls[3]?.[1]?.method).toBe('PATCH')
    expect(fetchMock.mock.calls[4]?.[1]?.method).toBe('POST')
    expect(fetchMock.mock.calls[4]?.[1]?.body).toBe(JSON.stringify({ force: true }))
    expect(fetchMock.mock.calls[5]?.[1]?.method).toBe('DELETE')
  })
})
