export type VariableType = 'STRING' | 'NUMBER' | 'BOOLEAN' | 'JSON' | 'LIST'

export interface Environment {
  id: number
  project_id: number
  name: string
  code: string
  base_url: string | null
  description: string | null
  is_default: boolean
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface EnvironmentPayload {
  project_id: number
  name: string
  code: string
  base_url?: string | null
  description?: string | null
  is_default?: boolean
}

export interface EnvironmentVariable {
  id: number
  environment_id: number
  key: string
  value: string
  value_type: VariableType
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface VariablePayload {
  value: string
  value_type: VariableType
  enabled: boolean
}
