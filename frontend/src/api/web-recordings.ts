import { AI_GENERATION_REQUEST_TIMEOUT_MS, http } from '@/api/http'
import type {
  WebRecordingAiSuggestionCreateRequest,
  WebRecordingAiSuggestionListResponse,
  WebRecordingAiSuggestionRejectRequest,
  WebRecordingAiSuggestionResponse,
  WebRecordingConfirmRequest,
  WebRecordingConfirmResponse,
  WebRecordingCreateRequest,
  WebRecordingDetailResponse,
  WebRecordingDispatchResponse,
  WebRecordingListResponse,
} from '@/types/web-recording'

export async function createWebRecording(
  payload: WebRecordingCreateRequest,
): Promise<WebRecordingDetailResponse> {
  const response = await http.post<WebRecordingDetailResponse>('/web-recordings', payload)
  return response.data
}

export async function getWebRecordings(
  projectId: number,
  page = 1,
  pageSize = 20,
  planItemId?: number,
): Promise<WebRecordingListResponse> {
  const response = await http.get<WebRecordingListResponse>('/web-recordings', {
    params: { project_id: projectId, page, page_size: pageSize, plan_item_id: planItemId },
  })
  return response.data
}

export async function getWebRecording(recordingId: string): Promise<WebRecordingDetailResponse> {
  const response = await http.get<WebRecordingDetailResponse>(
    `/web-recordings/${encodeURIComponent(recordingId)}`,
  )
  return response.data
}

export async function dispatchWebRecording(
  recordingId: string,
): Promise<WebRecordingDispatchResponse> {
  const response = await http.post<WebRecordingDispatchResponse>(
    `/web-recordings/${encodeURIComponent(recordingId)}/dispatch`,
  )
  return response.data
}

export async function stopWebRecording(recordingId: string): Promise<WebRecordingDetailResponse> {
  const response = await http.post<WebRecordingDetailResponse>(
    `/web-recordings/${encodeURIComponent(recordingId)}/stop`,
  )
  return response.data
}

export async function cancelWebRecording(recordingId: string): Promise<WebRecordingDetailResponse> {
  const response = await http.post<WebRecordingDetailResponse>(
    `/web-recordings/${encodeURIComponent(recordingId)}/cancel`,
  )
  return response.data
}

export async function confirmWebRecording(
  recordingId: string,
  payload: WebRecordingConfirmRequest,
): Promise<WebRecordingConfirmResponse> {
  const response = await http.post<WebRecordingConfirmResponse>(
    `/web-recordings/${encodeURIComponent(recordingId)}/confirm`,
    payload,
  )
  return response.data
}

export async function generateWebRecordingAiSuggestion(
  recordingId: string,
  payload: WebRecordingAiSuggestionCreateRequest,
): Promise<WebRecordingAiSuggestionResponse> {
  const response = await http.post<WebRecordingAiSuggestionResponse>(
    `/web-recordings/${encodeURIComponent(recordingId)}/ai-suggestions`,
    payload,
    { timeout: AI_GENERATION_REQUEST_TIMEOUT_MS },
  )
  return response.data
}

export async function getWebRecordingAiSuggestions(
  recordingId: string,
): Promise<WebRecordingAiSuggestionListResponse> {
  const response = await http.get<WebRecordingAiSuggestionListResponse>(
    `/web-recordings/${encodeURIComponent(recordingId)}/ai-suggestions`,
  )
  return response.data
}

export async function rejectWebRecordingAiSuggestion(
  recordingId: string,
  suggestionId: number,
  payload: WebRecordingAiSuggestionRejectRequest = {},
): Promise<WebRecordingAiSuggestionResponse> {
  const response = await http.post<WebRecordingAiSuggestionResponse>(
    `/web-recordings/${encodeURIComponent(recordingId)}/ai-suggestions/${suggestionId}/reject`,
    payload,
  )
  return response.data
}
