import { http } from '@/api/http'
import type {
  Environment,
  EnvironmentPayload,
  EnvironmentVariable,
  VariablePayload,
} from '@/types/environment'

export async function getEnvironments(projectId: number): Promise<Environment[]> {
  const response = await http.get<{ items: Environment[] }>('/environments', {
    params: { project_id: projectId },
  })
  return response.data.items
}

export async function createEnvironment(payload: EnvironmentPayload): Promise<Environment> {
  const response = await http.post<Environment>('/environments', payload)
  return response.data
}

export async function updateEnvironment(
  environmentId: number,
  payload: Partial<Omit<EnvironmentPayload, 'project_id'>> & { enabled?: boolean },
): Promise<Environment> {
  const response = await http.patch<Environment>(`/environments/${environmentId}`, payload)
  return response.data
}

export async function setDefaultEnvironment(environmentId: number): Promise<Environment> {
  const response = await http.post<Environment>(`/environments/${environmentId}/default`)
  return response.data
}

export async function getVariables(environmentId: number): Promise<EnvironmentVariable[]> {
  const response = await http.get<{ items: EnvironmentVariable[] }>(
    `/environments/${environmentId}/variables`,
  )
  return response.data.items
}

export async function saveVariable(
  environmentId: number,
  key: string,
  payload: VariablePayload,
): Promise<EnvironmentVariable> {
  const response = await http.put<EnvironmentVariable>(
    `/environments/${environmentId}/variables/${encodeURIComponent(key)}`,
    payload,
  )
  return response.data
}

export async function deleteVariable(environmentId: number, key: string): Promise<void> {
  await http.delete(`/environments/${environmentId}/variables/${encodeURIComponent(key)}`)
}
