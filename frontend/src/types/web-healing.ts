import type { LocatorStrategy } from './web'

export type HealingLocatorStrategy = Exclude<LocatorStrategy, 'xpath' | 'text'>
export type HealingLocatorSource = 'MANUAL' | 'IMPORTED' | 'HEALED'
export type WebHealingProposalStatus = 'DRAFT' | 'ACCEPTED' | 'REJECTED'
export type HealingAnalysisStage = 'LLM_DOM' | 'VISION_SCREENSHOT' | 'AGENT_LOCATE'

export interface WebHealingCandidate {
  tag: string
  role?: string
  id?: string
  name?: string
  'aria-label'?: string
  placeholder?: string
  'data-testid'?: string
  type?: string
  title?: string
}

export interface WebHealingContext {
  schema_version: 1
  trigger: 'ALL_LOCATORS_FAILED'
  element_version_id: number | null
  page_url: string | null
  page_title: string
  dom_candidates: WebHealingCandidate[]
}

export interface HealingLocator {
  strategy: HealingLocatorStrategy
  value: string
}

export interface HealingSourceLocator {
  strategy: LocatorStrategy
  value: string
  priority?: number
  source?: HealingLocatorSource
}

export interface HealingLocatorReference {
  element_version_id: number
  locators: HealingSourceLocator[]
}

export type HealingOldLocator = HealingLocator | HealingLocatorReference | HealingSourceLocator

export interface HealingCandidateLocator {
  candidate_index: number
  locator: HealingLocator
  similarity_score: number
}

export interface WebHealingProposalCreateRequest {
  case_run_id: number
  node_id: string
  prompt_id: number
  additional_instructions?: string | null
  analysis_stage?: HealingAnalysisStage
  screenshot_artifact_id?: string | null
}

export interface WebHealingProposalDecisionRequest {
  locator?: HealingLocator | null
  decision_note?: string | null
}

export interface WebHealingProposalValidateRequest {
  locator?: HealingLocator | null
}

export interface WebHealingValidationResponse {
  id: number
  run_id: string
  run_code: string
  run_status: string
  locator: HealingLocator
  created_by: string
  created_at: string
}

export interface WebHealingProposalRejectRequest {
  decision_note?: string | null
}

export interface WebHealingProposalResponse {
  schema_version: 1
  id: number
  project_id: number
  run_id: string
  case_run_id: number
  web_case_id: number
  web_case_version_id: number
  node_id: string
  status: WebHealingProposalStatus
  ai_call_id: number | null
  analysis_stage: HealingAnalysisStage
  screenshot_artifact_id: string | null
  actual_model: string | null
  prompt_version_id: number | null
  output_schema_id: number | null
  fallback_used: boolean | null
  repair_used: boolean | null
  source_snapshot_sha256: string
  source_snapshot_size: number
  old_locator: HealingOldLocator
  proposed_locator: HealingLocator | null
  human_locator: HealingLocator | null
  candidate_locators: HealingCandidateLocator[]
  confidence: number
  reason: string
  evidence_candidate_index: number
  decision_note: string | null
  created_element_version_id: number | null
  created_web_case_version_id: number | null
  latest_validation: WebHealingValidationResponse | null
  validated_locators: HealingLocator[]
  created_by: string
  reviewed_by: string | null
  created_at: string
  reviewed_at: string | null
  idempotent: boolean
}

export interface WebHealingProposalListResponse {
  items: WebHealingProposalResponse[]
  total: number
}
