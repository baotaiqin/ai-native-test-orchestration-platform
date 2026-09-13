export type ScenarioNodeType =
  | 'START' | 'END' | 'HTTP' | 'IF' | 'ELSE' | 'LOOP' | 'WAIT'
  | 'SET_VARIABLE' | 'EXTRACT' | 'ASSERT_STATUS' | 'ASSERT_JSONPATH' | 'AI_ASSERTION'
  | 'SQL_QUERY' | 'SQL_EXECUTE' | 'SQL_CLEANUP' | 'PYTHON_SCRIPT' | 'API_CLEANUP'

export interface ScenarioNode {
  id: string
  type: ScenarioNodeType
  name: string
  description?: string | null
  enabled: boolean
  parent_id: string | null
  config: Record<string, unknown>
  failure_policy: 'STOP' | 'CONTINUE' | 'RETRY_ONCE' | null
  timeout_ms: number
}

export interface ScenarioDsl {
  version: '1.0'
  nodes: ScenarioNode[]
  settings: {
    stop_on_failure: boolean
    cleanup_policy: 'ALWAYS' | 'ON_SUCCESS' | 'ON_FAILURE' | 'NEVER'
    max_loop_iterations: number
    initial_variables: string[]
  }
}

export interface ScenarioVersion {
  id: number
  scenario_id: number
  version_no: number
  dsl: ScenarioDsl
  change_note: string | null
  created_by: string
  created_at: string
}

export interface Scenario {
  id: number
  project_id: number
  code: string
  name: string
  status: 'DRAFT' | 'APPROVED' | 'ARCHIVED'
  current_version_id: number | null
  created_by: string
  created_at: string
  updated_at: string
  current_version?: ScenarioVersion | null
}

export interface ScenarioValidationIssue {
  severity: 'ERROR' | 'WARNING'
  code: string
  message: string
  node_id: string | null
}

export interface ScenarioValidationResult {
  valid: boolean
  issues: ScenarioValidationIssue[]
  node_count: number
}

export interface ScenarioExecutionTrace {
  sequence: number
  node_id: string
  node_type: ScenarioNodeType
  status: 'PASSED' | 'FAILED' | 'SKIPPED' | 'TIMEOUT'
  iteration_path: number[]
  detail: Record<string, unknown>
  attempt: number
  max_attempts: number
  duration_ms: number
  timeout_ms: number
  failure_policy: 'STOP' | 'CONTINUE' | 'RETRY_ONCE'
  error_code: string | null
  message: string | null
  terminal: boolean
  retryable: boolean
}

export type ScenarioPreviewMode = 'FULL' | 'NODE' | 'RUN_TO_HERE' | 'RUN_FROM_HERE'

export interface ScenarioExecutionPreview {
  status: 'PASS' | 'FAIL' | 'REVIEW'
  traces: ScenarioExecutionTrace[]
  context: Record<string, unknown>
  assertion_results?: import('@/types/test-case').AssertionResult[]
  final_status?: 'PASS' | 'FAIL' | 'REVIEW'
  mode: ScenarioPreviewMode
  target_node_id: string | null
  target_reached: boolean | null
  stop_reason: string | null
  stop_message: string | null
  execution_boundary: 'SCENARIO_PREVIEW_ONLY'
}

export type ScenarioCleanupOutcome = 'SUCCESS' | 'FAILURE' | 'CANCELLED' | 'TIMEOUT'

export interface ScenarioPreviewProfile {
  scenario_id: number
  scenario_version_id: number
  context: Record<string, unknown>
  responses_by_node: Record<string, import('@/types/test-case').RuntimeResponseSnapshot>
  cleanup_outcome: ScenarioCleanupOutcome
  source: 'SAVED' | 'GENERATED'
  saved_at: string | null
}

export interface ScenarioAiBaseline {
  scenario_id: number
  suggestion_id: number
  source_version_id: number
  name: string
  dsl: ScenarioDsl
}
