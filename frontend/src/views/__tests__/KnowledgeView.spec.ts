import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getKnowledgeBase } from '@/services/knowledgeBases'
import { listKnowledgeEntries } from '@/services/knowledgeEntries'
import KnowledgeView from '@/views/KnowledgeView.vue'

const push = vi.fn()
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { knowledgeBaseId: 'kb' } }),
  useRouter: () => ({ push }),
}))
vi.mock('@/services/knowledgeBases', () => ({ getKnowledgeBase: vi.fn() }))
vi.mock('@/services/knowledgeEntries', () => ({ listKnowledgeEntries: vi.fn() }))

describe('KnowledgeView', () => {
  beforeEach(() => {
    push.mockReset()
    vi.mocked(getKnowledgeBase).mockResolvedValue({
      id: 'kb',
      name: 'Backend',
      description: null,
      created_at: '',
      updated_at: '',
    })
    vi.mocked(listKnowledgeEntries).mockResolvedValue({
      items: [
        {
          id: 'entry',
          knowledge_base_id: 'kb',
          question: 'Why did the transaction fail?',
          background: null,
          root_cause: 'Two commits',
          solution: 'Use one transaction',
          failed_attempts: [],
          validation_status: 'verified',
          tags: ['postgres'],
          source_conversation_id: 'conversation',
          source_user_message_id: 'user',
          source_assistant_message_id: 'assistant',
          question_snapshot: 'Why?',
          answer_snapshot: 'Answer',
          sources_snapshot: [],
          generation_metadata_snapshot: null,
          created_at: '2026-08-11T00:00:00Z',
          updated_at: '2026-08-11T00:00:00Z',
        },
      ],
      total: 1,
      offset: 0,
      limit: 100,
      available_tags: ['postgres'],
    })
  })

  it('loads editorial knowledge rows and navigates to detail', async () => {
    const wrapper = mount(KnowledgeView, {
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()
    const row = wrapper.get('[data-testid="knowledge-entry-entry"]')
    expect(row.text()).toContain('Why did the transaction fail?')
    expect(row.text()).toContain('已验证')
    await row.trigger('click')
    expect(push).toHaveBeenCalledWith('/knowledge-bases/kb/knowledge/entry')
  })
})
