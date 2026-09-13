import { http } from './http'
import type { SystemHealth } from '@/types/system'

export async function getSystemHealth(): Promise<SystemHealth> {
  const response = await http.get<SystemHealth>('/system/health')
  return response.data
}

