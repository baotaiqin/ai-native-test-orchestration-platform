import type { WebCaseContent } from '@/types/web'

export type WebPlanGenerationStatus = 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'
export type WebExplorationStatus =
  | 'CREATED' | 'QUEUED' | 'RUNNING' | 'STOP_REQUESTED'
  | 'COMPLETED' | 'FAILED' | 'CANCELLED'
export type WebExplorationDispatchStatus = 'PENDING' | 'PUBLISHED' | 'FAILED'
export type WebExplorationPermission =
  | 'PAGE_READ' | 'MANAGED_LOGIN' | 'FORM_SUBMIT'
  | 'TEST_DATA_CREATE' | 'DIRECT_API_NAVIGATION' | 'SCREENSHOT_CAPTURE'

export interface WebExplorationEvidence {
  id: string
  exploration_id: string
  sequence: number
  kind: 'CHECKPOINT' | 'FINAL' | 'FAILURE'
  label: string
  file_name: string
  mime: 'image/png'
  size: number
  sha256: string
  created_at: string
  download_path: string
}

export interface WebExplorationStep {
  sequence: number
  page_url: string | null
  page_title: string | null
  action: string | null
  element: string | null
  reason: string | null
  expected_observation: string | null
  status: string
  mcp_tool: string | null
  result: string | null
  evidence_ids: string[]
}

export interface WebPlanItem {
  id: number
  candidate_key: string
  order_index: number
  name: string
  objective: string
  category: 'POSITIVE' | 'NEGATIVE' | 'BOUNDARY' | 'RECOVERY'
  priority: 'P0' | 'P1' | 'P2' | 'P3'
  rationale: string
  requirement_ids: number[]
  api_definition_ids: number[]
  preconditions: string[]
  planned_steps: string[]
  expected_outcomes: string[]
  start_url_hint: string | null
  latest_exploration_id: string | null
  latest_revision_id: number | null
}

export interface WebPlan {
  id: number
  project_id: number
  requirement_document_version_id: number
  requirement_document_version_no: number
  api_import_id: number | null
  prompt_id: number
  ai_call_id: number | null
  generation_status: WebPlanGenerationStatus
  source_snapshot_sha256: string
  summary: string | null
  items: WebPlanItem[]
  actual_model: string | null
  fallback_used: boolean
  repair_used: boolean
  created_at: string
  started_at: string | null
  completed_at: string | null
  error_message: string | null
  reused: boolean
}

export interface WebPlanCreateRequest {
  project_id: number
  prompt_id: number
  requirement_document_version_id: number
  requirement_id?: number
  api_import_id?: number
  api_definition_ids: number[]
  additional_instructions?: string
}

export interface WebExploration {
  id: string
  project_id: number
  plan_item_id: number
  plan_item_name?: string | null
  plan_item_order?: number | null
  runner_id: string
  environment_id: number | null
  session_profile_id: number | null
  use_login_credentials: boolean
  headless: boolean
  decision_prompt_id: number
  start_url: string
  allowed_origins: string[]
  permissions: WebExplorationPermission[]
  max_steps: number
  current_step: number
  status: WebExplorationStatus
  dispatch_status: WebExplorationDispatchStatus
  message_id: string | null
  observation_count: number
  action_count: number
  steps: WebExplorationStep[]
  evidence: WebExplorationEvidence[]
  result_summary: Record<string, unknown> | null
  error_type: string | null
  error_message: string | null
  created_at: string
  updated_at: string
  started_at: string | null
  completed_at: string | null
}

export interface WebExplorationCreateRequest {
  runner_id: string
  decision_prompt_id: number
  environment_id?: number | null
  session_profile_id?: number | null
  use_login_credentials: boolean
  headless: boolean
  start_url: string
  allowed_origins: string[]
  permissions: WebExplorationPermission[]
  max_steps: number
}

export interface WebReconcileResult {
  suggested_name: string
  summary: string
  coverage_notes: string[]
  unresolved_gaps: string[]
  content: WebCaseContent
}

export interface WebDesignRevision {
  id: number
  project_id: number
  plan_item_id: number
  exploration_id: string | null
  recording_id: string | null
  prompt_id: number
  ai_call_id: number | null
  source_type: 'MCP_EXPLORATION' | 'MANUAL_RECORDING'
  generation_status: WebPlanGenerationStatus
  status: 'DRAFT' | 'ACCEPTED' | 'REJECTED'
  structured_result: WebReconcileResult | null
  human_content: WebCaseContent | null
  decision_note: string | null
  created_web_case_id: number | null
  created_web_case_version_id: number | null
  created_by: string
  reviewed_by: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
  reviewed_at: string | null
  error_message: string | null
}
