import type { RunNodeStatus, RunStatus, RunTriggerType, RunType } from './run'

export type RecordingAvailability = 'RECORDED' | 'NOT_RECORDED' | 'NOT_APPLICABLE'
export type ReportDurationKind = 'COMPLETED' | 'OBSERVED' | 'UNAVAILABLE'
export type ReportExportFormat = 'markdown' | 'html'
export type RequirementCaptureStatus = 'CAPTURED' | 'CAPTURED_EMPTY' | 'NOT_RECORDED' | 'UNSUPPORTED_TARGET'
export type RequirementVersionBinding = 'EXACT_REQUIREMENT_VERSION' | 'REQUIREMENT_VERSION_UNKNOWN'
export type RequirementAssetVersionBinding = 'EXACT_EXECUTION_VERSION' | 'ASSET_VERSION_UNKNOWN'

export interface ReportExportResponse {
  blob: Blob
  contentType: string
  contentDisposition: string
}

export interface ReportProjectReference {
  id: number
  name: string
  status: string
  metadata_basis: 'CURRENT'
}

export interface ReportEnvironmentReference {
  id: number
  name: string | null
  enabled: boolean | null
  available: boolean
  metadata_basis: 'CURRENT'
}

export interface ReportRunnerReference {
  id: string
  name: string | null
  status: string | null
  available: boolean
  metadata_basis: 'CURRENT'
}

export interface ReportTargetReference {
  kind: RunType
  asset_id: number
  version_id: number
  asset_name: string | null
  asset_code: string | null
  asset_status: string | null
  current_version_id: number | null
  version_no: number | null
  version_status: string | null
  version_available: boolean
  is_current_version: boolean | null
  asset_metadata_basis: 'CURRENT'
  version_basis: 'LOCKED_BY_RUN'
  definition_exposure: 'REFERENCE_ONLY'
}

export interface ReportRecordedCounts {
  total: number
  passed: number
  failed: number
  review: number
  timeout: number
  source: 'RUN_RECORD'
}

export interface ReportCaseStatusCounts {
  created: number
  assigned: number
  running: number
  cancelling: number
  success: number
  failed: number
  review: number
  timeout: number
  cancelled: number
  skipped: number
  unfinished: number
  total: number
  source: 'CASE_RUN_GROUPING'
}

export interface ReportRate {
  numerator: number
  denominator: number
  value: number | null
  unit: 'RATIO'
  definition: string
}

export interface ReportDuration {
  milliseconds: number | null
  kind: ReportDurationKind
  observed_at: string
}

export interface ReportSummary {
  run_id: string
  run_code: string
  run_type: RunType
  project: ReportProjectReference
  environment: ReportEnvironmentReference | null
  runner: ReportRunnerReference | null
  target: ReportTargetReference
  status: RunStatus
  trigger_type: RunTriggerType
  started_at: string | null
  ended_at: string | null
  created_at: string
  updated_at: string
  duration: ReportDuration
  error_type: string | null
  error_message: string | null
  recorded_counts: ReportRecordedCounts
  case_status_counts: ReportCaseStatusCounts
  case_success_rate: ReportRate
}

export interface ReportListResponse {
  items: ReportSummary[]
  total: number
  page: number
  page_size: number
  has_more: boolean
}

export interface ReportDataField {
  availability: RecordingAvailability
  value: unknown
  note: string
}

export interface ReportExecutionResult {
  kind: RunType
  availability: RecordingAvailability
  message_id: string | null
  outcome: string | null
  status: string | null
  retry_count: number | null
  error_type: string | null
  error_message: string | null
  completed_at: string | null
  actual_request: ReportDataField
  response: ReportDataField
  extractions: ReportDataField
  assertions: ReportDataField
  traces: ReportDataField
}

export interface ReportRequirementCapture {
  case_run_id: number
  status: RequirementCaptureStatus
  captured_at: string | null
  captured_at_time_basis: 'UTC' | null
  target_type: RunType
  target_asset_id: number
  target_version_id: number
  total: number
  consistency_basis: 'CREATE_RUN_TRANSACTION' | null
  note: string
}

