import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchHealth } from '@/services/health'
import HomeView from '@/views/HomeView.vue'

vi.mock('@/services/health', () => ({ fetchHealth: vi.fn() }))
const mockedFetchHealth = vi.mocked(fetchHealth)

function mountView() {
  return mount(HomeView)
}

describe('HomeView', () => {
  beforeEach(() => {
    mockedFetchHealth.mockReset()
  })

  it('displays the project name and a healthy backend', async () => {
    mockedFetchHealth.mockResolvedValue({
      status: 'ok',
      service: 'TraceMind API',
      version: '0.1.0',
    })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('TraceMind')
    expect(wrapper.text()).toContain('服务正常')
  })

  it('displays an unavailable state when the request fails', async () => {
    mockedFetchHealth.mockRejectedValue(new Error('network unavailable'))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('服务不可用')
  })

  it('checks the backend again when the button is clicked', async () => {
    mockedFetchHealth.mockRejectedValueOnce(new Error('temporary failure')).mockResolvedValueOnce({
      status: 'ok',
      service: 'TraceMind API',
      version: '0.1.0',
    })
    const wrapper = mountView()
    await flushPromises()

    await wrapper.get('button').trigger('click')
    await flushPromises()

    expect(mockedFetchHealth).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('服务正常')
  })
})
