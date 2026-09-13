import { http } from '@/api/http'
import type { DashboardResponse } from '@/types/dashboard'

export async function getDashboard(projectId?: number): Promise<DashboardResponse> {
  const response = await http.get<DashboardResponse>('/dashboard', {
    params: projectId ? { project_id: projectId } : undefined,
  })
  return response.data
}
