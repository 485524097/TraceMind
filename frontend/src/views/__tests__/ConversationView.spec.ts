import { flushPromises, mount } from '@vue/test-utils'
import { ElMessageBox } from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  renameConversation,
} from '@/services/conversations'
import { streamRagAnswer } from '@/services/rag'
import { getKnowledgeBase } from '@/services/knowledgeBases'
import type { Conversation, ConversationDetail, ConversationMessage } from '@/types/conversation'
import type { RagDoneEvent, RagSource } from '@/types/rag'
import ConversationView from '@/views/ConversationView.vue'

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { knowledgeBaseId: 'kb' } }),
  RouterLink: { template: '<a><slot /></a>' },
}))
vi.mock('@/services/conversations', () => ({
  listConversations: vi.fn(),
  createConversation: vi.fn(),
  getConversation: vi.fn(),
  renameConversation: vi.fn(),
  deleteConversation: vi.fn(),
}))
vi.mock('@/services/rag', () => ({ streamRagAnswer: vi.fn() }))
vi.mock('@/services/knowledgeBases', () => ({ getKnowledgeBase: vi.fn() }))

const mockedList = vi.mocked(listConversations)
const mockedCreate = vi.mocked(createConversation)
const mockedGet = vi.mocked(getConversation)
const mockedRename = vi.mocked(renameConversation)
const mockedDelete = vi.mocked(deleteConversation)
const mockedStream = vi.mocked(streamRagAnswer)
const mockedKnowledgeBase = vi.mocked(getKnowledgeBase)

const first: Conversation = {
  id: 'first',
  knowledge_base_id: 'kb',
  title: '第一会话',
  created_at: '2026-07-29T00:00:00Z',
  updated_at: '2026-07-29T01:00:00Z',
}
const second: Conversation = {
  ...first,
  id: 'second',
  title: '第二会话',
  updated_at: '2026-07-29T00:30:00Z',
}
const source: RagSource = {
  source_id: 'S1',
  score: 0.9,
  content: '引用快照',
  knowledge_base_id: 'kb',
  document_id: 'doc',
  document_version_id: 'version',
  chunk_id: 'chunk',
  index_generation: 'generation',
  document_name: 'guide.md',
  relative_path: 'docs/guide.md',
  version_number: 1,
  chunk_index: 0,
  content_hash: 'a'.repeat(64),
  chunk_type: 'paragraph',
  language: null,
  section_title: '配置',
  page_number: null,
  start_line: 2,
  end_line: 4,
  symbol_kind: 'method',
  symbol_name: 'configure',
  symbol_qualified_name: 'demo.Guide.configure',
  symbol_signature: 'void configure()',
}

function message(
  id: string,
  status: ConversationMessage['status'],
  content: string,
  sources: RagSource[] | null = null,
  generationMetadata: ConversationMessage['generation_metadata'] = { grounded: true },
): ConversationMessage {
  return {
    id,
    conversation_id: 'first',
    role: 'assistant',
    status,
    content,
    trace_id: 'trace',
    sources,
    generation_metadata: generationMetadata,
    created_at: '2026-07-29T01:00:00Z',
  }
}

function detail(conversation: Conversation, messages: ConversationMessage[] = []): ConversationDetail {
  return { ...conversation, messages }
}

function doneEvent(overrides: Partial<RagDoneEvent> = {}): RagDoneEvent {
  return {
    trace_id: 'trace',
    finish_reason: 'stop',
    grounded: true,
    valid_citation_count: 1,
    invalid_citation_count: 0,
    retrieval_latency_ms: 20,
    llm_first_token_latency_ms: 30,
    llm_latency_ms: 40,
    total_latency_ms: 60,
    retrieval_mode: 'hybrid_reranker',
    rerank_latency_ms: 10,
    reranker_fallback: false,
    query_rewrite_mode: 'rewritten',
    query_rewrite_latency_ms: 5,
    history_turn_count: 1,
    source_count: 1,
    ...overrides,
  }
}

