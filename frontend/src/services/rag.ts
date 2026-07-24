import { createParser } from 'eventsource-parser'

import { apiUrl, ApiError } from '@/services/api'
import type {
  RagDoneEvent,
  RagErrorEvent,
  RagNoAnswerEvent,
  RagRetrievalEvent,
  RagStreamRequest,
  RagTokenEvent,
} from '@/types/rag'

const MAX_EVENT_DATA_CHARS = 1_000_000

export interface RagStreamHandlers {
  onRetrieval(event: RagRetrievalEvent): void
  onToken(event: RagTokenEvent): void
  onNoAnswer(event: RagNoAnswerEvent): void
  onDone(event: RagDoneEvent): void
  onError(event: RagErrorEvent): void
}

export async function streamRagAnswer(
  knowledgeBaseId: string,
  request: RagStreamRequest,
  handlers: RagStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(
    apiUrl(`/api/v1/knowledge-bases/${knowledgeBaseId}/rag/stream`),
    {
      method: 'POST',
      headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      signal,
    },
  )
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null
    throw new ApiError(response.status, body?.detail ?? '回答生成服务暂时不可用')
  }
  if (!response.headers.get('content-type')?.includes('text/event-stream')) {
    throw new ApiError(502, '回答生成服务返回了无效响应')
  }
  if (!response.body) throw new ApiError(502, '回答生成服务未返回数据流')

  let done = false
  const parser = createParser({
    onEvent(event) {
      if (event.data.length > MAX_EVENT_DATA_CHARS) {
        throw new ApiError(502, '回答生成事件过大')
      }
      const data = JSON.parse(event.data) as unknown
      if (event.event === 'retrieval') handlers.onRetrieval(data as RagRetrievalEvent)
      else if (event.event === 'token') handlers.onToken(data as RagTokenEvent)
      else if (event.event === 'no_answer') handlers.onNoAnswer(data as RagNoAnswerEvent)
      else if (event.event === 'done') {
        done = true
        handlers.onDone(data as RagDoneEvent)
      } else if (event.event === 'error') {
        done = true
        handlers.onError(data as RagErrorEvent)
      }
    },
  })
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  try {
    while (true) {
      const { value, done: streamDone } = await reader.read()
      if (streamDone) break
      parser.feed(decoder.decode(value, { stream: true }))
    }
    parser.feed(decoder.decode())
  } finally {
    reader.releaseLock()
  }
  if (!done && !signal?.aborted) {
    throw new ApiError(502, '回答生成数据流意外中断')
  }
}
