import { http } from '@/api/http'
import type {
  ReportCasePage,
  ReportDetailResponse,
  ReportEvidencePage,
  ReportEvidenceQuery,
  ReportExportFormat,
  ReportExportResponse,
  ReportListQuery,
  ReportListResponse,
  ReportRequirementSourcePage,
  ReportRequirementSourceQuery,
  ReportStepPage,
  ReportStepQuery,
} from '@/types/reports'

const REPORT_EXPORT_TIMEOUT_MS = 60_000

export async function getReports(query: ReportListQuery): Promise<ReportListResponse> {
  const response = await http.get<ReportListResponse>('/reports', { params: query })
  return response.data
}

export async function getReport(runId: string): Promise<ReportDetailResponse> {
  const response = await http.get<ReportDetailResponse>(`/reports/${encodeURIComponent(runId)}`)
  return response.data
}

export async function getReportCases(
  runId: string,
  page = 1,
  pageSize = 20,
): Promise<ReportCasePage> {
  const response = await http.get<ReportCasePage>(
    `/reports/${encodeURIComponent(runId)}/cases`,
    { params: { page, page_size: pageSize } },
  )
  return response.data
}

export async function getReportSteps(
  runId: string,
  query: ReportStepQuery = {},
): Promise<ReportStepPage> {
  const response = await http.get<ReportStepPage>(
    `/reports/${encodeURIComponent(runId)}/steps`,
    { params: { ...query, page: query.page ?? 1, page_size: query.page_size ?? 50 } },
  )
  return response.data
}

export async function getReportEvidence(
  runId: string,
  query: ReportEvidenceQuery = {},
): Promise<ReportEvidencePage> {
  const response = await http.get<ReportEvidencePage>(
    `/reports/${encodeURIComponent(runId)}/evidence`,
    { params: { ...query, page: query.page ?? 1, page_size: query.page_size ?? 50 } },
  )
  return response.data
}

export async function getReportRequirementSources(
  runId: string,
  query: ReportRequirementSourceQuery = {},
): Promise<ReportRequirementSourcePage> {
  const response = await http.get<ReportRequirementSourcePage>(
    `/reports/${encodeURIComponent(runId)}/requirement-sources`,
    { params: { ...query, page: query.page ?? 1, page_size: query.page_size ?? 20 } },
  )
  return response.data
}

export async function exportReport(
  runId: string,
  format: ReportExportFormat,
): Promise<ReportExportResponse> {
  const response = await http.get<Blob>(
    `/reports/${encodeURIComponent(runId)}/export`,
    {
      params: { format },
      responseType: 'blob',
      timeout: REPORT_EXPORT_TIMEOUT_MS,
    },
  )
  return {
    blob: response.data,
    contentType: String(response.headers['content-type'] ?? ''),
    contentDisposition: String(response.headers['content-disposition'] ?? ''),
  }
}
