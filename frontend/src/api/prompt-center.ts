import { http } from '@/api/http'
import type { AiTaskType } from '@/types/model-center'
import type {
  PromptCreateRequest, PromptDefinition, PromptRenderResult, PromptVersion,
  PromptVersionCreateRequest, ProjectPromptTemplate, ProjectPromptVersionCreateRequest,
} from '@/types/prompt-center'

export async function getPrompts(
  includeDisabled = true, taskType?: AiTaskType,
): Promise<PromptDefinition[]> {
  const response = await http.get<{ items: PromptDefinition[] }>('/prompt-center', {
    params: { include_disabled: includeDisabled, task_type: taskType },
  })
  return response.data.items
}

export async function createPrompt(payload: PromptCreateRequest): Promise<PromptDefinition> {
  const response = await http.post<PromptDefinition>('/prompt-center', payload)
  return response.data
}

export async function updatePrompt(
  id: number, payload: { name?: string; description?: string; enabled?: boolean },
): Promise<PromptDefinition> {
  const response = await http.patch<PromptDefinition>(`/prompt-center/${id}`, payload)
  return response.data
}

export async function createPromptVersion(
  id: number,
  payload: PromptVersionCreateRequest,
): Promise<PromptDefinition> {
  const response = await http.post<PromptDefinition>(
    `/prompt-center/${id}/versions`, payload,
  )
  return response.data
}

export async function getPromptVersions(id: number): Promise<PromptVersion[]> {
  const response = await http.get<PromptVersion[]>(`/prompt-center/${id}/versions`)
  return response.data
}

export async function rollbackPromptVersion(
  id: number, versionId: number,
): Promise<PromptDefinition> {
  const response = await http.post<PromptDefinition>(
    `/prompt-center/${id}/versions/${versionId}/rollback`,
  )
  return response.data
}

export async function copyPrompt(
  id: number, payload: { name: string; code: string },
): Promise<PromptDefinition> {
  const response = await http.post<PromptDefinition>(`/prompt-center/${id}/copy`, payload)
  return response.data
}

export async function renderPrompt(
  id: number, variables: Record<string, unknown>,
): Promise<PromptRenderResult> {
  const response = await http.post<PromptRenderResult>(`/prompt-center/${id}/render`, {
    variables,
  })
  return response.data
}

export async function getProjectPromptTemplates(projectId: number): Promise<ProjectPromptTemplate[]> {
  const response = await http.get<{ items: ProjectPromptTemplate[] }>('/prompt-center/project-templates', {
    params: { project_id: projectId },
  })
  return response.data.items
}

export async function saveProjectPromptVersion(
  projectId: number,
  basePromptId: number,
  payload: ProjectPromptVersionCreateRequest,
): Promise<ProjectPromptTemplate> {
  const response = await http.post<ProjectPromptTemplate>(
    `/prompt-center/project-templates/${basePromptId}/versions`,
    payload,
    { params: { project_id: projectId } },
  )
  return response.data
}

export async function getProjectPromptVersions(
  projectId: number,
  basePromptId: number,
): Promise<PromptVersion[]> {
  const response = await http.get<PromptVersion[]>(
    `/prompt-center/project-templates/${basePromptId}/versions`,
    { params: { project_id: projectId } },
  )
  return response.data
}

export async function restoreProjectPromptDefault(
  projectId: number,
  basePromptId: number,
): Promise<ProjectPromptTemplate> {
  const response = await http.post<ProjectPromptTemplate>(
    `/prompt-center/project-templates/${basePromptId}/restore`,
    undefined,
    { params: { project_id: projectId } },
  )
  return response.data
}

export async function updateSystemPromptTemplate(
  id: number,
  payload: Pick<PromptVersionCreateRequest, 'system_prompt' | 'user_template' | 'output_schema_id'>,
): Promise<PromptDefinition> {
  const response = await http.patch<PromptDefinition>(`/prompt-center/system-templates/${id}`, payload)
  return response.data
}

export async function restoreSystemPromptTemplate(id: number): Promise<PromptDefinition> {
  const response = await http.post<PromptDefinition>(`/prompt-center/system-templates/${id}/restore`)
  return response.data
}
