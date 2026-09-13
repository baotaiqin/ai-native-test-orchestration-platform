import { http } from '@/api/http'
import type {
  SessionProfileCreateRequest,
  SessionProfileResponse,
  SessionProfileUpdateRequest,
  WebCaseCreateRequest,
  WebCaseDetailResponse,
  WebCaseListResponse,
  WebCaseResponse,
  WebCaseUpdateRequest,
  WebCaseVersionCreateRequest,
  WebCaseVersionResponse,
  WebElementCreateRequest,
  WebElementResponse,
  WebElementUpdateRequest,
  WebElementVersionCreateRequest,
  WebElementVersionResponse,
  WebPageCreateRequest,
  WebPageResponse,
  WebPageUpdateRequest,
} from '@/types/web'

export async function getWebCases(projectId: number, includeArchived = false): Promise<WebCaseListResponse> {
  const response = await http.get<WebCaseListResponse>('/web-cases', {
    params: { project_id: projectId, include_archived: includeArchived },
  })
  return response.data
}

export async function getWebCase(caseId: number): Promise<WebCaseDetailResponse> {
  const response = await http.get<WebCaseDetailResponse>(`/web-cases/${caseId}`)
  return response.data
}

export async function createWebCase(payload: WebCaseCreateRequest): Promise<WebCaseDetailResponse> {
  const response = await http.post<WebCaseDetailResponse>('/web-cases', payload)
  return response.data
}

export async function updateWebCase(caseId: number, payload: WebCaseUpdateRequest): Promise<WebCaseResponse> {
  const response = await http.patch<WebCaseResponse>(`/web-cases/${caseId}`, payload)
  return response.data
}

export async function getWebCaseVersions(caseId: number): Promise<WebCaseVersionResponse[]> {
  const response = await http.get<WebCaseVersionResponse[]>(`/web-cases/${caseId}/versions`)
  return response.data
}

export async function createWebCaseVersion(
  caseId: number,
  payload: WebCaseVersionCreateRequest,
): Promise<WebCaseVersionResponse> {
  const response = await http.post<WebCaseVersionResponse>(`/web-cases/${caseId}/versions`, payload)
  return response.data
}

export async function approveWebCase(caseId: number): Promise<WebCaseResponse> {
  const response = await http.post<WebCaseResponse>(`/web-cases/${caseId}/approve`)
  return response.data
}

export async function archiveWebCase(caseId: number): Promise<WebCaseResponse> {
  const response = await http.post<WebCaseResponse>(`/web-cases/${caseId}/archive`)
  return response.data
}

export async function restoreWebCase(caseId: number): Promise<WebCaseResponse> {
  const response = await http.post<WebCaseResponse>(`/web-cases/${caseId}/restore`)
  return response.data
}

export async function getWebPages(projectId: number): Promise<WebPageResponse[]> {
  const response = await http.get<WebPageResponse[]>('/web-pages', { params: { project_id: projectId } })
  return response.data
}

export async function createWebPage(payload: WebPageCreateRequest): Promise<WebPageResponse> {
  const response = await http.post<WebPageResponse>('/web-pages', payload)
  return response.data
}

export async function updateWebPage(pageId: number, payload: WebPageUpdateRequest): Promise<WebPageResponse> {
  const response = await http.patch<WebPageResponse>(`/web-pages/${pageId}`, payload)
  return response.data
}

export async function archiveWebPage(pageId: number): Promise<WebPageResponse> {
  const response = await http.post<WebPageResponse>(`/web-pages/${pageId}/archive`)
  return response.data
}

export async function restoreWebPage(pageId: number): Promise<WebPageResponse> {
  const response = await http.post<WebPageResponse>(`/web-pages/${pageId}/restore`)
  return response.data
}

export async function getWebElements(pageId: number): Promise<WebElementResponse[]> {
  const response = await http.get<WebElementResponse[]>('/web-elements', { params: { page_id: pageId } })
  return response.data
}

export async function createWebElement(payload: WebElementCreateRequest): Promise<WebElementResponse> {
  const response = await http.post<WebElementResponse>('/web-elements', payload)
  return response.data
}

export async function updateWebElement(
  elementId: number,
  payload: WebElementUpdateRequest,
): Promise<WebElementResponse> {
  const response = await http.patch<WebElementResponse>(`/web-elements/${elementId}`, payload)
  return response.data
}

export async function getWebElementVersions(elementId: number): Promise<WebElementVersionResponse[]> {
  const response = await http.get<WebElementVersionResponse[]>(`/web-elements/${elementId}/versions`)
  return response.data
}

export async function createWebElementVersion(
  elementId: number,
  payload: WebElementVersionCreateRequest,
): Promise<WebElementVersionResponse> {
  const response = await http.post<WebElementVersionResponse>(`/web-elements/${elementId}/versions`, payload)
  return response.data
}

export async function archiveWebElement(elementId: number): Promise<WebElementResponse> {
  const response = await http.post<WebElementResponse>(`/web-elements/${elementId}/archive`)
  return response.data
}

export async function restoreWebElement(elementId: number): Promise<WebElementResponse> {
  const response = await http.post<WebElementResponse>(`/web-elements/${elementId}/restore`)
  return response.data
}

export async function getSessionProfiles(projectId: number): Promise<SessionProfileResponse[]> {
  const response = await http.get<SessionProfileResponse[]>('/session-profiles', { params: { project_id: projectId } })
  return response.data
}

export async function getSessionProfile(profileId: number): Promise<SessionProfileResponse> {
  const response = await http.get<SessionProfileResponse>(`/session-profiles/${profileId}`)
  return response.data
}

export async function createSessionProfile(payload: SessionProfileCreateRequest): Promise<SessionProfileResponse> {
  const response = await http.post<SessionProfileResponse>('/session-profiles', payload)
  return response.data
}

export async function updateSessionProfile(
  profileId: number,
  payload: SessionProfileUpdateRequest,
): Promise<SessionProfileResponse> {
  const response = await http.patch<SessionProfileResponse>(`/session-profiles/${profileId}`, payload)
  return response.data
}

export async function archiveSessionProfile(profileId: number): Promise<SessionProfileResponse> {
  const response = await http.post<SessionProfileResponse>(`/session-profiles/${profileId}/archive`)
  return response.data
}

export async function restoreSessionProfile(profileId: number): Promise<SessionProfileResponse> {
  const response = await http.post<SessionProfileResponse>(`/session-profiles/${profileId}/restore`)
  return response.data
}
