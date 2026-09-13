import { http } from './http'
import type {
  TestPlan,
  TestPlanRun,
  TestPlanRunnerOption,
  TestPlanRunStart,
  TestPlanValidation,
  TestPlanWrite,
} from '@/types/test-plan'

export async function getTestPlans(projectId: number): Promise<TestPlan[]> {
  const response = await http.get<{ items: TestPlan[] }>('/test-plans', {
    params: { project_id: projectId },
  })
  return response.data.items
}

export async function createTestPlan(payload: TestPlanWrite): Promise<TestPlan> {
  const response = await http.post<TestPlan>('/test-plans', payload)
  return response.data
}

export async function updateTestPlan(id: number, payload: TestPlanWrite): Promise<TestPlan> {
  const response = await http.put<TestPlan>(`/test-plans/${id}`, payload)
  return response.data
}

export async function validateTestPlan(id: number): Promise<TestPlanValidation> {
  const response = await http.post<TestPlanValidation>(`/test-plans/${id}/validate`)
  return response.data
}

export async function runTestPlan(id: number): Promise<TestPlanRunStart> {
  const response = await http.post<TestPlanRunStart>(`/test-plans/${id}/run`)
  return response.data
}

export async function setTestPlanArchived(id: number, archived: boolean): Promise<TestPlan> {
  const action = archived ? 'archive' : 'restore'
  const response = await http.post<TestPlan>(`/test-plans/${id}/${action}`)
  return response.data
}

export async function getTestPlanRuns(projectId: number): Promise<TestPlanRun[]> {
  const response = await http.get<{ items: TestPlanRun[] }>('/test-plans/runs', {
    params: { project_id: projectId },
  })
  return response.data.items
}

export async function cancelTestPlanRun(id: string): Promise<TestPlanRun> {
  const response = await http.post<TestPlanRun>(`/test-plans/runs/${encodeURIComponent(id)}/cancel`)
  return response.data
}

export async function retryTestPlanRun(id: string): Promise<TestPlanRun> {
  const response = await http.post<TestPlanRun>(`/test-plans/runs/${encodeURIComponent(id)}/retry`)
  return response.data
}

export async function getTestPlanRunnerOptions(
  projectId: number,
): Promise<TestPlanRunnerOption[]> {
  const response = await http.get<{ items: TestPlanRunnerOption[] }>('/test-plans/runner-options', {
    params: { project_id: projectId },
  })
  return response.data.items
}
