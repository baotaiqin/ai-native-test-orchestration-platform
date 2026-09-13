import { http } from '@/api/http'
import type {
  DemoBootstrapPayload,
  DemoBootstrapResponse,
  DemoBootstrapStatus,
  DemoResetPreview,
  DemoResetResponse,
} from '@/types/demo'

export async function getDemoStatus(): Promise<DemoBootstrapStatus> {
  const response = await http.get<DemoBootstrapStatus>('/demo/status')
  return response.data
}

export async function bootstrapDemo(payload: DemoBootstrapPayload): Promise<DemoBootstrapResponse> {
  const response = await http.post<DemoBootstrapResponse>('/demo/bootstrap', payload, {
    timeout: 90_000,
  })
  return response.data
}

export async function getDemoResetPreview(): Promise<DemoResetPreview> {
  const response = await http.get<DemoResetPreview>('/demo/reset-preview')
  return response.data
}

export async function resetDemo(confirmation: string): Promise<DemoResetResponse> {
  const response = await http.post<DemoResetResponse>('/demo/reset', { confirmation }, {
    timeout: 90_000,
  })
  return response.data
}