export interface ReportRequirementSource {
  id: number
  case_run_id: number
  sequence_no: number
  original_link_id: number
  supersedes_link_id: number | null
  requirement_id: number
  requirement_code: string
  requirement_title: string
  requirement_type: string
  requirement_status: string
  requirement_version_id: number | null
  requirement_version_no: number | null
  requirement_content_hash: string | null
  requirement_source_type: string | null
  requirement_version_binding: RequirementVersionBinding
  target_type: RunType
  link_asset_type: 'TEST_CASE' | 'WEB_CASE'
  target_asset_id: number
  target_version_id: number
  link_asset_version_id: number | null
  asset_version_binding: RequirementAssetVersionBinding
  binding_note: string
  relation_type: string
  source: string
  confidence: number
  link_created_by: string | null
  link_created_at: string
  link_created_at_time_basis: 'UTC' | 'LEGACY_UNKNOWN'
  captured_at: string
  captured_at_time_basis: 'UTC'
  historical_scope: 'RUN_CREATION_SNAPSHOT'
}

export interface ReportCase {
  id: number
  sequence_no: number
  run_id: string
  target: ReportTargetReference
  status: RunNodeStatus
  duration_ms: number
  retry_count: number
  started_at: string | null
  ended_at: string | null
  error_type: string | null
  error_message: string | null
  execution_result: ReportExecutionResult
  requirement_capture: ReportRequirementCapture
}

export interface ReportStep {
  id: number
  case_run_id: number
  sequence_no: number
  node_id: string
  name: string
  type: string
  status: RunNodeStatus
  duration_ms: number
  retry_count: number
  started_at: string | null
  ended_at: string | null
  error_type: string | null
  error_message: string | null
}

export interface ReportEvidence {
  id: string
  run_id: string
  case_run_id: number
  step_run_id: number | null
  artifact_type: string
  file_name: string
  mime: string
  size: number
  sha256: string
  metadata: unknown
  created_at: string
  download_path: string
}

export interface ReportCasePage {
  run_id: string
  items: ReportCase[]
  total: number
  page: number
  page_size: number
  has_more: boolean
  next_page: number | null
  continuation_path: string | null
}

export interface ReportStepPage {
  run_id: string
  case_run_id: number | null
  items: ReportStep[]
  total: number
  page: number
  page_size: number
  has_more: boolean
  next_page: number | null
  continuation_path: string | null
}

export interface ReportEvidencePage {
  run_id: string
  case_run_id: number | null
  step_run_id: number | null
  items: ReportEvidence[]
  total: number
  page: number
  page_size: number
  has_more: boolean
  next_page: number | null
  continuation_path: string | null
}

export interface ReportRequirementSourcePage {
  run_id: string
  case_run_id: number | null
  captures: ReportRequirementCapture[]
  items: ReportRequirementSource[]
  total: number
  page: number
  page_size: number
  has_more: boolean
  next_page: number | null
  continuation_path: string | null
}

export interface ReportAiAuditPreview {
  id: number
  kind: 'WEB_FAILURE_ANALYSIS' | 'WEB_HEALING_PROPOSAL'
  case_run_id: number
  status: string
  ai_call_id: number | null
  created_at: string
  summary: unknown
}

export interface ReportAiAuditGroup {
  total: number
  latest: ReportAiAuditPreview | null
  has_more: boolean
  continuation_path: string
}

export interface ReportRelatedAi {
  failure_analyses: ReportAiAuditGroup
  healing_proposals: ReportAiAuditGroup
  raw_ai_content_exposed: false
}

export interface ReportDetailResponse {
  summary: ReportSummary
  cases: ReportCasePage
  steps: ReportStepPage
  evidence: ReportEvidencePage
  requirement_sources: ReportRequirementSourcePage
  related_ai: ReportRelatedAi
  pagination_note: string
}

export interface ReportListQuery {
  project_id: number
  run_type?: RunType
  status?: RunStatus
  environment_id?: number
  created_from?: string
  created_to?: string
  page?: number
  page_size?: number
}

export interface ReportStepQuery {
  case_run_id?: number
  page?: number
  page_size?: number
}

export interface ReportEvidenceQuery extends ReportStepQuery {
  step_run_id?: number
}

export interface ReportRequirementSourceQuery {
  case_run_id?: number
  page?: number
  page_size?: number
}
