import { http } from '@/api/http'
import type {
  Dataset, DatasetIterationPreview, DatasetPreview, DatasetSnapshotRow, DatasetSourceType,
  DatasetVersion,
} from '@/types/dataset'

export async function getDatasets(projectId: number): Promise<{ items: Dataset[]; total: number }> {
  const response = await http.get<{ items: Dataset[]; total: number }>('/datasets', { params: { project_id: projectId } })
  return response.data
}

export async function getDataset(datasetId: number): Promise<Dataset> {
  const response = await http.get<Dataset>(`/datasets/${datasetId}`)
  return response.data
}

export async function getDatasetVersions(datasetId: number): Promise<DatasetVersion[]> {
  const response = await http.get<DatasetVersion[]>(`/datasets/${datasetId}/versions`)
  return response.data
}

export async function archiveDataset(datasetId: number): Promise<Dataset> {
  const response = await http.post<Dataset>(`/datasets/${datasetId}/archive`)
  return response.data
}

export async function previewCsv(file: File, delimiter: string): Promise<DatasetPreview> {
  const data = new FormData()
  data.append('file', file)
  data.append('delimiter', delimiter)
  const response = await http.post<DatasetPreview>('/datasets/preview/csv', data)
  return response.data
}

export async function previewExcelSheets(file: File): Promise<string[]> {
  const data = new FormData()
  data.append('file', file)
  const response = await http.post<{ items: string[] }>('/datasets/preview/excel/sheets', data)
  return response.data.items
}

export async function previewExcel(file: File, sheetName: string, headerRow: number): Promise<DatasetPreview> {
  const data = new FormData()
  data.append('file', file)
  data.append('sheet_name', sheetName)
  data.append('header_row', String(headerRow))
  const response = await http.post<DatasetPreview>('/datasets/preview/excel', data)
  return response.data
}

export async function previewFaker(payload: {
  fields: { name: string; generator: string }[]
  row_count: number
  seed?: number | null
}): Promise<DatasetPreview> {
  const response = await http.post<DatasetPreview>('/datasets/preview/faker', { config: payload })
  return response.data
}

export async function previewMysql(projectId: number, config: {
  connection_id: number
  sql: string
  params: Record<string, unknown> | unknown[]
  max_rows: number
}): Promise<DatasetPreview> {
  const response = await http.post<DatasetPreview>('/datasets/preview/mysql', { project_id: projectId, config })
  return response.data
}

export async function createDataset(payload: {
  project_id: number
  name: string
  source_type: DatasetSourceType
  config: Record<string, unknown>
  source_metadata: Record<string, unknown>
  columns: { name: string; value_type: string }[]
  snapshot: DatasetSnapshotRow[]
}): Promise<Dataset> {
  const response = await http.post<Dataset>('/datasets', payload)
  return response.data
}

export async function createDatasetVersion(datasetId: number, payload: {
  source_type: DatasetSourceType
  config: Record<string, unknown>
  source_metadata: Record<string, unknown>
  columns: { name: string; value_type: string }[]
  snapshot: DatasetSnapshotRow[]
}): Promise<DatasetVersion> {
  const response = await http.post<DatasetVersion>(`/datasets/${datasetId}/versions`, payload)
  return response.data
}

export async function previewDatasetIterations(payload: {
  dataset_id: number
  dataset_version_id?: number
  prefix?: string
  column_mapping?: Record<string, string>
  limit?: number
  offset?: number
}): Promise<DatasetIterationPreview> {
  const response = await http.post<DatasetIterationPreview>('/datasets/iterations/preview', payload)
  return response.data
}
