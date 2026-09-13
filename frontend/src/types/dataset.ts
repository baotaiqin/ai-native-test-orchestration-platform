export type DatasetSourceType = 'CSV' | 'EXCEL' | 'FAKER' | 'MYSQL'
export type DatasetStatus = 'ACTIVE' | 'ARCHIVED'

export interface DatasetColumn {
  name: string
  value_type: string
}

export interface DatasetVersion {
  id: number
  dataset_id: number
  version_no: number
  source_type: DatasetSourceType
  columns: DatasetColumn[]
  row_count: number
  config: Record<string, unknown>
  source_metadata: Record<string, unknown>
  snapshot: DatasetSnapshotRow[]
  created_by: string
  created_at: string
}

export interface DatasetSnapshotRow {
  row_index: number
  data: Record<string, unknown>
}

export interface Dataset {
  id: number
  project_id: number
  name: string
  status: DatasetStatus
  current_version_id: number | null
  created_by: string
  created_at: string
  updated_at: string
  archived_at: string | null
  current_version?: DatasetVersion | null
}

export interface DatasetPreview {
  source_type: DatasetSourceType
  columns: DatasetColumn[]
  row_count: number
  rows: DatasetSnapshotRow[]
  snapshot: DatasetSnapshotRow[]
  source_metadata: Record<string, unknown>
  config: Record<string, unknown>
}

export interface DatasetIteration {
  dataset_id: number
  dataset_version_id: number
  row_index: number
  parameters: Record<string, unknown>
  context: Record<string, unknown>
}

export interface DatasetIterationPreview {
  items: DatasetIteration[]
  total: number
  limit: number
  offset: number
}
