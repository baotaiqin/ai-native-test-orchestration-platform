import type { RunStatus } from './run'

export interface RunEventResponse {
  id: string
  schema_version: 1
  event_type: string
  project_id: number
  run_id: string
  case_run_id: number | null
  step_run_id: number | null
  from_status: RunStatus | null
  to_status: RunStatus
  occurred_at: string
  total?: number | null
  pass_count?: number | null
  fail_count?: number | null
  review_count?: number | null
  timeout_count?: number | null
}
