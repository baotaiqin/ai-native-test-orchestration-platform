import { AI_GENERATION_REQUEST_TIMEOUT_MS, http } from '@/api/http'
import type {
  WebDesignRevision,
  WebExploration,
  WebExplorationCreateRequest,
  WebPlan,
  WebPlanCreateRequest,
} from '@/types/web-design'

export async function createWebPlan(payload: WebPlanCreateRequest): Promise<WebPlan> {
  const response = await http.post<WebPlan>('/web-design/plans', payload, {
    timeout: AI_GENERATION_REQUEST_TIMEOUT_MS,
  })
  return response.data
}

export async function getWebPlans(projectId: number): Promise<WebPlan[]> {
  const response = await http.get<{ items: WebPlan[] }>('/web-design/plans', {
    params: { project_id: projectId },
  })
  return response.data.items
}

export async function getWebPlan(planId: number): Promise<WebPlan> {
  return (await http.get<WebPlan>(`/web-design/plans/${planId}`)).data
}

export async function createWebExploration(
  itemId: number,
  payload: WebExplorationCreateRequest,
): Promise<WebExploration> {
  return (await http.post<WebExploration>(
    `/web-design/plan-items/${itemId}/explorations`, payload,
  )).data
}

export async function dispatchWebExploration(explorationId: string): Promise<void> {
  await http.post(`/web-design/explorations/${encodeURIComponent(explorationId)}/dispatch`)
}

export async function getWebExploration(explorationId: string): Promise<WebExploration> {
  return (await http.get<WebExploration>(
    `/web-design/explorations/${encodeURIComponent(explorationId)}`,
  )).data
}

export async function getWebExplorations(itemId: number): Promise<WebExploration[]> {
  const response = await http.get<{ items: WebExploration[] }>(
    `/web-design/plan-items/${itemId}/explorations`,
  )
  return response.data.items
}

export async function getWebPlanExplorations(planId: number): Promise<WebExploration[]> {
  const response = await http.get<{ items: WebExploration[] }>(
    `/web-design/plans/${planId}/explorations`,
  )
  return response.data.items
}

export async function stopWebExploration(explorationId: string): Promise<WebExploration> {
  return (await http.post<WebExploration>(
    `/web-design/explorations/${encodeURIComponent(explorationId)}/stop`,
  )).data
}

export async function downloadWebExplorationEvidence(evidenceId: string): Promise<Blob> {
  return (await http.get<Blob>(
    `/web-design/exploration-evidence/${encodeURIComponent(evidenceId)}/download`,
    { responseType: 'blob' },
  )).data
}

export async function createWebDesignRevision(
  itemId: number,
  payload: { prompt_id: number; exploration_id?: string; recording_id?: string },
): Promise<WebDesignRevision> {
  return (await http.post<WebDesignRevision>(
    `/web-design/plan-items/${itemId}/revisions`, payload,
    { timeout: AI_GENERATION_REQUEST_TIMEOUT_MS },
  )).data
}

export async function getWebDesignRevisions(itemId: number): Promise<WebDesignRevision[]> {
  const response = await http.get<{ items: WebDesignRevision[] }>(
    `/web-design/plan-items/${itemId}/revisions`,
  )
  return response.data.items
}

export async function decideWebDesignRevision(
  revisionId: number,
  payload: {
    action: 'ACCEPT' | 'REJECT'
    name?: string
    web_case_id?: number
    decision_note?: string
  },
): Promise<WebDesignRevision> {
  return (await http.post<WebDesignRevision>(
    `/web-design/revisions/${revisionId}/decision`, payload,
  )).data
}
