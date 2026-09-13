export interface RequirementReviewResult {
  clarity_issues: string[]
  ambiguity: string[]
  missing_rules: string[]
  exception_gaps: string[]
  testability: string[]
  acceptance_criteria_suggestions: string[]
  overall_summary: string
}

export interface RequirementRevisionContentGroup {
  target_requirement_code: string
  suggestion_ids: string[]
  reason: string
  proposed_markdown: string
}

export interface RequirementRevisionAddition {
  client_key: string
  suggestion_ids: string[]
  parent_requirement_code: string
  insert_after_requirement_code: string
  title: string
  requirement_type: 'FEATURE' | 'RULE' | 'ACCEPTANCE_CRITERIA' | 'SECTION'
  proposed_markdown: string
  reason: string
}

export interface RequirementRevisionDeletion {
  target_requirement_code: string
  suggestion_ids: string[]
  delete_mode: 'SUBTREE' | 'PROMOTE_CHILDREN'
  reason: string
}

export interface RequirementRevisionUnresolved {
  suggestion_ids: string[]
  reason: string
}

export interface RequirementRevisionPlanResult {
  content_revisions: RequirementRevisionContentGroup[]
  additions: RequirementRevisionAddition[]
  deletions: RequirementRevisionDeletion[]
  unresolved: RequirementRevisionUnresolved[]
}

export interface RequirementRevisionSuggestion {
  id: string
  category: string
  content: string
}

export interface RequirementRevisionPlanState {
  status: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'
  result: RequirementRevisionPlanResult | null
  suggestions?: RequirementRevisionSuggestion[]
  ai_call_id: number | null
  error_message: string | null
  updated_at: string
}

export interface RequirementReview {
  id: number
  project_id: number
  requirement_id: number
  requirement_version_id: number
  requirement_code: string
  requirement_title: string
  requirement_version_no: number
  prompt_id: number | null
  ai_call_id: number | null
  generation_status: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'
  status: 'DRAFT' | 'ACCEPTED' | 'REJECTED'
  additional_instructions: string | null
  context_snapshot: Record<string, unknown>
  raw_response: string | null
  structured_result: RequirementReviewResult | null
  human_result: RequirementReviewResult | null
  error_message: string | null
  decision_note: string | null
  created_by: string
  reviewed_by: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
  updated_at: string
  decided_at: string | null
  reused: boolean
}

export interface RequirementReviewPage {
  items: RequirementReview[]
  total: number
  page: number
  page_size: number
  active_count: number
}
