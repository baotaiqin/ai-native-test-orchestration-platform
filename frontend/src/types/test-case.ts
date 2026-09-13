export type CaseType = 'API' | 'WEB' | 'MANUAL'
export type CasePriority = 'P0' | 'P1' | 'P2' | 'P3'

export type CleanupType = 'API' | 'SQL'
export type CleanupPolicy = 'ALWAYS' | 'ON_SUCCESS' | 'ON_FAILURE' | 'NEVER'
export type CleanupStatus = 'PENDING' | 'CLEANING' | 'CLEANED' | 'FAILED' | 'SKIPPED'
export type CleanupOutcome = 'SUCCESS' | 'FAILURE' | 'CANCELLED' | 'TIMEOUT'

export interface CleanupConfig {
  cleanup_id?: string | null
  cleanup_type: CleanupType
  policy: CleanupPolicy
  enabled: boolean
  timeout_ms: number
  method?: 'DELETE' | 'POST' | 'PUT' | 'PATCH' | null
  url?: string | null
  query_params: RequestValueItem[]
  headers: RequestValueItem[]
  cookies: RequestValueItem[]
  body?: { type: 'NONE' | 'JSON' | 'FORM_URLENCODED' | 'MULTIPART' | 'RAW'; content?: unknown | null } | null
  auth?: {
    type: 'NONE' | 'BEARER' | 'BASIC' | 'API_KEY'
    credential_ref?: string | null
    secret_id?: number | null
    key_name?: string | null
    placement?: 'HEADER' | 'QUERY'
  }
  connection_id?: number | null
  sql?: string | null
  params: Record<string, unknown> | unknown[]
  resource_id_param?: string | null
}

export type AssertionKind = 'DETERMINISTIC' | 'AI_SEMANTIC'
export type DeterministicAssertionType =
  | 'STATUS_CODE' | 'JSONPATH_EQUAL' | 'CONTAINS' | 'REGEX' | 'HEADER' | 'COOKIE'
  | 'JSON_SCHEMA' | 'RESPONSE_TIME' | 'EXISTS' | 'NOT_EXISTS' | 'ARRAY_LENGTH' | 'TYPE'
export type AssertionSource =
  | 'STATUS_CODE' | 'JSON_BODY' | 'JSONPATH' | 'RESPONSE_TEXT'
  | 'HEADER' | 'COOKIE' | 'RESPONSE_TIME'
export type AssertionOperator = 'EQ' | 'CONTAINS' | 'LT' | 'LTE' | 'GT' | 'GTE'

export interface Assertion {
  kind: AssertionKind
  type: DeterministicAssertionType | 'AI_SEMANTIC'
  name: string
  enabled: boolean
  source?: AssertionSource | null
  expression?: string | null
  operator?: AssertionOperator | null
  expected?: unknown
  prompt_id?: number | null
  criteria?: string | null
  confidence_threshold?: number | null
}

export interface SuggestedStep {
  order: number
  action: string
  expected: string
}

export interface RequestValueItem {
  name: string
  value: string
  enabled: boolean
  description?: string | null
}

export type RetryCondition = 'TARGET_NETWORK_ERROR' | 'TARGET_TIMEOUT' | 'HTTP_5XX'

export interface RetryPolicy {
  max_retries: number
  backoff_ms: number
  retry_on: RetryCondition[]
}

export interface ApiRequestTemplate {
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'HEAD' | 'OPTIONS'
  url: string
  query_params: RequestValueItem[]
  headers: RequestValueItem[]
  cookies: RequestValueItem[]
  body: {
    type: 'NONE' | 'JSON' | 'FORM_URLENCODED' | 'MULTIPART' | 'RAW'
    content: unknown | null
    content_type?: string | null
  }
  auth: {
    type: 'NONE' | 'BEARER' | 'BASIC' | 'API_KEY'
    token?: string | null
    username?: string | null
    password?: string | null
    key_name?: string | null
    key_value?: string | null
    placement?: 'HEADER' | 'QUERY'
  }
  timeout_ms: number
  follow_redirects: boolean
  retry_policy: RetryPolicy
}

/**
 * Legacy SET_VARIABLE payload. Keep this shape without a `type` field when
 * loading and saving existing cases for byte-for-byte compatible DSL data.
 */
export interface VariableAction {
  name: string
  value: unknown
  enabled: boolean
}

export type FakerGenerator =
  | 'uuid'
  | 'email'
  | 'username'
  | 'first_name'
  | 'last_name'
  | 'integer'
  | 'word'
  | 'boolean'

export type ActionResponseSource = 'JSONPATH' | 'HEADER' | 'COOKIE'

