import { mount } from '@vue/test-utils'
import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AppShell from '@/components/AppShell.vue'

const routeState = { params: {} as Record<string, string | undefined> }

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
  RouterLink: {
    props: ['to'],
    template: '<a :data-to="to"><slot /></a>',
  },
}))

describe('AppShell', () => {
  beforeEach(() => {
    routeState.params = {}
  })

  it('renders the global navigation without knowledge-base tabs', () => {
    const wrapper = mount(AppShell, { slots: { default: '<p>Page content</p>' } })

    expect(wrapper.text()).toContain('TraceMind')
    expect(wrapper.text()).toContain('Knowledge Bases')
    expect(wrapper.text()).toContain('Page content')
    expect(wrapper.find('.kb-bar').exists()).toBe(false)
  })

  it('renders scoped navigation and the injected knowledge-base name', () => {
    routeState.params = { knowledgeBaseId: 'kb-1' }
    const wrapper = mount(AppShell, {
      global: { provide: { shellKbName: ref('Project KB') } },
    })

    expect(wrapper.get('.kb-name').text()).toBe('Project KB')
    expect(wrapper.get('.kb-tab[data-to="/knowledge-bases/kb-1/documents"]').text()).toBe('Documents')
    expect(wrapper.get('.kb-tab[data-to="/knowledge-bases/kb-1/chat"]').text()).toBe('Ask')
  })
})
