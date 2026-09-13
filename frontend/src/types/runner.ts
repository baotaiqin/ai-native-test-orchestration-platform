export type RunnerStatus = 'ACTIVE' | 'REVOKED'
export type OnlineStatus = 'ONLINE' | 'OFFLINE' | 'UNKNOWN'
export type RunnerCapabilityName = 'API' | 'WEB' | 'SQL' | 'SCRIPT' | 'SSE' | 'JMETER'
export type RunnerCapabilityStatus = 'READY' | 'UNAVAILABLE'
export type RunnerSlotType = 'API' | 'WEB' | 'PERFORMANCE'

export interface RunnerCapability {
  name: RunnerCapabilityName
  status: RunnerCapabilityStatus
  reason: string | null
}

export interface RunnerSlot {
  type: RunnerSlotType
  total: number
  available: number
}

export interface Runner {
  id: string
  name: string
  hostname: string
  ip_address: string | null
  os: string | null
  cpu: string | null
  ram: string | null
  disk: string | null
  python: string | null
  chrome: string | null
  playwright: string | null
  java: string | null
  jmeter: string | null
  status: RunnerStatus
  online: boolean
  online_status: OnlineStatus
  redis_available: boolean
  heartbeat_interval_seconds: number
  last_heartbeat_at: string | null
  revoked_at: string | null
  created_at: string
  updated_at: string
  tags: string[]
  capabilities: RunnerCapability[]
  slots: RunnerSlot[]
}

export interface RunnerListResponse {
  items: Runner[]
  total: number
}

export type RegistrationTokenStatus = 'ACTIVE' | 'CONSUMED' | 'REVOKED' | 'EXPIRED'

export interface RegistrationTokenCreateRequest {
  expires_in_seconds?: number | null
}

export interface RegistrationTokenCreateResponse {
  id: number
  token: string
  status: RegistrationTokenStatus
  expires_at: string
}

export interface RegistrationTokenMetadata {
  id: number
  status: RegistrationTokenStatus
  expires_at: string
  consumed_at: string | null
  revoked_at: string | null
  consumed_runner_id: string | null
  created_by: string
  created_at: string
}

export interface RegistrationTokenListResponse {
  items: RegistrationTokenMetadata[]
  total: number
}

