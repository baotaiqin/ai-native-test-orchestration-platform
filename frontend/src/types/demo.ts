export interface DemoAssetStatus {
  project: boolean
  environment: boolean
  model: boolean
  prompts: number
  prompt_total: number
  bindings: number
  binding_total: number
  requirement: boolean
  requirement_count: number
  openapi: boolean
  api_definition_count: number
  runtime_secret: boolean
}

export interface DemoBootstrapStatus {
  available: boolean
  ready: boolean
  project_id: number | null
  requirement_id: number | null
  model_id: number | null
  model_name: string | null
  provider_base_url: string | null
  demo_url: string
  has_api_key: boolean
  assets: DemoAssetStatus
  prompt_ids: Record<string, number>
  next_path: string
  message: string
}

export interface DemoProbe {
  success: boolean
  status: string
  duration_ms: number
  summary: string
  error_type: string | null
}

export interface DemoBootstrapResponse extends DemoBootstrapStatus {
  probe: DemoProbe
}

export interface DemoBootstrapPayload {
  model_id: number
}

export interface DemoResetCounts {
  ai_records: number
  test_cases: number
  scenarios: number
  runs: number
  evidence_files: number
  web_assets: number
  defects: number
  data_sources: number
  secrets: number
}

export interface DemoResetPreview {
  available: boolean
  project_id: number | null
  can_reset: boolean
  active_operations: number
  blockers: string[]
  delete_counts: DemoResetCounts
  preserved: string[]
}

export interface DemoResetResponse {
  reset: boolean
  deleted: DemoResetCounts
  status: DemoBootstrapStatus
  message: string
}
