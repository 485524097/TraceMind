import type { HealthResponse } from '@/types/health'

import { apiRequest } from './api'

export async function fetchHealth(): Promise<HealthResponse> {
  return apiRequest<HealthResponse>('/api/v1/health/live')
}
