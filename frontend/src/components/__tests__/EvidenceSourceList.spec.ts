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
  symbol_kind: 'method',
  symbol_name: 'run',
  symbol_qualified_name: 'Service.run',
  symbol_signature: 'void run()',
}

describe('EvidenceSourceList', () => {
  it('renders shared code evidence with stable citation identity', () => {
    const wrapper = mount(EvidenceSourceList, {
      props: { sources: [source], identityPrefix: 'answer' },
    })
    const item = wrapper.get('[data-testid="evidence-source-answer-S1"]')
    expect(item.text()).toContain('Code')
    expect(item.text()).toContain('L10–12')
    expect(item.text()).toContain('void run()')
  })
})
