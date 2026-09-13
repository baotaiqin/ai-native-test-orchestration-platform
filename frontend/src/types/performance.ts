import type { RunDetail, RunDispatchResponse } from '@/types/run'

export type PerformanceTargetType = 'API_CASE' | 'SCENARIO'
export type PerformanceEngine = 'PYTHON_HTTP' | 'PYTHON_STREAM' | 'JMETER'
export type PerformanceLoadMode =
  | 'FIXED_ITERATIONS'
  | 'FIXED_RPS'
  | 'FIXED_CONCURRENCY'
  | 'STEP_LOAD'
export type PerformanceCleanupMode = 'NONE' | 'RESOURCE_CLEANUP' | 'BATCH_CLEANUP'

export interface PerformanceStepStage {
  concurrency: number
  duration_seconds: number
}

export interface PerformanceStreamConfig {
  protocol: 'SSE' | 'NDJSON'
  completion_marker: string
  require_completion_marker: boolean
}

export interface PerformanceSla {
  max_error_rate?: number | null
  max_p95_ms?: number | null
  max_p99_ms?: number | null
  max_average_ms?: number | null
  max_ttft_p95_ms?: number | null
  min_rps?: number | null
  min_success_rate?: number | null
}

export interface PerformanceProfile {
  id: number
  project_id: number
  name: string
  description: string | null
  target_type: PerformanceTargetType
  case_id: number | null
  case_version_id: number | null
  scenario_id: number | null
  scenario_version_id: number | null
  concurrency: number
  iterations: number
  load_mode: PerformanceLoadMode
  target_rps: number | null
  duration_seconds: number | null
  step_stages: PerformanceStepStage[] | null
  warmup_iterations: number
  request_timeout_ms: number
  engine: PerformanceEngine
  cleanup_mode: PerformanceCleanupMode
  stream_config: PerformanceStreamConfig
  sla: PerformanceSla
  status: 'ACTIVE' | 'ARCHIVED'
  created_by: string
  created_at: string
  updated_at: string
}

export interface PerformanceTrendPoint {
  second: number
  requests: number
  success: number
  fail: number
  average_ms: number
  p95_ms: number
}

export interface PerformanceMetrics {
  request_count: number
  success_count: number
  fail_count: number
  success_rate: number
  error_rate: number
  rps: number
  tps: number
  min_ms: number
  max_ms: number
  average_ms: number
  p50_ms: number
  p75_ms: number
  p90_ms: number
  p95_ms: number
  p99_ms: number
  received_bytes: number
  sent_bytes: number
  active_users_peak: number
  wall_duration_ms: number
  ttft_average_ms: number | null
  ttft_p95_ms: number | null
  stream_duration_average_ms: number | null
  input_tokens: number | null
  output_tokens: number | null
  tokens_per_second: number | null
  chunk_count: number | null
  max_chunk_gap_ms: number | null
  interrupted_streams: number | null
  trends: PerformanceTrendPoint[]
}

export interface PerformanceSlaResult {
  metric: string
  operator: 'LTE' | 'GTE'
  threshold: number
  actual: number
  passed: boolean
}

export interface PerformanceRun {
  run_id: string
  profile_id: number
  project_id: number
  run_code: string
  status: string
  config_snapshot: Record<string, unknown>
  metrics: PerformanceMetrics | null
  sla_results: PerformanceSlaResult[]
  error_distribution: Record<string, number>
  created_at: string
  completed_at: string | null
}

export interface PerformanceRunStartResponse {
  run: RunDetail
  dispatch: RunDispatchResponse
}

export interface PerformanceRunnerOption {
  id: string
  name: string
  performance_slots_available: number
  ready_capabilities: string[]
}

export interface PerformanceComparisonItem {
  run_id: string
  run_code: string
  status: string
  metrics: PerformanceMetrics
  delta_from_baseline: Record<string, number>
}

export interface PerformanceComparison {
  baseline_run_id: string
  items: PerformanceComparisonItem[]
}

export type PerformanceAnalysisVerdict = 'MEETS_SLA' | 'MISSES_SLA' | 'NO_SLA'

export interface PerformanceAnalysisFinding {
  category: 'LATENCY' | 'THROUGHPUT' | 'ERRORS' | 'STABILITY' | 'STREAMING' | 'CAPACITY'
  severity: 'INFO' | 'WARNING' | 'CRITICAL'
  metric: string
  observed: number
  observation: string
  recommendation: string
}

export interface PerformanceBottleneckHypothesis {
  description: string
  evidence_metrics: string[]
  confidence: number
  validation_steps: string[]
}

export interface PerformanceAnalysisResult {
  verdict: PerformanceAnalysisVerdict
  summary: string
  findings: PerformanceAnalysisFinding[]
  bottleneck_hypotheses: PerformanceBottleneckHypothesis[]
  recommendations: string[]
  confidence: number
  needs_human_review: boolean
}

export interface PerformanceAnalysis {
  id: number
  project_id: number
  run_id: string
  status: 'DRAFT' | 'COMPLETED'
  ai_call_id: number | null
  actual_model: string | null
  prompt_version_id: number
  output_schema_id: number
  fallback_used: boolean | null
  repair_used: boolean | null
  source_snapshot_sha256: string
  source_snapshot_size: number
  source_sla_verdict: PerformanceAnalysisVerdict
  structured_result: PerformanceAnalysisResult | null
  created_by: string
  created_at: string
}