export type ActionType =
  | 'SET_VARIABLE'
  | 'FAKER'
  | 'SQL_QUERY'
  | 'PYTHON_SCRIPT'
  | 'GET_TOKEN'
  | 'API_SETUP'
  | 'EXTRACT_RESPONSE'
  | 'REGISTER_RESOURCE'

export interface SetVariableAction {
  type: 'SET_VARIABLE'
  name: string
  value: unknown
  enabled: boolean
}

export interface FakerAction {
  type: 'FAKER'
  name: string
  generator: FakerGenerator
  seed?: number
  enabled: boolean
}

export interface SqlQueryAction {
  type: 'SQL_QUERY'
  connection_id: number
  sql: string
  params: Record<string, unknown> | unknown[]
  result_variable: string
  max_rows: number
  enabled: boolean
}

export interface PythonScriptAction {
  type: 'PYTHON_SCRIPT'
  script: string
  enabled: boolean
}

export interface RuntimeResponseSnapshot {
  status_code: number
  json_body: unknown
  text?: string | null
  headers: Record<string, string>
  cookies: Record<string, string>
  elapsed_ms?: number | null
  response_time_ms?: number | null
}

export interface GetTokenAction {
  type: 'GET_TOKEN'
  name: string
  source: ActionResponseSource
  expression: string
  required: boolean
  default_value?: unknown
  response: RuntimeResponseSnapshot
  request?: ApiRequestTemplate | null
  enabled: boolean
}

export interface ApiSetupAction {
  type: 'API_SETUP'
  name: string
  source: ActionResponseSource
  expression: string
  required: boolean
  default_value?: unknown
  response: RuntimeResponseSnapshot
  request: ApiRequestTemplate
  enabled: boolean
}

export interface ExtractResponseAction {
  type: 'EXTRACT_RESPONSE'
  name: string
  source: ActionResponseSource
  expression: string
  required: boolean
  default_value?: unknown
  enabled: boolean
}

export interface RegisterResourceAction {
  type: 'REGISTER_RESOURCE'
  name: string
  resource_type: string
  value: unknown
  metadata: Record<string, unknown>
  cleanup?: CleanupConfig | null
  cleanup_ref?: string | null
  enabled: boolean
}

export type PreAction =
  | VariableAction
  | SetVariableAction
  | FakerAction
  | SqlQueryAction
  | PythonScriptAction
  | GetTokenAction
  | ApiSetupAction

export type PostAction =
  | VariableAction
  | SetVariableAction
  | PythonScriptAction
  | ExtractResponseAction
  | RegisterResourceAction

export type CaseAction = PreAction | PostAction

export interface ResponseExtractor {
  name: string
  source: 'JSONPATH' | 'HEADER' | 'COOKIE'
  expression: string
  required: boolean
  default_value?: unknown
  enabled: boolean
}

export interface SuggestedCase {
  title: string
  case_type: CaseType
  priority: CasePriority
  preconditions: string[]
  steps: SuggestedStep[]
  test_data: Record<string, unknown>
  expected_result: string
  tags: string[]
  confidence: number
  request?: ApiRequestTemplate | null
  pre_actions?: PreAction[]
  post_actions?: PostAction[]
  extractors?: ResponseExtractor[]
  data_source?: CaseDataSourceConfig | null
  assertions?: Assertion[]
  cleanup?: CleanupConfig[]
}

export interface CaseDataSourceConfig {
  dataset_id: number
  dataset_version_id?: number | null
  prefix?: string
  column_mapping?: Record<string, string>
}

export interface RuntimePreviewTrace {
  sequence: number
  phase: 'PRE' | 'REQUEST' | 'POST'
  action_type: string
  status: 'PASSED' | 'SKIPPED' | 'FAILED'
  detail: Record<string, unknown>
}

export interface RuntimePreviewResult {
  rendered_request: Record<string, unknown>
  context: Record<string, unknown>
  extracted: Record<string, unknown>
  traces: RuntimePreviewTrace[]
  assertion_results: AssertionResult[]
  final_status: 'PASS' | 'FAIL' | 'REVIEW'
}

export interface AssertionResult {
  sequence: number
  name: string
  type: string
  status: 'PASS' | 'FAIL' | 'REVIEW' | 'SKIPPED'
  expected: unknown
  actual: unknown
  message: string
  duration_ms: number
  confidence?: number | null
  reason?: string | null
  ai_call_id?: number | null
  actual_model?: string | null
  fallback_used?: boolean | null
  repair_used?: boolean | null
}

export interface RuntimePreviewRequest {
  project_id: number
  request: ApiRequestTemplate
  context: Record<string, unknown>
  pre_actions: PreAction[]
  response: RuntimeResponseSnapshot
  extractors: ResponseExtractor[]
  post_actions: PostAction[]
  assertions: Assertion[]
}

