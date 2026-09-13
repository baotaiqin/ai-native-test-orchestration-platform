import { AI_GENERATION_REQUEST_TIMEOUT_MS, http } from './http'
import type {
  PerformanceAnalysis,
  PerformanceCleanupMode,
  PerformanceComparison,
  PerformanceEngine,
  PerformanceLoadMode,
  PerformanceProfile,
  PerformanceRun,
  PerformanceRunStartResponse,
  PerformanceRunnerOption,
  PerformanceSla,
  PerformanceStepStage,
  PerformanceStreamConfig,
} from '@/types/performance'

export async function getPerformanceProfiles(projectId: number): Promise<PerformanceProfile[]> {
  const response = await http.get<{ items: PerformanceProfile[] }>('/performance/profiles', {
    params: { project_id: projectId },
  })
  return response.data.items
}

export async function getPerformanceRunnerOptions(
  projectId: number,
): Promise<PerformanceRunnerOption[]> {
  const response = await http.get<{ items: PerformanceRunnerOption[] }>(
    '/performance/runner-options',
    { params: { project_id: projectId } },
  )
  return response.data.items
}

export async function createPerformanceProfile(payload: {
  project_id: number
  name: string
  description?: string
  target_type: 'API_CASE' | 'SCENARIO'
  case_id?: number
  case_version_id?: number
  scenario_id?: number
  scenario_version_id?: number
  concurrency: number
  iterations: number
  load_mode: PerformanceLoadMode
  target_rps?: number
  duration_seconds?: number
  step_stages?: PerformanceStepStage[]
  warmup_iterations: number
  request_timeout_ms: number
  engine: PerformanceEngine
  cleanup_mode: PerformanceCleanupMode
  stream_config: PerformanceStreamConfig
  sla: PerformanceSla
}): Promise<PerformanceProfile> {
  const response = await http.post<PerformanceProfile>('/performance/profiles', payload)
  return response.data
}

export async function startPerformanceRun(
  profileId: number,
  payload: { environment_id: number; runner_id: string },
): Promise<PerformanceRunStartResponse> {
  const response = await http.post<PerformanceRunStartResponse>(
    `/performance/profiles/${profileId}/runs`,
    payload,
  )
  return response.data
}

export async function getPerformanceRuns(projectId: number): Promise<PerformanceRun[]> {
  const response = await http.get<{ items: PerformanceRun[] }>('/performance/runs', {
    params: { project_id: projectId },
  })
  return response.data.items
}

export async function comparePerformanceRuns(
  projectId: number,
  runIds: string[],
): Promise<PerformanceComparison> {
  const response = await http.get<PerformanceComparison>('/performance/runs-comparison', {
    params: { project_id: projectId, run_ids: runIds },
    paramsSerializer: { indexes: null },
  })
  return response.data
}

export async function getPerformanceAnalyses(runId: string): Promise<PerformanceAnalysis[]> {
  const response = await http.get<{ items: PerformanceAnalysis[] }>(
    `/performance/runs/${runId}/analyses`,
  )
  return response.data.items
}

export async function generatePerformanceAnalysis(
  runId: string,
  payload: { prompt_id: number; additional_instructions?: string },
): Promise<PerformanceAnalysis> {
  const response = await http.post<PerformanceAnalysis>(
    `/performance/runs/${runId}/analyses`,
    payload,
    { timeout: AI_GENERATION_REQUEST_TIMEOUT_MS },
  )
  return response.data
}