describe('ConversationView', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    mockedList.mockReset()
    mockedCreate.mockReset()
    mockedGet.mockReset()
    mockedRename.mockReset()
    mockedDelete.mockReset()
    mockedStream.mockReset()
    mockedKnowledgeBase.mockResolvedValue({
      id: 'kb',
      name: '测试知识库',
      description: null,
      created_at: first.created_at,
      updated_at: first.updated_at,
    })
    mockedList.mockResolvedValue({ items: [first, second], total: 2, offset: 0, limit: 100 })
    mockedGet.mockImplementation(async (_kb, id) =>
      id === first.id ? detail(first) : detail(second),
    )
  })

  it('loads conversation list and restores message history with citation sources', async () => {
    const history = message('answer', 'completed', '查看配置 [S1]', [source])
    mockedGet.mockResolvedValue(detail(first, [history]))
    const wrapper = mount(ConversationView)
    await flushPromises()

    expect(mockedList).toHaveBeenCalledWith('kb')
    expect(mockedGet).toHaveBeenCalledWith('kb', 'first')
    expect(wrapper.text()).toContain('测试知识库')
    expect(wrapper.text()).toContain('查看配置 [S1]')
    expect(wrapper.text()).toContain('引用来源（1）')
    const assistantMessage = wrapper.get('article.conversation-message.assistant')
    const sources = assistantMessage.get('[data-testid="conversation-sources"]')
    expect(sources.attributes('data-message-id')).toBe('answer')
    expect(sources.attributes('open')).toBeUndefined()
    await wrapper.get('details summary').trigger('click')
    expect(wrapper.text()).toContain('docs/guide.md')
    expect(wrapper.text()).toContain('void configure()')
    expect(wrapper.text()).toContain('第 2-4 行')
  })

  it('uses a full-height stretch layout with an independently scrolling message area', async () => {
    const wrapper = mount(ConversationView)
    await flushPromises()

    const layout = wrapper.get('[data-testid="conversation-layout"]')
    const main = wrapper.get('.conversation-main')
    const thread = main.get('[data-testid="conversation-thread"]')
    const composer = main.get('[data-testid="conversation-composer"]')
    expect(wrapper.get('.conversation-page').attributes('data-layout')).toBe('viewport-grid')
    expect(layout.attributes('data-layout')).toBe('stretch-columns')
    expect(thread.attributes('data-scroll-region')).toBe('true')
    expect(composer.attributes('data-position')).toBe('panel-bottom')
    expect(thread.element.parentElement).toBe(main.element)
    expect(composer.element.parentElement).toBe(main.element)
    expect(
      thread.element.compareDocumentPosition(composer.element) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()

  })

  it('creates, renames and deletes conversations', async () => {
    mockedCreate.mockResolvedValue({ ...first, id: 'created', title: '新会话' })
    mockedGet.mockResolvedValue(detail({ ...first, id: 'created', title: '新会话' }))
    const wrapper = mount(ConversationView)
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '新建会话')?.trigger('click')
    await flushPromises()
    expect(mockedCreate).toHaveBeenCalledWith('kb')

    vi.spyOn(ElMessageBox, 'prompt').mockResolvedValue({ value: '重命名后' } as never)
    mockedRename.mockResolvedValue({ ...first, id: 'created', title: '重命名后' })
    await wrapper.findAll('button').find((button) => button.text() === '重命名')?.trigger('click')
    await flushPromises()
    expect(mockedRename).toHaveBeenCalledWith('kb', 'created', '重命名后')

    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as never)
    mockedDelete.mockResolvedValue(undefined)
    mockedList.mockResolvedValue({ items: [], total: 0, offset: 0, limit: 100 })
    await wrapper.findAll('button').find((button) => button.text() === '删除')?.trigger('click')
    await flushPromises()
    expect(mockedDelete).toHaveBeenCalledWith('kb', 'created')
  })

  it('writes SSE tokens only to the active conversation and aborts on switch', async () => {
    let callbacks: Parameters<typeof streamRagAnswer>[2] | undefined
    let signal: AbortSignal | undefined
    mockedStream.mockImplementation(async (_kb, _body, handlers, currentSignal) => {
      callbacks = handlers
      signal = currentSignal
      return await new Promise<void>((resolve) => {
        currentSignal?.addEventListener('abort', () => resolve())
      })
    })
    const wrapper = mount(ConversationView)
    await flushPromises()
    await wrapper.get('input[aria-label="知识库问题"]').setValue('问题')
    void wrapper.get('.conversation-composer').trigger('submit')
    await flushPromises()
    callbacks?.onToken({ trace_id: 'trace', text: '第一会话 token' })
    await flushPromises()
    expect(wrapper.text()).toContain('第一会话 token')

    await wrapper.findAll('.conversation-list-item')[1]?.trigger('click')
    await flushPromises()
    expect(signal?.aborted).toBe(true)
    callbacks?.onToken({ trace_id: 'trace', text: '错误写入' })
    await flushPromises()
    expect(wrapper.text()).not.toContain('错误写入')
    expect(mockedGet).toHaveBeenCalledWith('kb', 'second')
  })

  it('shows immediate progress, retrieval count, generation, finalization and completion', async () => {
    let callbacks: Parameters<typeof streamRagAnswer>[2] | undefined
    let resolveStream: (() => void) | undefined
    mockedStream.mockImplementation(async (_kb, _body, handlers) => {
      callbacks = handlers
      await new Promise<void>((resolve) => {
        resolveStream = resolve
      })
    })
    const wrapper = mount(ConversationView)
    await flushPromises()
    await wrapper.get('input[aria-label="知识库问题"]').setValue('Nacos 如何配置？')
    void wrapper.get('.conversation-composer').trigger('submit')
    await flushPromises()

    const progress = wrapper.get('[data-testid="conversation-progress"]')
    expect(progress.attributes('data-state')).toBe('preparing')
    expect(progress.text()).toContain('正在理解问题并检索知识库')
    expect(progress.text()).toContain('已用时 0 秒')
    expect(wrapper.get('[data-testid="stop-generation"]').isVisible()).toBe(true)

    callbacks?.onRetrieval({ trace_id: 'trace', source_count: 1, sources: [source] })
    await flushPromises()
    expect(progress.attributes('data-state')).toBe('retrieved')
    expect(progress.text()).toContain('已找到 1 条来源，正在生成回答')

    callbacks?.onToken({ trace_id: 'trace', text: '回答 [S1]' })
    await flushPromises()
    expect(progress.attributes('data-state')).toBe('generating')
    expect(progress.text()).toContain('正在生成回答')

    callbacks?.onDone(doneEvent())
    await flushPromises()
    expect(progress.attributes('data-state')).toBe('finalizing')
    expect(progress.text()).toContain('正在校验引用并保存')

    resolveStream?.()
    await flushPromises()
    expect(progress.attributes('data-state')).toBe('completed')
    expect(progress.text()).toContain('已完成')
  })

  it('marks an explicitly stopped generation as cancelled', async () => {
    mockedStream.mockImplementation(
      async (_kb, _body, _handlers, signal) =>
        await new Promise<void>((resolve) => signal?.addEventListener('abort', () => resolve())),
    )
    const wrapper = mount(ConversationView)
    await flushPromises()
    await wrapper.get('input[aria-label="知识库问题"]').setValue('停止测试')
    void wrapper.get('.conversation-composer').trigger('submit')
    await flushPromises()

    await wrapper.get('[data-testid="stop-generation"]').trigger('click')
    await flushPromises()
    const progress = wrapper.get('[data-testid="conversation-progress"]')
    expect(progress.attributes('data-state')).toBe('cancelled')
    expect(progress.text()).toContain('已取消')
  })

  it('keeps new-conversation entry points visible when there is no conversation on narrow screens', async () => {
    window.innerWidth = 600
    window.dispatchEvent(new Event('resize'))
    mockedList.mockResolvedValue({ items: [], total: 0, offset: 0, limit: 100 })
    const wrapper = mount(ConversationView)
    await flushPromises()

    expect(wrapper.get('[data-testid="new-conversation-sidebar"]').isVisible()).toBe(true)
    expect(wrapper.get('[data-testid="new-conversation-empty"]').isVisible()).toBe(true)
    expect(wrapper.get('.conversation-list-scroll').classes()).toContain('conversation-list-scroll')
  })

  it('keeps the new-conversation and composer entries at a low viewport height', async () => {
    window.innerWidth = 1366
    window.innerHeight = 768
    window.dispatchEvent(new Event('resize'))
    const wrapper = mount(ConversationView)
    await flushPromises()

    expect(wrapper.get('[data-testid="new-conversation-sidebar"]').isVisible()).toBe(true)
    expect(wrapper.get('[data-testid="conversation-composer"]').isVisible()).toBe(true)
    expect(wrapper.get('input[aria-label="知识库问题"]').isVisible()).toBe(true)
  })

  it('renders completed, no-answer, failed and cancelled states', async () => {
    mockedGet.mockResolvedValue(
      detail(first, [
        message('completed', 'completed', 'ok'),
        message('none', 'no_answer', 'none'),
        message('failed', 'failed', 'failed'),
        message('cancelled', 'cancelled', ''),
      ]),
    )
    const wrapper = mount(ConversationView)
    await flushPromises()
    expect(wrapper.text()).toContain('已完成')
    expect(wrapper.text()).toContain('无答案')
    expect(wrapper.text()).toContain('生成失败')
    expect(wrapper.text()).toContain('已取消')
  })

  it('shows exact rewrite labels and verifiable execution details', async () => {
    mockedGet.mockResolvedValue(
      detail(first, [
        message('skipped', 'completed', 'independent answer', null, {
          ...doneEvent({
            query_rewrite_mode: 'skipped',
            history_turn_count: 2,
            retrieval_query: 'PostgreSQL 如何配置？',
            path_scope_mode: 'exact',
            scoped_relative_path: 'src/main/java/demo/UserService.java',
          }),
        }),
        message('rewritten', 'completed', 'history answer', null, {
          ...doneEvent({ query_rewrite_mode: 'rewritten', history_turn_count: 2 }),
        }),
        message('fallback', 'completed', 'fallback answer', null, {
          ...doneEvent({
            query_rewrite_mode: 'fallback',
            history_turn_count: 1,
            reranker_fallback: true,
          }),
        }),
      ]),
    )
    const wrapper = mount(ConversationView)
    await flushPromises()

    expect(wrapper.text()).not.toContain('已结合对话历史')
    expect(wrapper.text()).toContain('独立问题，未改写')
    expect(wrapper.text()).toContain('已根据对话历史改写检索问题')
    expect(wrapper.text()).toContain('查询改写失败，已使用原问题')
    expect(wrapper.text()).toContain('处理详情')
    expect(wrapper.text()).toContain('hybrid_reranker')
    expect(wrapper.text()).toContain('首 Token 延迟')
    expect(wrapper.text()).toContain('30 ms')
    expect(wrapper.text()).toContain('来源数量')
    expect(wrapper.text()).toContain('路径限定')
    expect(wrapper.text()).toContain('src/main/java/demo/UserService.java')
    expect(wrapper.text()).not.toContain('Conversation History')
  })

  it('restores exact and fallback symbol scopes from persisted history', async () => {
    mockedGet.mockResolvedValue(
      detail(first, [
        message('exact', 'completed', 'exact', [{ ...source, ranking_mode: 'symbol_exact' }], {
          ...doneEvent(),
          symbol_scope_mode: 'exact',
          scoped_symbol_kind: 'method',
          scoped_symbol_qualified_name: 'demo.UserService.source',
          scoped_symbol_signature: 'source(String)',
        }),
        message('fallback', 'failed', 'fallback', null, {
          ...doneEvent(),
          symbol_scope_mode: 'fallback',
          symbol_scope_reason: 'ambiguous',
        }),
        message('legacy', 'cancelled', 'legacy', null, { grounded: false }),
      ]),
    )
    const wrapper = mount(ConversationView)
    await flushPromises()

    expect(wrapper.text()).toContain('精确符号：source(String)')
    expect(wrapper.text()).toContain('符号存在歧义，已回退普通检索')
    expect(wrapper.text()).toContain('精确符号命中')
    expect(wrapper.text()).toContain('docs/guide.md')
    expect(wrapper.text()).toContain('void configure()')
    expect(wrapper.text()).toContain('第 2-4 行')
    expect(wrapper.html()).not.toContain('symbol_lookup')
  })

  it('does not render missing or damaged symbol scope metadata', async () => {
    mockedGet.mockResolvedValue(
      detail(first, [
        message('legacy', 'completed', 'legacy', null, { grounded: true }),
        message('damaged', 'completed', 'damaged', null, {
          grounded: true,
          symbol_scope_mode: 'internal-value' as never,
          symbol_scope_reason: 'private-error' as never,
        }),
      ]),
    )
    const wrapper = mount(ConversationView)
    await flushPromises()
    expect(wrapper.text()).not.toContain('符号限定')
    expect(wrapper.text()).not.toContain('internal-value')
    expect(wrapper.text()).not.toContain('private-error')
  })
})
