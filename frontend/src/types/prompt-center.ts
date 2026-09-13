import type { AiTaskType } from '@/types/model-center'

export interface PromptCreateRequest {
  name: string
  code: string
  task_type: AiTaskType
  description?: string | null
  system_prompt: string
  user_template: string
  output_schema_id: number | null
}

export interface PromptVersionCreateRequest {
  system_prompt: string
  user_template: string
  output_schema_id: number | null
  change_note?: string | null
}

export interface PromptVersion {
  id: number
  prompt_id: number
  version_no: number
  system_prompt: string
  user_template: string
  output_schema_id: number | null
  change_note: string | null
  created_by: string
  created_at: string
}

export interface PromptDefinition {
  id: number
  name: string
  code: string
  task_type: AiTaskType
  scope: 'SYSTEM' | 'PROJECT'
  project_id: number | null
  base_prompt_id: number | null
  is_builtin: boolean
  description: string | null
  enabled: boolean
  current_version_id: number | null
  created_by: string
  created_at: string
  updated_at: string
  current_version: PromptVersion | null
}

export interface ProjectPromptVersionCreateRequest {
  system_prompt: string
  user_template: string
  output_schema_id: number | null
  change_note: string
}

export interface ProjectPromptTemplate {
  base_prompt_id: number
  name: string
  code: string
  task_type: AiTaskType
  description: string | null
  using_system_default: boolean
  project_prompt_id: number | null
  project_version_no: number | null
  system_prompt: string
  user_template: string
  output_schema_id: number | null
  change_note: string | null
  updated_at: string
}

export interface PromptRenderResult {
  prompt_id: number
  prompt_version_id: number
  system_prompt: string
  user_prompt: string
  required_variables: string[]
  missing_variables: string[]
}
