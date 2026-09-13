export type SecretType = 'PASSWORD' | 'TOKEN' | 'API_KEY' | 'DB_PASSWORD' | 'CLIENT_SECRET'

export interface Secret {
  id: number
  project_id: number
  environment_id: number | null
  name: string
  secret_type: SecretType
  masked_value: string
  enabled: boolean
  created_at: string
  updated_at: string
  rotated_at: string | null
}

export interface SecretPayload {
  project_id: number
  environment_id?: number | null
  name: string
  secret_type: SecretType
  value: string
}
