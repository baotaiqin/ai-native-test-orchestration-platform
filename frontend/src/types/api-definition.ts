export type ApiDiffStatus = 'ADDED' | 'CHANGED' | 'UNCHANGED' | 'REMOVED'

export interface ParsedApiOperation {
  name: string
  method: string
  path: string
  operation_id: string | null
  summary: string | null
  description: string | null
  parameters: Record<string, unknown>[]
  request_schema: Record<string, unknown> | null
  response_schema: Record<string, unknown>
  auth_info: Record<string, unknown>
  tags: string[]
  contract_hash: string
  diff_status: ApiDiffStatus
  existing_definition_id: number | null
}

export interface OpenApiPreview {
  filename: string
  title: string | null
  spec_version: string
  operations: ParsedApiOperation[]
  removed: ParsedApiOperation[]
  added_count: number
  changed_count: number
  unchanged_count: number
  removed_count: number
}

export interface ApiDefinition extends Omit<ParsedApiOperation, 'diff_status' | 'existing_definition_id'> {
  id: number
  project_id: number
  import_id: number | null
  source: string
  source_filename: string | null
  source_version: number | null
  status: 'ACTIVE' | 'REMOVED'
  created_by: string
  created_at: string
  updated_at: string
}

export interface OpenApiImportResult {
  import_id: number
  version_no: number
  created_count: number
  updated_count: number
  unchanged_count: number
  removed_count: number
}

export type ApiDesignSuggestionKind = 'CASE_SET' | 'SCENARIO'
export type ApiDesignSuggestionStatus = 'DRAFT' | 'ACCEPTED' | 'REJECTED'
export type ApiDesignSuggestionGenerationStatus = 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'

export interface ApiDesignScenarioReference {
  id: number
  code: string
  name: string
  status: 'DRAFT' | 'APPROVED' | 'ARCHIVED'
  version_no: number | null
}

export interface ApiDesignRequirementReference {
  id: number | null
  code: string
  title: string
  document_version_id: number
  document_version_no: number
  scope_count: number
  scope_mode: 'DOCUMENT' | 'SUBTREE'
}

export interface ApiDesignSuggestion {
  id: number
  project_id: number
  import_id: number
  source_filename: string
  source_version: number
  prompt_id: number | null
  plan_item_id: number | null
  ai_call_id: number | null
  kind: ApiDesignSuggestionKind
  status: ApiDesignSuggestionStatus
  generation_status: ApiDesignSuggestionGenerationStatus
  source_snapshot_sha256: string
  source_snapshot_size: number
  additional_instructions: string | null
  structured_result: Record<string, unknown> | null
  human_result: Record<string, unknown> | null
  decision_note: string | null
  created_test_case_ids: number[]
  created_scenario_id: number | null
  created_scenario: ApiDesignScenarioReference | null
  requirement_source: ApiDesignRequirementReference | null
  actual_model: string | null
  prompt_version_id: number | null
  output_schema_id: number | null
  fallback_used: boolean
  repair_used: boolean
  raw_response: string
  created_by: string
  reviewed_by: string | null
  created_at: string
  reviewed_at: string | null
  started_at: string | null
  completed_at: string | null
  error_message: string | null
  idempotent: boolean
  reused: boolean
}

export type ApiScenarioPlanGenerationStatus = 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'

export interface ApiScenarioPlanItem {
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
  api_flow: string[]
  preconditions: string[]
  expected_outcomes: string[]
  cleanup_required: boolean
  latest_suggestion: ApiDesignSuggestion | null
}

export interface ApiScenarioPlan {
  id: number
  project_id: number
  import_id: number
  source_filename: string
  source_version: number
  prompt_id: number
  ai_call_id: number | null
  generation_status: ApiScenarioPlanGenerationStatus
  requirement_source: ApiDesignRequirementReference
  summary: string | null
  items: ApiScenarioPlanItem[]
  actual_model: string | null
  fallback_used: boolean
  repair_used: boolean
  created_at: string
  started_at: string | null
  completed_at: string | null
  error_message: string | null
  reused: boolean
}
