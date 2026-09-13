export interface DatabaseConnection {
  id: number
  project_id: number
  environment_id: number
  name: string
  host: string
  port: number
  database_name: string
  username: string
  password_secret_id: number
  password_masked: string
  ssl_enabled: boolean
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface DatabaseConnectionPayload {
  project_id: number
  environment_id: number
  name: string
  host: string
  port: number
  database_name: string
  username: string
  password_secret_id: number
  ssl_enabled: boolean
}

export interface ConnectionTestResult {
  status: 'ok' | 'failed'
  latency_ms: number
  database: string | null
  server_version: string | null
  message: string
}
