import type { CleanupOutcome, CleanupStatus, CleanupType } from '@/types/test-case'

export interface ResourceRegistryEntry {
  id: number
  project_id: number
  run_id: string
  resource_type: string
  resource_id: string
  cleanup_type: CleanupType
  cleanup_config: Record<string, unknown>
  status: CleanupStatus
  registration_sequence: number
  attempt_count: number
  last_error: string | null
  error_code: string | null
  source: string
  created_at: string
  cleaned_at: string | null
  updated_at: string
}

export interface ResourceRegistryList {
  items: ResourceRegistryEntry[]
  total: number
}

export interface CleanupPlanItem {
  resource: ResourceRegistryEntry
  action: 'CLEAN' | 'SKIP'
  reason: string
}

export interface CleanupPlan {
  project_id: number
  run_id: string
  outcome: CleanupOutcome
  items: CleanupPlanItem[]
}

export interface CleanupResultItem {
  resource: ResourceRegistryEntry
  status: CleanupStatus
  error_code?: string | null
  message?: string | null
}

export interface CleanupExecutionResult {
  project_id: number
  run_id: string
  outcome: CleanupOutcome
  items: CleanupResultItem[]
}
