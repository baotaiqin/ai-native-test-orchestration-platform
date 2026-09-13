import { http } from '@/api/http'
import type {
  Scenario, ScenarioAiBaseline, ScenarioCleanupOutcome, ScenarioDsl, ScenarioExecutionPreview,
  ScenarioPreviewMode, ScenarioPreviewProfile, ScenarioValidationResult, ScenarioVersion,
} from '@/types/scenario'
import type { RuntimeResponseSnapshot } from '@/types/test-case'

export async function getScenarios(projectId: number): Promise<Scenario[]> {
  const response = await http.get<{ items: Scenario[] }>('/scenarios', {
    params: { project_id: projectId },
  })
  return response.data.items
}

export async function getScenario(id: number): Promise<Scenario> {
  const response = await http.get<Scenario>(`/scenarios/${id}`)
  return response.data
}

export async function deleteScenario(id: number): Promise<void> {
  await http.delete(`/scenarios/${id}`)
}

export async function getScenarioAiBaseline(id: number): Promise<ScenarioAiBaseline> {
  const response = await http.get<ScenarioAiBaseline>(`/scenarios/${id}/ai-baseline`)
  return response.data
}

export async function getScenarioPreviewProfile(id: number): Promise<ScenarioPreviewProfile> {
  const response = await http.get<ScenarioPreviewProfile>(`/scenarios/${id}/preview-profile`)
  return response.data
}

export async function getScenarioPreviewDefaults(id: number): Promise<ScenarioPreviewProfile> {
  const response = await http.get<ScenarioPreviewProfile>(`/scenarios/${id}/preview-profile/defaults`)
  return response.data
}

export async function saveScenarioPreviewProfile(
  id: number,
  profile: Pick<ScenarioPreviewProfile, 'scenario_version_id' | 'context' | 'responses_by_node' | 'cleanup_outcome'>,
): Promise<ScenarioPreviewProfile> {
  const response = await http.put<ScenarioPreviewProfile>(`/scenarios/${id}/preview-profile`, profile)
  return response.data
}

export async function approveScenario(id: number): Promise<Scenario> {
  const response = await http.post<Scenario>(`/scenarios/${id}/approve`)
  return response.data
}

export async function validateScenario(
  projectId: number, name: string, dsl: ScenarioDsl,
): Promise<ScenarioValidationResult> {
  const response = await http.post<ScenarioValidationResult>('/scenarios/validate', {
    project_id: projectId, name, dsl,
  })
  return response.data
}

export async function createScenario(
  projectId: number, name: string, dsl: ScenarioDsl,
): Promise<Scenario> {
  const response = await http.post<Scenario>('/scenarios', {
    project_id: projectId, name, dsl, change_note: '创建 Scenario',
  })
  return response.data
}

export async function createScenarioVersion(
  id: number, name: string, dsl: ScenarioDsl, changeNote: string,
): Promise<ScenarioVersion> {
  const response = await http.post<ScenarioVersion>(`/scenarios/${id}/versions`, {
    name, dsl, change_note: changeNote,
  })
  return response.data
}

export async function getScenarioVersions(id: number): Promise<ScenarioVersion[]> {
  const response = await http.get<ScenarioVersion[]>(`/scenarios/${id}/versions`)
  return response.data
}

export async function executeScenarioPreview(
  projectId: number, dsl: ScenarioDsl, context: Record<string, unknown>,
  responsesByNode: Record<string, RuntimeResponseSnapshot> = {},
  cleanupOutcome: ScenarioCleanupOutcome = 'SUCCESS',
  mode: ScenarioPreviewMode = 'FULL',
  targetNodeId?: string,
): Promise<ScenarioExecutionPreview> {
  const response = await http.post<ScenarioExecutionPreview>('/scenarios/execute-preview', {
    project_id: projectId,
    dsl,
    context,
    responses_by_node: responsesByNode,
    cleanup_outcome: cleanupOutcome,
    mode,
    ...(targetNodeId ? { target_node_id: targetNodeId } : {}),
  })
  return response.data
}
