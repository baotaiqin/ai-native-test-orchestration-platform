import { http } from '@/api/http'
import type {
  ConnectionTestResult,
  DatabaseConnection,
  DatabaseConnectionPayload,
} from '@/types/database-connection'

export async function getDatabaseConnections(
  projectId: number,
  environmentId?: number,
): Promise<DatabaseConnection[]> {
  const response = await http.get<{ items: DatabaseConnection[] }>('/database-connections', {
    params: { project_id: projectId, environment_id: environmentId },
  })
  return response.data.items
}

export async function createDatabaseConnection(
  payload: DatabaseConnectionPayload,
): Promise<DatabaseConnection> {
  const response = await http.post<DatabaseConnection>('/database-connections', payload)
  return response.data
}

export async function updateDatabaseConnection(
  connectionId: number,
  payload: Partial<Omit<DatabaseConnectionPayload, 'project_id' | 'environment_id'>> & {
    enabled?: boolean
  },
): Promise<DatabaseConnection> {
  const response = await http.patch<DatabaseConnection>(
    `/database-connections/${connectionId}`,
    payload,
  )
  return response.data
}

export async function testDatabaseConnection(connectionId: number): Promise<ConnectionTestResult> {
  const response = await http.post<ConnectionTestResult>(
    `/database-connections/${connectionId}/test`,
  )
  return response.data
}
