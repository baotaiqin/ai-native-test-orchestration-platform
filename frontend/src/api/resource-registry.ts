import { http } from '@/api/http'
import type {
  CleanupExecutionResult,
  CleanupPlan,
  ResourceRegistryEntry,
  ResourceRegistryList,
} from '@/types/resource-registry'
import type { CleanupOutcome, CleanupConfig } from '@/types/test-case'

export async function getResourceRegistry(
  projectId: number, runId: string, status?: string,
): Promise<ResourceRegistryList> {
  const response = await http.get<ResourceRegistryList>('/resource-registry', {
    params: { project_id: projectId, run_id: runId, status },
  })
  return response.data
}

export async function registerResource(payload: {
  project_id: number
  run_id: string
  resource_type: string
  resource_id: string
  cleanup: CleanupConfig
}): Promise<ResourceRegistryEntry> {
  const response = await http.post<ResourceRegistryEntry>('/resource-registry', payload)
  return response.data
}

export async function planResourceCleanup(
  projectId: number, runId: string, outcome: CleanupOutcome, retryFailed = false,
): Promise<CleanupPlan> {
  const response = await http.post<CleanupPlan>('/resource-registry/cleanup/plan', {
    project_id: projectId, run_id: runId, outcome, retry_failed: retryFailed,
  })
  return response.data
}

export async function executeResourceCleanup(
  projectId: number, runId: string, outcome: CleanupOutcome, retryFailed = false,
): Promise<CleanupExecutionResult> {
  const response = await http.post<CleanupExecutionResult>('/resource-registry/cleanup/execute', {
    project_id: projectId, run_id: runId, outcome, retry_failed: retryFailed,
  })
  return response.data
}
