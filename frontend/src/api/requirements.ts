import { http } from '@/api/http'
import type {
  MarkdownPreviewNode,
  Requirement,
  RequirementDiff,
  RequirementDocumentDraftNode,
  RequirementDocumentVersion,
  RequirementTreeNode,
  RequirementTreeState,
  RequirementType,
  RequirementVerificationType,
  RequirementAutomationReadiness,
  RequirementVersion,
} from '@/types/requirement'

export interface MarkdownImportPayload {
  project_id: number
  filename?: string
  content: string
}

export async function getRequirementTree(projectId: number): Promise<RequirementTreeNode[]> {
  return (await getRequirementTreeState(projectId)).items
}

export async function getRequirementTreeState(projectId: number): Promise<RequirementTreeState> {
  const response = await http.get<RequirementTreeState>('/requirements', {
    params: { project_id: projectId },
  })
  return response.data
}

export async function getRequirementDocumentVersions(
  projectId: number,
): Promise<RequirementDocumentVersion[]> {
  const response = await http.get<RequirementDocumentVersion[]>(
    `/requirements/projects/${projectId}/document-versions`,
  )
  return response.data
}

export async function publishRequirementDocumentVersion(
  projectId: number,
  payload: {
    nodes: RequirementDocumentDraftNode[]
    change_summary?: string
    source_review_id?: number
  },
): Promise<RequirementDocumentVersion> {
  const response = await http.post<{ document_version: RequirementDocumentVersion }>(
    `/requirements/projects/${projectId}/document-versions`,
    payload,
  )
  return response.data.document_version
}

export async function getRequirement(requirementId: number): Promise<Requirement> {
  const response = await http.get<Requirement>(`/requirements/${requirementId}`)
  return response.data
}

export async function createRequirement(payload: {
  project_id: number
  parent_id?: number | null
  title: string
  type: RequirementType
  verification_type?: RequirementVerificationType
  automation_readiness?: RequirementAutomationReadiness
  markdown_content: string
}): Promise<Requirement> {
  const response = await http.post<Requirement>('/requirements', payload)
  return response.data
}

export async function previewMarkdown(
  payload: MarkdownImportPayload,
): Promise<MarkdownPreviewNode[]> {
  const response = await http.post<{ nodes: MarkdownPreviewNode[] }>(
    '/requirements/import/preview',
    payload,
  )
  return response.data.nodes
}

export async function importMarkdown(payload: MarkdownImportPayload): Promise<number> {
  const response = await http.post<{ created_count: number }>('/requirements/import', payload)
  return response.data.created_count
}

export async function createRequirementVersion(
  requirementId: number,
  payload: { title?: string; markdown_content: string; change_summary?: string },
): Promise<Requirement> {
  const response = await http.post<Requirement>(
    `/requirements/${requirementId}/versions`,
    payload,
  )
  return response.data
}

export async function getRequirementVersions(
  requirementId: number,
): Promise<RequirementVersion[]> {
  const response = await http.get<RequirementVersion[]>(
    `/requirements/${requirementId}/versions`,
  )
  return response.data
}

export async function setCurrentRequirementVersion(
  requirementId: number,
  versionId: number,
): Promise<Requirement> {
  const response = await http.post<Requirement>(
    `/requirements/${requirementId}/versions/${versionId}/current`,
  )
  return response.data
}

export async function getRequirementDiff(
  requirementId: number,
  fromVersion: number,
  toVersion: number,
): Promise<RequirementDiff> {
  const response = await http.get<RequirementDiff>(`/requirements/${requirementId}/diff`, {
    params: { from_version: fromVersion, to_version: toVersion },
  })
  return response.data
}
