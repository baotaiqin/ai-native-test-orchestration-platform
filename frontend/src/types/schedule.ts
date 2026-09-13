export type ScheduleType = 'DAILY' | 'WEEKLY' | 'CRON'
export type ScheduleStatus = 'ACTIVE' | 'ARCHIVED'
export type ScheduleTriggerStatus = 'CLAIMED' | 'DISPATCHED' | 'FAILED' | 'SKIPPED'

export interface ScheduleWrite {
  project_id: number
  plan_id: number
  name: string
  schedule_type: ScheduleType
  timezone: string
  daily_time?: string
  weekdays?: number[]
  cron_expression?: string
  enabled: boolean
}

export interface TestSchedule extends ScheduleWrite {
  id: number
  plan_name: string
  status: ScheduleStatus
  next_run_at: string | null
  last_scheduled_at: string | null
  last_trigger_status: ScheduleTriggerStatus | null
  last_error_code: string | null
  last_error_message: string | null
  created_by: string
  created_at: string
  updated_at: string
}

export interface ScheduleTrigger {
  id: string
  schedule_id: number
  schedule_name: string
  project_id: number
  scheduled_for_at: string
  status: ScheduleTriggerStatus
  plan_run_id: string | null
  plan_run_status: string | null
  error_code: string | null
  error_message: string | null
  created_at: string
  completed_at: string | null
}

export interface SchedulePreviewPayload {
  schedule_type: ScheduleType
  timezone: string
  daily_time?: string
  weekdays?: number[]
  cron_expression?: string
  count?: number
}
