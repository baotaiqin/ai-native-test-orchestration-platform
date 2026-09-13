import { http } from '@/api/http'
import type {
  ApiDefinition,
  ApiDesignSuggestion,
  ApiDesignSuggestionKind,
  ApiScenarioPlan,
  OpenApiImportResult,
  OpenApiPreview,
} from '@/types/api-definition'

export interface OpenApiImportPayload {
  project_id: number
  filename: string
  content: string
  mark_missing_removed?: boolean
}

export async function previewOpenApi(payload: OpenApiImportPayload): Promise<OpenApiPreview> {
  const response = await http.post<OpenApiPreview>('/api-definitions/import/preview', payload)
  return response.data
}

export async function importOpenApi(
  payload: OpenApiImportPayload,
): Promise<OpenApiImportResult> {
  const response = await http.post<OpenApiImportResult>('/api-definitions/import', payload)
  return response.data
}

export async function getApiDefinitions(projectId: number): Promise<ApiDefinition[]> {
  const response = await http.get<{ items: ApiDefinition[] }>('/api-definitions', {
    params: { project_id: projectId },
  })
  return response.data.items
}

export async function getApiDesignSuggestions(
  importId: number,
): Promise<ApiDesignSuggestion[]> {
  const response = await http.get<{ items: ApiDesignSuggestion[] }>(
    `/api-definitions/imports/${importId}/ai-suggestions`,
  )
  return response.data.items
}

export async function getApiScenarioPlans(importId: number): Promise<ApiScenarioPlan[]> {
  const response = await http.get<{ items: ApiScenarioPlan[] }>(
    `/api-definitions/imports/${importId}/scenario-plans`,
  )
  return response.data.items
}

export async function createApiScenarioPlan(
  importId: number,
  payload: {
    prompt_id: number
    requirement_document_version_id: number
    requirement_id?: number
    additional_instructions?: string
  },
): Promise<ApiScenarioPlan> {
  const response = await http.post<ApiScenarioPlan>(
    `/api-definitions/imports/${importId}/scenario-plans`, payload,
  )
  return response.data
}

export async function generateApiScenarioPlanItems(
  planId: number,
  payload: { item_ids: number[]; prompt_id: number; additional_instructions?: string },
): Promise<ApiDesignSuggestion[]> {
  const response = await http.post<{ suggestions: ApiDesignSuggestion[] }>(
    `/api-definitions/scenario-plans/${planId}/generate`, payload,
  )
  return response.data.suggestions
}

export async function generateApiDesignSuggestion(
  importId: number,
  payload: {
    kind: ApiDesignSuggestionKind
    prompt_id: number
    requirement_document_version_id?: number
    requirement_id?: number
    additional_instructions?: string
  },
): Promise<ApiDesignSuggestion> {
  const response = await http.post<ApiDesignSuggestion>(
    `/api-definitions/imports/${importId}/ai-suggestions`, payload,
  )
  return response.data
}

export async function editApiDesignSuggestion(
  suggestionId: number,
  humanResult: Record<string, unknown>,
  decisionNote?: string,
): Promise<ApiDesignSuggestion> {
  const response = await http.patch<ApiDesignSuggestion>(
    `/api-definitions/ai-suggestions/${suggestionId}`,
    { human_result: humanResult, decision_note: decisionNote },
  )
  return response.data
}

export async function decideApiDesignSuggestion(
  suggestionId: number,
  action: 'ACCEPT' | 'REJECT',
  decisionNote?: string,
): Promise<ApiDesignSuggestion> {
  const response = await http.post<ApiDesignSuggestion>(
    `/api-definitions/ai-suggestions/${suggestionId}/decision`,
    { action, decision_note: decisionNote },
  )
  return response.data
}
