import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchHealth } from '@/services/health'
import { listKnowledgeBases } from '@/services/knowledgeBases'
import HomeView from '@/views/HomeView.vue'

vi.mock('@/services/health', () => ({ fetchHealth: vi.fn() }))
vi.mock('@/services/knowledgeBases', () => ({ listKnowledgeBases: vi.fn() }))
const mockedFetchHealth = vi.mocked(fetchHealth)
const mockedListKbs = vi.mocked(listKnowledgeBases)

function mountView() {
  return mount(HomeView, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
}

describe('HomeView', () => {
  beforeEach(() => {
    mockedFetchHealth.mockReset()
    mockedListKbs.mockReset()
    mockedListKbs.mockResolvedValue({ items: [], total: 0, offset: 0, limit: 20 })
  })

  it('displays the project name and product description', async () => {
    mockedFetchHealth.mockResolvedValue({
      status: 'ok',
      service: 'TraceMind API',
      version: '0.1.0',
    })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('TraceMind')
    expect(wrapper.text()).toContain('打开知识库')
  })

  it('shows backend unavailable status when health fails', async () => {
    mockedFetchHealth.mockRejectedValue(new Error('network'))
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('后端服务不可用')
  })

  it('retries backend check on button click', async () => {
    mockedFetchHealth
      .mockRejectedValueOnce(new Error('temp'))
      .mockResolvedValueOnce({ status: 'ok', service: 'TraceMind API', version: '0.1.0' })
    const wrapper = mountView()
    await flushPromises()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(mockedFetchHealth).toHaveBeenCalledTimes(2)
  })

  it('shows recent KBs when backend is healthy', async () => {
    mockedFetchHealth.mockResolvedValue({
      status: 'ok',
      service: 'TraceMind API',
      version: '0.1.0',
    })
    mockedListKbs.mockResolvedValue({
      items: [
        {
          id: '1',
          name: 'MyKB',
          description: null,
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
        },
      ],
      total: 1,
      offset: 0,
      limit: 20,
    })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('最近使用')
    expect(wrapper.text()).toContain('MyKB')
  })
})
