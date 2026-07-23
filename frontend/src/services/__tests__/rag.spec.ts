import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/services/api'
import { streamRagAnswer } from '@/services/rag'

function response(chunks: string[], contentType = 'text/event-stream'): Response {
  const encoder = new TextEncoder()
  return new Response(
    new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
        controller.close()
      },
    }),
    { status: 200, headers: { 'Content-Type': contentType } },
  )
}

const handlers = () => ({
  onRetrieval: vi.fn(),
  onToken: vi.fn(),
  onNoAnswer: vi.fn(),
  onDone: vi.fn(),
  onError: vi.fn(),
})

describe('streamRagAnswer', () => {
  afterEach(() => vi.restoreAllMocks())

  it('posts JSON and parses events split across arbitrary chunks', async () => {
    const wire =
      'event: retrieval\ndata: {"trace_id":"t","source_count":0,"sources":[]}\n\n' +
      'event: token\ndata: {"trace_id":"t","text":"答"}\n\n' +
      'event: token\ndata: {"trace_id":"t","text":"案"}\n\n' +
      'event: done\ndata: {"trace_id":"t","finish_reason":"stop","grounded":false,' +
      '"valid_citation_count":0,"invalid_citation_count":0,"retrieval_latency_ms":1,' +
      '"llm_latency_ms":2,"total_latency_ms":3}\n\n'
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(response([wire.slice(0, 17), wire.slice(17, 83), wire.slice(83)]))
    const callbacks = handlers()
    await streamRagAnswer('kb', { query: '问题', language: 'java' }, callbacks)

    const [url, init] = fetchMock.mock.calls[0]!
    expect(url).toContain('/api/v1/knowledge-bases/kb/rag/stream')
    expect(init).toMatchObject({
      method: 'POST',
      headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: '问题', language: 'java' }),
    })
    expect(callbacks.onRetrieval).toHaveBeenCalledOnce()
    expect(callbacks.onToken.mock.calls.map(([event]) => event.text)).toEqual(['答', '案'])
    expect(callbacks.onDone).toHaveBeenCalledOnce()
  })

  it('dispatches no_answer and error events', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      response([
        'event: no_answer\ndata: {"trace_id":"t","message":"none"}\n\n' +
          'event: error\ndata: {"trace_id":"t","code":"llm_unavailable","message":"safe"}\n\n',
      ]),
    )
    const callbacks = handlers()
    await streamRagAnswer('kb', { query: 'x' }, callbacks)
    expect(callbacks.onNoAnswer).toHaveBeenCalledOnce()
    expect(callbacks.onError).toHaveBeenCalledOnce()
  })

  it('rejects invalid HTTP, content type, body and unfinished streams', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'disabled' }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    await expect(streamRagAnswer('kb', { query: 'x' }, handlers())).rejects.toBeInstanceOf(ApiError)
    fetchMock.mockResolvedValueOnce(response([], 'application/json'))
    await expect(streamRagAnswer('kb', { query: 'x' }, handlers())).rejects.toBeInstanceOf(ApiError)
    fetchMock.mockResolvedValueOnce(
      new Response(null, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }),
    )
    await expect(streamRagAnswer('kb', { query: 'x' }, handlers())).rejects.toBeInstanceOf(ApiError)
    fetchMock.mockResolvedValueOnce(response(['event: token\ndata: {"text":"x"}\n\n']))
    await expect(streamRagAnswer('kb', { query: 'x' }, handlers())).rejects.toBeInstanceOf(ApiError)
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })

  it('preserves AbortError and does not retry the POST', async () => {
    const abortError = new DOMException('stopped', 'AbortError')
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockRejectedValue(abortError)
    await expect(
      streamRagAnswer('kb', { query: 'x' }, handlers(), new AbortController().signal),
    ).rejects.toBe(abortError)
    expect(fetchMock).toHaveBeenCalledOnce()
  })
})
