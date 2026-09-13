import { http } from './http'
import type {
  RunCreateRequest,
  RunBatchDispatchResponse,
  RunDetail,
  RunDispatchResponse,
  RunListResponse,
  RunValidationResponse,
} from '@/types/run'

export async function validateRun(payload: RunCreateRequest): Promise<RunValidationResponse> {
  const response = await http.post<RunValidationResponse>('/runs/validate', payload)
  return response.data
}

export async function createRun(payload: RunCreateRequest): Promise<RunDetail> {
  const response = await http.post<RunDetail>('/runs', payload)
  return response.data
}

export async function getRuns(projectId: number, page = 1, pageSize = 10): Promise<RunListResponse> {
  const response = await http.get<RunListResponse>('/runs', {
    params: { project_id: projectId, page, page_size: pageSize },
  })
  return response.data
}

export async function getRun(runId: string): Promise<RunDetail> {
  const response = await http.get<RunDetail>(`/runs/${encodeURIComponent(runId)}`)
  return response.data
}

export async function dispatchRun(runId: string): Promise<RunDispatchResponse> {
  const response = await http.post<RunDispatchResponse>(`/runs/${encodeURIComponent(runId)}/dispatch`)
  return response.data
}

export async function dispatchRunsBatch(projectId: number, runIds: string[]): Promise<RunBatchDispatchResponse> {
  const response = await http.post<RunBatchDispatchResponse>('/runs/dispatch-batch', {
    project_id: projectId,
    run_ids: runIds,
  })
  return response.data
}

export async function cancelRun(runId: string): Promise<RunDetail> {
  const response = await http.post<RunDetail>(`/runs/${encodeURIComponent(runId)}/cancel`)
  return response.data
}

export async function forceStopRun(runId: string): Promise<RunDetail> {
  const response = await http.post<RunDetail>(`/runs/${encodeURIComponent(runId)}/force-stop`)
  return response.data
}
