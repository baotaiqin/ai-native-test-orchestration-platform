export interface DefectDraft {
  schema_version: 1
  id: number
  project_id: number
  run_id: string
  case_run_id: number | null
  prompt_version_id: number
  output_schema_id: number
  ai_call_id: number
  title: string
  module: string
  environment: string
  preconditions: string[]
  reproduction_steps: string[]
  expected_result: string
  actual_result: string
  evidence: string[]
  ai_analysis: string
  actual_model: string
  fallback_used: boolean
  repair_used: boolean
  confidence: number | null
  needs_human_review: boolean
  source_snapshot_sha256: string
  source_snapshot_size: number
  revision: number
  created_by: string
  updated_by: string
  created_at: string
  updated_at: string
  external_submission_supported: false
}

export interface DefectDraftListResponse {
  items: DefectDraft[]
  total: number
  page: number
  page_size: number
  has_more: boolean
}

export interface DefectDraftGenerateRequest {
  prompt_id: number
  case_run_id?: number
  additional_instructions?: string
}

export interface DefectDraftUpdateRequest {
  revision: number
  title: string
  module: string
  environment: string
  preconditions: string[]
  reproduction_steps: string[]
  expected_result: string
  actual_result: string
  evidence: string[]
  ai_analysis: string
}

export interface DefectDraftExportResponse {
  blob: Blob
  contentDisposition: string
}
