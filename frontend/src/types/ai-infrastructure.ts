import type { AiTaskType } from '@/types/model-center'

export interface OutputSchema {
  id: number
  name: string
  version_no: number
  description: string | null
  schema_json: Record<string, unknown>
  enabled: boolean
  created_by: string
  created_at: string
}

export interface AiCallLog {
  id: number
  project_id: number
  task_type: AiTaskType
  entity_type: string | null
  entity_id: string | null
  model_config_id: number
  actual_model: string
  prompt_version_id: number
  output_schema_id: number | null
  input_token: number
  output_token: number
  total_token: number
  estimated_cost: string
  latency_ms: number
  success: boolean
  fallback_used: boolean
  retry_count: number
  repair_used: boolean
  error_type: string | null
  response_id: string | null
  raw_response: string
  repair_response: string | null
  parsed_result: unknown
  validation_errors: string[]
  created_at: string
}

export interface StructuredValidationResult {
  ai_call_id: number
  success: boolean
  repair_used: boolean
  parsed_result: unknown
  validation_errors: string[]
  estimated_cost: string
}

export interface AiGenerateResult {
  ai_call_id: number
  success: boolean
  content: string
  parsed_result: unknown
  actual_model: string
  fallback_used: boolean
  repair_used: boolean
  input_token: number
  output_token: number
  total_token: number
  estimated_cost: string
  latency_ms: number
  response_id: string | null
}
