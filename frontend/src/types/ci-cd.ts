export interface CiTokenCreate {
  project_id: number
  name: string
  expires_in_days: number
  allowed_plan_ids?: number[]
}

export interface CiTokenMetadata {
  id: number
  project_id: number
  name: string
  token_prefix: string
  allowed_plan_ids: number[] | null
  status: 'ACTIVE' | 'REVOKED' | 'EXPIRED'
  expires_at: string
  last_used_at: string | null
  revoked_at: string | null
  created_by: string
  created_at: string
}

export interface CiTokenCreated {
  token: string
  metadata: CiTokenMetadata
}

export interface WebhookEndpointCreate {
  project_id: number
  name: string
  url: string
  signing_secret?: string
  events: ['TEST_PLAN_RUN_COMPLETED']
  enabled: boolean
  max_attempts: number
  timeout_seconds: number
}

export interface WebhookEndpoint {
  id: number
  project_id: number
  name: string
  target_hint: string
  has_signing_secret: boolean
  events: string[]
  enabled: boolean
  status: 'ACTIVE' | 'ARCHIVED'
  max_attempts: number
  timeout_seconds: number
  created_by: string
  created_at: string
  updated_at: string
}

export interface WebhookDelivery {
  id: string
  endpoint_id: number
  endpoint_name: string
  project_id: number
  plan_run_id: string
  event_id: string
  event_type: string
  status: 'PENDING' | 'SENDING' | 'RETRY' | 'SUCCEEDED' | 'FAILED'
  attempt_count: number
  next_attempt_at: string
  response_status: number | null
  last_error_code: string | null
  last_error_message: string | null
  last_attempt_at: string | null
  created_at: string
  completed_at: string | null
}
