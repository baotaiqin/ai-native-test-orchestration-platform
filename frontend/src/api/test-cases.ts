import { AI_GENERATION_REQUEST_TIMEOUT_MS, http } from '@/api/http'
import type {
  CaseDesignPlan, CaseDesignTask, CaseDesignTaskPage, CaseGeneration, CaseGenerationRecompileResult, CaseGenerationTask, CaseSuggestion, RequirementCaseLink, RuntimePreviewRequest,
  RuntimePreviewResult, SuggestedCase, TestCaseAsset, TestCaseVersion,
} from '@/types/test-case'

export async function getCaseGenerations(requirementId: number): Promise<CaseGeneration[]> {
  const response = await http.get<{ items: CaseGeneration[] }>(
    `/test-cases/requirements/${requirementId}/generations`,
  )
  return response.data.items
}

export async function generateCaseSuggestions(
  requirementId: number, payload: { prompt_id: number; additional_instructions?: string },
): Promise<CaseGeneration> {
  const response = await http.post<CaseGeneration>(
    `/test-cases/requirements/${requirementId}/generations`, payload,
    { timeout: AI_GENERATION_REQUEST_TIMEOUT_MS },
  )
  return response.data
}

export async function recompileCaseGeneration(
  generationId: number,
): Promise<CaseGenerationRecompileResult> {
  const response = await http.post<CaseGenerationRecompileResult>(
    `/test-cases/generations/${generationId}/recompile`,
  )
  return response.data
}

export async function getCaseGenerationTasks(
  requirementId: number,
): Promise<CaseGenerationTask[]> {
  const response = await http.get<{ items: CaseGenerationTask[] }>(
    `/test-cases/requirements/${requirementId}/generation-tasks`,
  )
  return response.data.items
}

export async function getCaseDesignPlan(
  requirementId: number, includeApiIds: number[] = [],
): Promise<CaseDesignPlan> {
  const params = new URLSearchParams()
  includeApiIds.forEach((id) => params.append('include_api_ids', String(id)))
  const response = await http.get<CaseDesignPlan>(
    `/test-cases/requirements/${requirementId}/design-plan`,
    { params },
  )
  return response.data
}

export async function getCaseDesignTasks(requirementId: number): Promise<CaseDesignTask[]> {
  const response = await http.get<CaseDesignTaskPage>(
    `/test-cases/requirements/${requirementId}/design-tasks`,
  )
  return response.data.items
}

export async function getProjectCaseDesignTasks(
  projectId: number, page = 1, pageSize = 10,
): Promise<CaseDesignTaskPage> {
  const response = await http.get<CaseDesignTaskPage>(
    `/test-cases/projects/${projectId}/design-tasks`,
    { params: { page, page_size: pageSize } },
  )
  return response.data
}

export async function createCaseDesignTask(
  requirementId: number,
  payload: { prompt_id: number; include_api_ids: number[]; force_refresh?: boolean },
): Promise<CaseDesignTask> {
  const response = await http.post<CaseDesignTask>(
    `/test-cases/requirements/${requirementId}/design-tasks`, payload,
  )
  return response.data
}

export async function deleteCaseDesignTask(taskId: number): Promise<void> {
  await http.delete(`/test-cases/design-tasks/${taskId}`)
}

export async function createCaseGenerationTask(
  requirementId: number, payload: {
    prompt_id: number
    additional_instructions?: string
    selected_api_definition_ids: number[]
    design_task_id?: number
  },
): Promise<CaseGenerationTask> {
  const response = await http.post<CaseGenerationTask>(
    `/test-cases/requirements/${requirementId}/generation-tasks`, payload,
  )
  return response.data
}

export async function editCaseSuggestion(
  id: number, humanResult: SuggestedCase, decisionNote?: string,
): Promise<CaseSuggestion> {
  const response = await http.patch<CaseSuggestion>(`/test-cases/suggestions/${id}`, {
    human_result: humanResult, decision_note: decisionNote,
  })
  return response.data
}

export async function decideCaseSuggestion(
  id: number, action: 'ACCEPT' | 'REJECT', decisionNote?: string,
): Promise<CaseSuggestion> {
  const response = await http.post<CaseSuggestion>(
    `/test-cases/suggestions/${id}/decision`, { action, decision_note: decisionNote },
  )
  return response.data
}

export async function bulkDecideCaseSuggestions(
  ids: number[], action: 'ACCEPT' | 'REJECT', decisionNote?: string,
): Promise<CaseSuggestion[]> {
  const response = await http.post<CaseSuggestion[]>(
    '/test-cases/suggestions/bulk-decision',
    { suggestion_ids: ids, action, decision_note: decisionNote },
  )
  return response.data
}

export async function bulkEditCaseSuggestions(
  ids: number[], payload: {
    priority?: SuggestedCase['priority']
    tags?: string[]
    requirement_ids?: number[]
  },
): Promise<CaseSuggestion[]> {
  const response = await http.post<CaseSuggestion[]>('/test-cases/suggestions/bulk-edit', {
    suggestion_ids: ids, ...payload,
  })
  return response.data
}

export async function bulkDeleteCaseSuggestions(ids: number[]): Promise<number> {
  const response = await http.post<{ deleted_count: number }>(
    '/test-cases/suggestions/bulk-delete', { suggestion_ids: ids },
  )
  return response.data.deleted_count
}

export async function getRequirementCaseLinks(
  requirementId: number,
): Promise<RequirementCaseLink[]> {
  const response = await http.get<RequirementCaseLink[]>(
    `/test-cases/requirements/${requirementId}/links`,
  )
  return response.data
}

export async function getTestCases(projectId: number): Promise<TestCaseAsset[]> {
  const response = await http.get<TestCaseAsset[]>('/test-cases', {
    params: { project_id: projectId },
  })
  return response.data
}

export async function getTestCase(id: number): Promise<TestCaseAsset> {
  const response = await http.get<TestCaseAsset>(`/test-cases/${id}`)
  return response.data
}

export async function createTestCase(payload: {
  project_id: number
  content: SuggestedCase
  requirement_id?: number
  requirement_ids?: number[]
  change_note?: string
}): Promise<TestCaseAsset> {
  const response = await http.post<TestCaseAsset>('/test-cases', payload)
  return response.data
}

export async function getTestCaseVersions(id: number): Promise<TestCaseVersion[]> {
  const response = await http.get<TestCaseVersion[]>(`/test-cases/${id}/versions`)
  return response.data
}

export async function createTestCaseVersion(
  id: number, content: SuggestedCase, changeNote: string,
): Promise<TestCaseVersion> {
  const response = await http.post<TestCaseVersion>(`/test-cases/${id}/versions`, {
    content, change_note: changeNote,
  })
  return response.data
}

export async function archiveTestCase(id: number): Promise<TestCaseAsset> {
  const response = await http.post<TestCaseAsset>(`/test-cases/${id}/archive`)
  return response.data
}

export async function previewCaseRuntime(payload: RuntimePreviewRequest): Promise<RuntimePreviewResult> {
  const response = await http.post<RuntimePreviewResult>('/test-cases/runtime/preview', payload)
  return response.data
}