export interface CaseSuggestion {
  id: number
  generation_id: number
  sequence_no: number
  status: 'DRAFT' | 'ACCEPTED' | 'REJECTED'
  structured_result: SuggestedCase
  human_result: SuggestedCase | null
  linked_requirement_ids: number[]
  decision_note: string | null
  test_case_id: number | null
  reviewed_by: string | null
  reviewed_at: string | null
}

export interface CaseGeneration {
  id: number
  project_id: number
  requirement_id: number
  requirement_code: string
  requirement_title: string
  requirement_version_id: number
  requirement_version_no: number
  ai_call_id: number
  actual_model: string
  prompt_version_id: number
  prompt_name: string
  prompt_version_no: number
  output_schema_id: number | null
  output_schema_name: string | null
  output_schema_version_no: number | null
  ai_confidence_by_sequence: Record<string, number>
  fallback_used: boolean
  repair_used: boolean
  additional_instructions: string | null
  selected_api_definition_ids: number[]
  coverage_plan: CaseDesignPlan
  raw_response: string
  structured_result: { cases: SuggestedCase[] }
  created_by: string
  created_at: string
  suggestions: CaseSuggestion[]
}

export interface CaseGenerationRecompileResult {
  generation_id: number
  compiler_version: string
  recompiled_count: number
  skipped_count: number
  failed_count: number
  items: Array<{
    suggestion_id: number
    case_code: string | null
    version_no: number | null
    status: 'RECOMPILED' | 'SKIPPED' | 'FAILED'
    message: string
  }>
}

export interface CaseGenerationTask {
  id: number
  project_id: number
  requirement_id: number
  requirement_version_id: number
  prompt_id: number
  generation_id: number | null
  status: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'
  additional_instructions: string | null
  selected_api_definition_ids: number[]
  coverage_plan: CaseDesignPlan
  error_code: string | null
  error_message: string | null
  created_by: string
  created_at: string
  started_at: string | null
  completed_at: string | null
  updated_at: string
}

export interface TestCheckPoint {
  key: string
  title: string
  source: string
}

export interface RecommendedApi {
  api_definition_id: number
  name: string
  method: string
  path: string
  role: '前置准备' | '核心操作' | '结果验证' | '数据清理'
  required: boolean
  reason: string
  check_point_keys: string[]
  selected_by_default: boolean
}

export interface CaseDesignPlan {
  requirement_id: number
  requirement_version_id: number
  requirement_version_no: number
  check_points: TestCheckPoint[]
  recommended_apis: RecommendedApi[]
  gaps: string[]
  existing_case_count: number
  source: 'AI' | 'RULE_FALLBACK' | 'RULE'
  scope_requirement_count: number
  scope_requirement_total: number
  excluded_requirements: Array<{
    requirement_code: string
    requirement_title: string
    verification_type: string
    automation_readiness: string
    reason: string
  }>
  platform_completed_checkpoint_count: number
  ignored_ai_reference_count: number
  intent_count?: number
  compiled_count?: number
  compile_skipped_count?: number
  compiled_intent_keys?: string[]
}

export interface CaseDesignTask {
  id: number
  project_id: number
  requirement_id: number
  requirement_version_id: number
  requirement_code: string
  requirement_title: string
  requirement_version_no: number
  prompt_id: number
  ai_call_id: number | null
  status: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'
  source: 'AI' | 'RULE_FALLBACK' | null
  included_api_definition_ids: number[]
  plan: CaseDesignPlan | null
  error_message: string | null
  reused: boolean
  created_at: string
  started_at: string | null
  completed_at: string | null
  updated_at: string
}

export interface CaseDesignTaskPage {
  items: CaseDesignTask[]
  total: number
  page: number
  page_size: number
  active_count: number
}

export interface RequirementCaseLink {
  id: number
  requirement_id: number
  requirement_version_id: number | null
  case_type: CaseType
  case_id: number
  relation_type: string
  source: string
  confidence: number
  created_at: string
}

export interface TestCaseVersion {
  id: number
  case_id: number
  version_no: number
  content: SuggestedCase
  change_note: string | null
  created_by: string
  created_at: string
}

export interface TestCaseAsset {
  id: number
  project_id: number
  api_definition_id: number | null
  code: string
  name: string
  case_type: CaseType
  status: 'ACTIVE' | 'ARCHIVED'
  source: 'AI' | 'MANUAL'
  current_version_id: number | null
  created_by: string
  created_at: string
  updated_at: string
  current_version?: TestCaseVersion | null
}
