import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import EvidenceSourceList from '@/components/EvidenceSourceList.vue'
import type { EvidenceSource } from '@/types/evidence'

const source: EvidenceSource = {
  source_id: 'S1',
  document_id: 'document',
  document_version_id: 'version',
  chunk_id: 'chunk',
  document_name: 'Service.java',
  relative_path: 'src/Service.java',
  version_number: 1,
  chunk_index: 2,
  content: 'void run() {}',
  content_hash: 'a'.repeat(64),
  chunk_type: 'code',
  language: 'java',
  section_title: null,
  page_number: null,
  start_line: 10,
  end_line: 12,
}

describe('EvidenceSourceList', () => {
  it('renders shared code evidence with stable citation identity', () => {
    const wrapper = mount(EvidenceSourceList, {
      props: { sources: [source], identityPrefix: 'answer' },
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    const item = wrapper.get('[data-testid="evidence-source-answer-S1"]')
    expect(item.text()).toContain('代码')
    expect(item.text()).toContain('第 10–12 行')
    expect(item.text()).toContain('void run()')
  })

  it('renders verified knowledge as a distinct traceable source', () => {
    const wrapper = mount(EvidenceSourceList, {
      props: {
        sources: [
          {
            source_id: 'S2',
            source_type: 'knowledge_entry',
            knowledge_base_id: 'kb',
            knowledge_entry_id: 'entry',
            knowledge_question: '事务为什么失败？',
            knowledge_updated_at: '2026-08-14T00:00:00Z',
            chunk_id: 'knowledge-chunk',
            chunk_index: 0,
            content: '把写操作放进同一个事务。',
            content_hash: 'b'.repeat(64),
            chunk_type: 'knowledge_entry',
            language: null,
            section_title: 'Solution',
            page_number: null,
            start_line: null,
            end_line: null,
          },
        ],
      },
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    expect(wrapper.text()).toContain('知识')
    expect(wrapper.text()).toContain('已验证知识')
    expect(wrapper.text()).toContain('事务为什么失败？')
  })
})
