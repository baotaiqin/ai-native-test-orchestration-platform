import type { LocatorStrategy, WebAssertion, WebCaseContent } from '@/types/web'

export type WebRecordingStatus =
  | 'CREATED'
  | 'QUEUED'
  | 'RUNNING'
  | 'STOP_REQUESTED'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED'

export type WebRecordingDispatchStatus = 'PENDING' | 'PUBLISHED' | 'FAILED'
export type WebRecordingEventType = 'NAVIGATE' | 'CLICK' | 'FILL' | 'SELECT' | 'PRESS'

export interface WebRecordingCreateRequest {
  project_id: number
  runner_id: string
  environment_id?: number | null
  start_url: string
  session_profile_id?: number | null
  plan_item_id?: number | null
  save_session: boolean
  save_session_name?: string | null
  save_session_expires_at?: string | null
}

export interface WebRecordingLocator {
  strategy: LocatorStrategy
  value: string
  priority: number
}

export interface WebRecordingEvent {
  event_type: WebRecordingEventType
  sequence: number
  relative_time_ms: number
  page_url: string | null
  title: string | null
  target_url: string | null
  locator_candidates: WebRecordingLocator[]
  value: string | null
  key: string | null
}

export interface WebRecordingListItem {
  id: string
  project_id: number
  runner_id: string
  environment_id: number | null
  session_profile_id: number | null
  plan_item_id: number | null
  saved_session_profile_id: number | null
  start_url: string
  browser: 'CHROME'
  status: WebRecordingStatus
  dispatch_status: WebRecordingDispatchStatus
  message_id: string | null
  event_count: number
  save_session: boolean
  confirmed_web_case_id: number | null
  confirmed_web_case_version_id: number | null
  created_by: string
  created_at: string
  updated_at: string
}

export interface WebRecordingDetailResponse extends WebRecordingListItem {
  events: WebRecordingEvent[]
  dom_context_available: boolean
  stop_requested_at: string | null
  cancel_requested_at: string | null
  started_at: string | null
  completed_at: string | null
  error_type: string | null
  error_message: string | null
  confirmed_by: string | null
  confirmed_at: string | null
}

export interface WebRecordingListResponse {
  items: WebRecordingListItem[]
  total: number
  page: number
  page_size: number
}

export interface WebRecordingDispatchResponse {
  schema_version: 1
  recording_id: string
  message_id: string
  runner_id: string
  status: WebRecordingStatus
  dispatch_status: WebRecordingDispatchStatus
  attempt_count: number
  published: boolean
  idempotent: boolean
}

export interface WebRecordingConfirmRequest {
  ai_suggestion_id?: number
  web_case_id?: number
  name?: string
  content?: WebCaseContent
  decision_note?: string | null
}

export interface WebRecordingConfirmResponse {
  schema_version: 1
  recording_id: string
  web_case_id: number
  web_case_version_id: number
  status: 'DRAFT'
  idempotent: boolean
}

export type WebRecordingAiSuggestionStatus = 'DRAFT' | 'ACCEPTED' | 'REJECTED'

export interface WebRecordingAiSuggestionCreateRequest {
  prompt_id: number
  additional_instructions?: string | null
}

export interface WebRecordingAiSuggestionStep {
  source_event_sequence: number
  natural_language_step: string
  action: 'GOTO' | 'CLICK' | 'FILL' | 'SELECT' | 'PRESS'
  locator: WebRecordingLocator | null
  element_name: string | null
  reason: string | null
}

export interface WebRecordingAiSuggestionAssertion {
  source_event_sequence: number
  assertion: WebAssertion
  reason: string | null
}

export interface WebRecordingAiSuggestionResult {
  suggested_name: string
  summary: string
  steps: WebRecordingAiSuggestionStep[]
  assertions: WebRecordingAiSuggestionAssertion[]
  warnings: string[]
}

export interface WebRecordingAiSuggestionResponse {
  schema_version: 1
  id: number
  recording_id: string
  project_id: number
  status: WebRecordingAiSuggestionStatus
  ai_call_id: number | null
  actual_model: string | null
  prompt_version_id: number | null
  output_schema_id: number | null
  fallback_used: boolean | null
  repair_used: boolean | null
  source_snapshot_sha256: string
  source_snapshot_size: number
  additional_instructions: string | null
  structured_result: WebRecordingAiSuggestionResult | null
  canonical_suggested_content: WebCaseContent | null
  human_content: WebCaseContent | null
  decision_note: string | null
  confirmed_web_case_id: number | null
  confirmed_web_case_version_id: number | null
  created_by: string
  reviewed_by: string | null
  created_at: string
  reviewed_at: string | null
  idempotent: boolean
}

export interface WebRecordingAiSuggestionListResponse {
  items: WebRecordingAiSuggestionResponse[]
  total: number
}

export interface WebRecordingAiSuggestionRejectRequest {
  decision_note?: string | null
}
