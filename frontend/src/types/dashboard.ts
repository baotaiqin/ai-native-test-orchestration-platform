import type { ReportRate } from './reports'
import type { RunStatus, RunType } from './run'

export interface DashboardDateRange {
  timezone: 'Asia/Shanghai'
  start_utc: string
  end_utc_exclusive: string
  run_time_field: 'created_at'
}

export interface DashboardCaseInventory {
  api_cases: number
  web_cases: number
  case_total: number
  scenarios: number
  definition: string
}

export interface DashboardTodayRuns {
  total: number
  success: number
  failed: number
  timeout: number
  cancelled: number
  unfinished: number
  success_rate: ReportRate
}

export interface DashboardPendingReviews {
  requirement_reviews: number
  ai_case_suggestions: number
  web_recording_ai_suggestions: number
  web_healing_proposals: number
  total: number
  definition: string
}

export interface DashboardRunnerOverview {
  visibility: 'ADMIN_ONLY' | 'VISIBLE'
  available: boolean | null
  status: 'KNOWN' | 'UNKNOWN' | 'HIDDEN'
  registered_total: number | null
  active_total: number | null
  online: number | null
  offline: number | null
  unknown: number | null
}

export interface DashboardRecentRun {
  run_id: string
  run_code: string
  run_type: RunType
  project_id: number
  project_name: string
  status: RunStatus
  created_at: string
  ended_at: string | null
  report_path: string
}

export interface DashboardResponse {
  generated_at: string
  timezone: 'Asia/Shanghai'
  project_scope_id: number | null
  active_project_count: number
  date_range: DashboardDateRange
  cases: DashboardCaseInventory
  today_runs: DashboardTodayRuns
  pending_reviews: DashboardPendingReviews
  runners: DashboardRunnerOverview
  recent_runs: DashboardRecentRun[]
  read_only: true
}
