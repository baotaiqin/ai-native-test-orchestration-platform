import { AI_GENERATION_REQUEST_TIMEOUT_MS, http } from '@/api/http'
import type {
  WebFailureAnalysisCreateRequest,
  WebFailureAnalysisListResponse,
  WebFailureAnalysisResponse,
} from '@/types/web-failure-analysis'

export const WEB_FAILURE_ANALYSIS_GENERATION_TIMEOUT_MS = AI_GENERATION_REQUEST_TIMEOUT_MS

function analysisPath(runId: string): string {
  return `/runs/${encodeURIComponent(runId)}/web-failure-analyses`
}

export async function generateWebFailureAnalysis(
  runId: string,
  payload: WebFailureAnalysisCreateRequest,
): Promise<WebFailureAnalysisResponse> {
  const response = await http.post<WebFailureAnalysisResponse>(analysisPath(runId), payload, {
    timeout: AI_GENERATION_REQUEST_TIMEOUT_MS,
  })
  return response.data
}

export async function getWebFailureAnalyses(
  runId: string,
  caseRunId?: number,
): Promise<WebFailureAnalysisListResponse> {
  const response = await http.get<WebFailureAnalysisListResponse>(analysisPath(runId), {
    params: caseRunId === undefined ? undefined : { case_run_id: caseRunId },
  })
  return response.data
}
