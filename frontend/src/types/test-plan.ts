import type { RunStatus, RunType } from './run'

export interface TestPlanItemInput {
  target_type: RunType
  case_id?: number
  case_version_id?: number
  scenario_id?: number
  scenario_version_id?: number
  web_case_id?: number
  web_case_version_id?: number
  enabled: boolean
}

export interface TestPlanWrite {
  project_id: number
  name: string
  description?: string
  environment_id: number
  runner_id: string
  runtime_variables: Record<string, unknown>
  execution_mode: 'PARALLEL'
  failure_strategy: 'CONTINUE'
  items: TestPlanItemInput[]
}

export interface TestPlanItem extends TestPlanItemInput {
  id: number
  sequence_no: number
  target_name: string
}

export interface TestPlan {
  id: number
  project_id: number
  name: string
  description: string | null
  environment_id: number
  runner_id: string
  runtime_variables: Record<string, unknown>
  execution_mode: 'PARALLEL'
  failure_strategy: 'CONTINUE'
  status: 'ACTIVE' | 'ARCHIVED'
  items: TestPlanItem[]
  created_by: string
  created_at: string
  updated_at: string
}

export interface TestPlanItemValidation {
  sequence_no: number
  target_type: RunType
  target_name: string
  valid: boolean
  issues: Array<{ code: string; message: string; field: string | null }>
}

export interface TestPlanValidation {
  valid: boolean
  item_count: number
  items: TestPlanItemValidation[]
}

export interface TestPlanRunnerOption {
  id: string
  name: string
  ready_capabilities: string[]
  slots: Array<{ type: 'API' | 'WEB'; available: number }>
}

export interface TestPlanRunItem {
  id: number
  sequence_no: number
  target_type: RunType
  target_name: string
  run_id: string | null
  run_code: string | null
  status: RunStatus
  error_code: string | null
  error_message: string | null
}

export interface TestPlanRun {
  id: string
  plan_id: number
  project_id: number
  plan_name: string
  status: RunStatus
  trigger_type: 'MANUAL' | 'SCHEDULE' | 'API'
  schedule_id: number | null
  total: number
  passed: number
  failed: number
  running: number
  pending: number
  items: TestPlanRunItem[]
  created_by: string
  created_at: string
  started_at: string | null
  ended_at: string | null
  updated_at: string
}

export interface TestPlanRunStart {
  plan_run: TestPlanRun
  validation: TestPlanValidation
}
