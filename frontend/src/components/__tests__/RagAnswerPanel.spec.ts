import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import RagAnswerPanel from '@/components/RagAnswerPanel.vue'
import { streamRagAnswer } from '@/services/rag'
import type { RagSource } from '@/types/rag'

vi.mock('@/services/rag', () => ({ streamRagAnswer: vi.fn() }))
const mockedStream = vi.mocked(streamRagAnswer)

const source: RagSource = {
  source_id: 'S1',
  score: 0.91,
  content: '<script>not html</script>\nlong source',
  knowledge_base_id: 'kb',
  document_id: 'doc',
  document_version_id: 'version',
  chunk_id: 'chunk',
  index_generation: 'generation',
  document_name: 'sample.md',
  version_number: 2,
  chunk_index: 3,
  content_hash: 'a'.repeat(64),
  chunk_type: 'paragraph',
  language: 'java',
  section_title: '架构',
  page_number: null,
  start_line: 10,
  end_line: 14,
}

describe('RagAnswerPanel', () => {
  beforeEach(() => mockedStream.mockReset())

  it('streams tokens, sources and scrollable citations without rendering HTML', async () => {
    const scrollIntoView = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoView
    mockedStream.mockImplementation(async (_id, _request, callbacks) => {
      if (!callbacks) return
      callbacks.onRetrieval({ trace_id: 'trace', source_count: 1, sources: [source] })
      callbacks.onToken({ trace_id: 'trace', text: '使用 Spring [S1]' })
      callbacks.onDone({
        trace_id: 'trace',
        finish_reason: 'stop',
        grounded: true,
        valid_citation_count: 1,
        invalid_citation_count: 0,
        retrieval_latency_ms: 1,
        llm_latency_ms: 2,
        total_latency_ms: 3,
      })
    })
    const wrapper = mount(RagAnswerPanel, {
      props: { knowledgeBaseId: 'kb' },
      attachTo: document.body,
    })
    await wrapper.get('input[aria-label="知识库问题"]').setValue('Java 技术')
    await wrapper.get('input[aria-label="问答语言过滤"]').setValue('java')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(mockedStream.mock.calls).toContainEqual([
      'kb',
      { query: 'Java 技术', language: 'java' },
      expect.any(Object),
      expect.any(AbortSignal),
    ])
    expect(wrapper.text()).toContain('使用 Spring [S1]')
    expect(wrapper.text()).toContain('sample.md · V2')
    expect(wrapper.text()).toContain('第 10-14 行')
    expect(wrapper.get('.rag-source-content').text()).toContain('<script>not html</script>')
    expect(wrapper.find('script').exists()).toBe(false)
    await wrapper.get('.rag-citation').trigger('click')
    expect(scrollIntoView).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('shows no-answer and ungrounded states and resets between questions', async () => {
    mockedStream.mockImplementationOnce(async (_id, _request, callbacks) => {
      callbacks.onNoAnswer({ trace_id: 't', message: '知识库中未找到足够相关的信息。' })
      callbacks.onDone({
        trace_id: 't', finish_reason: 'no_answer', grounded: false,
        valid_citation_count: 0, invalid_citation_count: 0,
        retrieval_latency_ms: 1, llm_latency_ms: 0, total_latency_ms: 1,
      })
    })
    const wrapper = mount(RagAnswerPanel, { props: { knowledgeBaseId: 'kb' } })
    await wrapper.get('input[aria-label="知识库问题"]').setValue('unknown')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('知识库中未找到足够相关的信息')

    mockedStream.mockImplementationOnce(async (_id, _request, callbacks) => {
      callbacks.onToken({ trace_id: 't2', text: '无引用回答' })
      callbacks.onDone({
        trace_id: 't2', finish_reason: 'stop', grounded: false,
        valid_citation_count: 0, invalid_citation_count: 0,
        retrieval_latency_ms: 1, llm_latency_ms: 1, total_latency_ms: 2,
      })
    })
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).not.toContain('知识库中未找到足够相关的信息')
    expect(wrapper.text()).toContain('该回答未包含有效引用')
    wrapper.unmount()
  })

  it('aborts a generation without displaying a service error', async () => {
    let signal: AbortSignal | undefined
    mockedStream.mockImplementation(
      async (_id, _request, _callbacks, currentSignal) =>
        currentSignal
          ? await new Promise<void>((_resolve, reject) => {
          signal = currentSignal
          currentSignal.addEventListener('abort', () =>
            reject(new DOMException('stopped', 'AbortError')),
          )
            })
          : undefined,
    )
    const wrapper = mount(RagAnswerPanel, { props: { knowledgeBaseId: 'kb' } })
    await wrapper.get('input[aria-label="知识库问题"]').setValue('question')
    void wrapper.get('form').trigger('submit')
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '停止生成')?.trigger('click')
    await flushPromises()
    expect(signal?.aborted).toBe(true)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    wrapper.unmount()

    const second = mount(RagAnswerPanel, { props: { knowledgeBaseId: 'kb' } })
    await second.get('input[aria-label="知识库问题"]').setValue('question')
    void second.get('form').trigger('submit')
    await flushPromises()
    second.unmount()
    expect(signal?.aborted).toBe(true)
  })
})
