import { AI_GENERATION_REQUEST_TIMEOUT_MS, http } from '@/api/http'
import type { AiTaskType } from '@/types/model-center'
import type {
  AiCallLog, AiGenerateResult, OutputSchema, StructuredValidationResult,
} from '@/types/ai-infrastructure'

export async function getOutputSchemas(includeDisabled = true): Promise<OutputSchema[]> {
  const response = await http.get<{ items: OutputSchema[] }>('/ai/output-schemas', {
    params: { include_disabled: includeDisabled },
  })
  return response.data.items
}

export async function createOutputSchema(payload: {
  name: string
  description?: string
  schema_json: Record<string, unknown>
}): Promise<OutputSchema> {
  const response = await http.post<OutputSchema>('/ai/output-schemas', payload)
  return response.data
}

export async function createOutputSchemaVersion(
  id: number,
  payload: { description?: string; schema_json: Record<string, unknown> },
): Promise<OutputSchema> {
  const response = await http.post<OutputSchema>(
    `/ai/output-schemas/${id}/versions`, payload,
  )
  return response.data
}

export async function setOutputSchemaEnabled(
  id: number, enabled: boolean,
): Promise<OutputSchema> {
  const response = await http.patch<OutputSchema>(`/ai/output-schemas/${id}/enabled`, null, {
    params: { enabled },
  })
  return response.data
}

export async function validateStructuredOutput(payload: {
  project_id: number
  task_type: AiTaskType
  model_config_id: number
  prompt_version_id: number
  output_schema_id: number
  raw_output: string
  repair_output?: string
  input_token: number
  output_token: number
  latency_ms: number
}): Promise<StructuredValidationResult> {
  const response = await http.post<StructuredValidationResult>(
    '/ai/structured-output/validate', payload,
  )
  return response.data
}

export async function getAiCalls(projectId: number): Promise<AiCallLog[]> {
  const response = await http.get<{ items: AiCallLog[] }>('/ai/calls', {
    params: { project_id: projectId },
  })
  return response.data.items
}

export async function generateAi(payload: {
  project_id: number
  task_type: AiTaskType
  prompt_id: number
  variables: Record<string, unknown>
  entity_type?: string
  entity_id?: string
}): Promise<AiGenerateResult> {
  const response = await http.post<AiGenerateResult>('/ai/generate', payload, {
    timeout: AI_GENERATION_REQUEST_TIMEOUT_MS,
  })
  return response.data
}
