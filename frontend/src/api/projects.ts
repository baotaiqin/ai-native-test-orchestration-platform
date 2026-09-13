import { http } from '@/api/http'
import type {
  Project,
  ProjectListResponse,
  ProjectMember,
  ProjectPayload,
  ProjectRole,
} from '@/types/project'

export async function getProjects(includeArchived = false): Promise<ProjectListResponse> {
  const response = await http.get<ProjectListResponse>('/projects', {
    params: { include_archived: includeArchived },
  })
  return response.data
}

export async function getProject(projectId: number): Promise<Project> {
  const response = await http.get<Project>(`/projects/${projectId}`)
  return response.data
}

export async function createProject(payload: ProjectPayload): Promise<Project> {
  const response = await http.post<Project>('/projects', payload)
  return response.data
}

export async function updateProject(projectId: number, payload: Partial<ProjectPayload>): Promise<Project> {
  const response = await http.patch<Project>(`/projects/${projectId}`, payload)
  return response.data
}

export async function archiveProject(projectId: number): Promise<Project> {
  const response = await http.post<Project>(`/projects/${projectId}/archive`)
  return response.data
}

export async function restoreProject(projectId: number): Promise<Project> {
  const response = await http.post<Project>(`/projects/${projectId}/restore`)
  return response.data
}

export async function getProjectMembers(projectId: number): Promise<ProjectMember[]> {
  const response = await http.get<{ items: ProjectMember[] }>(`/projects/${projectId}/members`)
  return response.data.items
}

export async function addProjectMember(
  projectId: number,
  userId: string,
  role: ProjectRole,
): Promise<ProjectMember> {
  const response = await http.post<ProjectMember>(`/projects/${projectId}/members`, {
    user_id: userId,
    role,
  })
  return response.data
}

export async function updateProjectMember(
  projectId: number,
  userId: string,
  role: ProjectRole,
): Promise<ProjectMember> {
  const response = await http.patch<ProjectMember>(
    `/projects/${projectId}/members/${encodeURIComponent(userId)}`,
    { role },
  )
  return response.data
}

export async function removeProjectMember(projectId: number, userId: string): Promise<void> {
  await http.delete(`/projects/${projectId}/members/${encodeURIComponent(userId)}`)
}
