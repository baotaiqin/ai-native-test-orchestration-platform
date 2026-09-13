import type { RunnerCapabilityName, RunnerSlotType } from './runner'
import type { WebExecutionTrace, WebSessionRecoveryAudit } from './web'

export type RunType = 'API_CASE' | 'SCENARIO' | 'WEB_CASE'
export type RunTriggerType = 'MANUAL' | 'API' | 'SYSTEM'
export type RunStatus =
  | 'CREATED'
  | 'QUEUED'
  | 'ASSIGNED'
  | 'RUNNING'
  | 'CANCELLING'
  | 'SUCCESS'
  | 'FAILED'
  | 'CANCELLED'
  | 'TIMEOUT'
export type RunNodeStatus =
  | 'CREATED'
  | 'ASSIGNED'
  | 'RUNNING'
  | 'CANCELLING'
  | 'SUCCESS'
  | 'FAILED'
  | 'REVIEW'
  | 'CANCELLED'
  | 'TIMEOUT'
  | 'SKIPPED'
export type DispatchOutboxStatus = 'PENDING' | 'PUBLISHED' | 'FAILED'

export interface RunCreateRequest {
  project_id: number
  environment_id: number
  runner_id: string
  run_type: RunType
  case_id?: number | null
  case_version_id?: number | null
  scenario_id?: number | null
  scenario_version_id?: number | null
  web_case_id?: number | null
  web_case_version_id?: number | null
  trigger_type: RunTriggerType
  required_capabilities: RunnerCapabilityName[]
  required_tags: string[]
  required_slot_type: RunnerSlotType
  required_slot_count: number
  total_timeout_ms?: number | null
  runtime_variables?: Record<string, unknown>
}

export interface RunValidationIssue {
  code: string
  message: string
  field: string | null
}

export interface RunValidationResponse {
  valid: boolean
  issues: RunValidationIssue[]
  run_type: RunType
  resolved_case_version_id: number | null
  resolved_scenario_version_id: number | null
  resolved_web_case_version_id: number | null
  required_capabilities: RunnerCapabilityName[]
}

export interface RunDispatchResponse {
  run_id: string
  run_status: RunStatus
  outbox_status: DispatchOutboxStatus
  message_id: string
  routing_key: string
  attempt_count: number
  last_error: string | null
  published_at: string | null
}

export interface RunBatchDispatchItemResponse {
  run_id: string
  dispatched: boolean
  run_status: RunStatus | null
  outbox_status: DispatchOutboxStatus | null
  error_code: string | null
  message: string
}

export interface RunBatchDispatchResponse {
  requested: number
  dispatched: number
  failed: number
  items: RunBatchDispatchItemResponse[]
}

export interface StepRun {
  id: number
  case_run_id: number
  sequence_no: number
  node_id: string
  step_name: string
  step_type: string
  status: RunNodeStatus
  started_at: string | null
  ended_at: string | null
  duration: number
  error_type: string | null
  error_message: string | null
  retry_count: number
  created_at: string
  updated_at: string
}

export interface CaseRun {
  id: number
  run_id: string
  sequence_no: number
  case_id: number | null
  case_version_id: number | null
  scenario_id: number | null
  scenario_version_id: number | null
  web_case_id: number | null
  web_case_version_id: number | null
  status: RunNodeStatus
  duration: number
  retry_count: number
  started_at: string | null
  ended_at: string | null
  error_type: string | null
  error_message: string | null
  created_at: string
  updated_at: string
}

export interface CaseRunDetail extends CaseRun {
  step_runs: StepRun[]
}

export interface ApiActionTrace {
  case_run_id: number
  sequence: number
  phase: 'PRE' | 'AUTH_REFRESH' | 'EXTRACTOR' | 'POST'
  type: string
  name: string | null
  status: 'SUCCESS' | 'FAILED' | 'SKIPPED'
  duration_ms: number
  error_type: string | null
}

export interface Run {
  id: string
  run_code: string
  run_type: RunType
  project_id: number
  environment_id: number | null
  runner_id: string | null
  case_id: number | null
  case_version_id: number | null
  scenario_id: number | null
  scenario_version_id: number | null
  web_case_id: number | null
  web_case_version_id: number | null
  status: RunStatus
  trigger_type: RunTriggerType
  required_capabilities: RunnerCapabilityName[]
  required_tags: string[]
  required_slot_type: RunnerSlotType
  required_slot_count: number
  runtime_snapshot_id: string | null
  runtime_variables: Record<string, unknown>
  total_timeout_ms: number | null
  effective_total_timeout_ms: number
  force_stop_requested_at: string | null
  force_stopped: boolean
  started_at: string | null
  ended_at: string | null
  total: number
  pass: number
  fail: number
  review: number
  timeout: number
  error_type: string | null
  error_message: string | null
  created_by: string
  created_at: string
  updated_at: string
}

export interface RunDetail extends Run {
  case_runs: CaseRunDetail[]
  web_traces: WebExecutionTrace[]
  web_session_recoveries: WebSessionRecoveryAudit[]
  api_action_traces: ApiActionTrace[]
}

export interface RunListResponse {
  items: Run[]
  total: number
  page: number
  page_size: number
}
