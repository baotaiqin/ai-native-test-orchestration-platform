export type WebFailureAnalysisStatus = 'DRAFT' | 'COMPLETED'

export type WebFailureAnalysisCategory =
  | 'LOCATOR_NOT_FOUND'
  | 'ACTION_FAILED'
  | 'ASSERTION_FAILED'
  | 'TIMEOUT'
  | 'NAVIGATION'
  | 'NETWORK'
  | 'SESSION'
  | 'PAGE_CHANGED'
  | 'UNKNOWN'

export type WebFailureAnalysisSeverity = 'LOW' | 'MEDIUM' | 'HIGH'

export interface WebFailureAnalysisResult {
  failure_category: WebFailureAnalysisCategory
  severity: WebFailureAnalysisSeverity
  summary: string
  root_cause: string
  recommendations: string[]
  evidence_node_ids: string[]
  confidence: number
  needs_human_review: boolean
}

export interface WebFailureAnalysisCreateRequest {
  case_run_id: number
  prompt_id: number
  additional_instructions?: string | null
}

export interface WebFailureAnalysisResponse {
  schema_version: 1
  id: number
  project_id: number
  run_id: string
  case_run_id: number
  status: WebFailureAnalysisStatus
  ai_call_id: number | null
  actual_model: string | null
  prompt_version_id: number
  output_schema_id: number
  fallback_used: boolean | null
  repair_used: boolean | null
  source_snapshot_sha256: string
  source_snapshot_size: number
  structured_result: WebFailureAnalysisResult | null
  created_by: string
  created_at: string
}

export interface WebFailureAnalysisListResponse {
  items: WebFailureAnalysisResponse[]
  total: number
}
