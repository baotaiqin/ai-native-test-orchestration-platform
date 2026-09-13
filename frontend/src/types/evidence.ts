export type EvidenceArtifactType =
  | 'RESPONSE'
  | 'ASSERTION_RESULT'
  | 'SCREENSHOT'
  | 'PLAYWRIGHT_TRACE'
  | 'CONSOLE_ERROR'
  | 'NETWORK_ERROR'
  | 'WEB_SUMMARY'

export interface EvidenceArtifact {
  id: string
  project_id: number
  run_id: string
  case_run_id: number
  step_run_id: number | null
  artifact_type: EvidenceArtifactType
  file_name: string
  mime: string
  size: number
  sha256: string
  metadata: Record<string, unknown> | null
  created_at: string
}

export interface EvidenceArtifactListResponse {
  items: EvidenceArtifact[]
  total: number
  page: number
  page_size: number
}
