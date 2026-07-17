import type { HealthResponse } from '@/types/health'

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(`${apiBaseUrl}/api/v1/health/live`, {
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    throw new Error('Health request failed')
  }
  return (await response.json()) as HealthResponse
}
