import { AI_GENERATION_REQUEST_TIMEOUT_MS, http } from '@/api/http'
import type {
  DefectDraft,
  DefectDraftExportResponse,
  DefectDraftGenerateRequest,
  DefectDraftListResponse,
  DefectDraftUpdateRequest,
} from '@/types/defect-draft'

export async function getDefectDrafts(
  projectId: number,
  options: { runId?: string; page?: number; pageSize?: number } = {},
): Promise<DefectDraftListResponse> {
  const response = await http.get<DefectDraftListResponse>('/defect-drafts', {
    params: {
      project_id: projectId,
      run_id: options.runId || undefined,
      page: options.page ?? 1,
      page_size: options.pageSize ?? 20,
    },
  })
  return response.data
}

export async function generateDefectDraft(
  runId: string,
  payload: DefectDraftGenerateRequest,
): Promise<DefectDraft> {
  const response = await http.post<DefectDraft>(
    `/runs/${encodeURIComponent(runId)}/defect-drafts/generate`,
    payload,
    { timeout: AI_GENERATION_REQUEST_TIMEOUT_MS },
  )
  return response.data
}

export async function updateDefectDraft(
  draftId: number,
  payload: DefectDraftUpdateRequest,
): Promise<DefectDraft> {
  const response = await http.patch<DefectDraft>(`/defect-drafts/${draftId}`, payload)
  return response.data
}

export async function exportDefectDraft(draftId: number): Promise<DefectDraftExportResponse> {
  const response = await http.get<Blob>(`/defect-drafts/${draftId}/export`, {
    responseType: 'blob',
  })
  return {
    blob: response.data,
    contentDisposition: String(response.headers['content-disposition'] ?? ''),
  }
}
