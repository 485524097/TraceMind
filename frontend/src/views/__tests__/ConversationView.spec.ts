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
import type { RagSource } from '@/types/rag'
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
  version_number: 1,
  chunk_index: 0,
  content_hash: 'a'.repeat(64),
  chunk_type: 'paragraph',
  language: null,
  section_title: '配置',
  page_number: null,
  start_line: 2,
  end_line: 4,
}

function message(
  id: string,
  status: ConversationMessage['status'],
  content: string,
  sources: RagSource[] | null = null,
): ConversationMessage {
  return {
    id,
    conversation_id: 'first',
    role: 'assistant',
    status,
    content,
    trace_id: 'trace',
    sources,
    generation_metadata: { grounded: true },
    created_at: '2026-07-29T01:00:00Z',
  }
}

function detail(conversation: Conversation, messages: ConversationMessage[] = []): ConversationDetail {
  return { ...conversation, messages }
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
    await wrapper.get('details summary').trigger('click')
    expect(wrapper.text()).toContain('guide.md')
    expect(wrapper.text()).toContain('第 2-4 行')
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
})
