import { http } from '@/api/http'
import type { Secret, SecretPayload } from '@/types/secret'

export async function getSecrets(projectId: number, environmentId?: number): Promise<Secret[]> {
  const response = await http.get<{ items: Secret[] }>('/secrets', {
    params: { project_id: projectId, environment_id: environmentId },
  })
  return response.data.items
}

export async function createSecret(payload: SecretPayload): Promise<Secret> {
  const response = await http.post<Secret>('/secrets', payload)
  return response.data
}

export async function updateSecret(
  secretId: number,
  payload: Partial<Pick<Secret, 'name' | 'secret_type' | 'enabled'>>,
): Promise<Secret> {
  const response = await http.patch<Secret>(`/secrets/${secretId}`, payload)
  return response.data
}

export async function rotateSecret(secretId: number, value: string): Promise<Secret> {
  const response = await http.post<Secret>(`/secrets/${secretId}/rotate`, { value })
  return response.data
}
