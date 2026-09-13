import { http } from '@/api/http'
import type { EvidenceArtifactListResponse } from '@/types/evidence'

export interface EvidenceListQuery {
  project_id: number
  run_id?: string
  case_run_id?: number
  page?: number
  page_size?: number
}

export async function listEvidence(query: EvidenceListQuery): Promise<EvidenceArtifactListResponse> {
  const response = await http.get<EvidenceArtifactListResponse>('/evidence', {
    params: {
      ...query,
      page: query.page ?? 1,
      page_size: query.page_size ?? 20,
    },
  })
  return response.data
}

export async function getEvidence(
  projectId: number,
  runId: string,
  page = 1,
  pageSize = 100,
): Promise<EvidenceArtifactListResponse> {
  return listEvidence({ project_id: projectId, run_id: runId, page, page_size: pageSize })
}

export async function downloadEvidence(artifactId: string): Promise<Blob> {
  try {
    const response = await http.get<Blob>(
      `/evidence/${encodeURIComponent(artifactId)}/download`,
      { responseType: 'blob' },
    )
    return response.data
  } catch (error) {
    const data = (error as { response?: { data?: unknown } }).response?.data
    if (data instanceof Blob) {
      let message: string | undefined
      try {
        const body = JSON.parse(await data.text()) as { message?: unknown }
        if (typeof body.message === 'string' && body.message.trim()) message = body.message
      } catch {
        // Keep the original transport error when the blob is not a JSON API error.
      }
      if (message) throw new Error(message)
    }
    throw error
  }
}
