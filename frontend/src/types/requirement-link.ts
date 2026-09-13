export type RequirementAssetType = 'TEST_CASE' | 'WEB_CASE'
export type RequirementLinkStatus = 'ACTIVE' | 'REMOVED'
export type RequirementAuditTimeBasis = 'UTC' | 'LEGACY_UNKNOWN'
export type RequirementImpactStatus = 'NO_CHANGE' | 'POSSIBLY_OUTDATED' | 'BASELINE_UNKNOWN'

export interface RequirementVersionReference {
  id: number
  version_no: number
  content_hash: string
}

export interface LinkedAssetReference {
  asset_type: RequirementAssetType
  id: number
  code: string
  name: string
  status: string
  case_type: string | null
  current_version_id: number | null
}

export interface LinkedAssetVersionReference {
  id: number
  version_no: number
  status: string | null
}

export interface RequirementLink {
  id: number
  requirement_id: number
  requirement_code: string
  requirement_title: string
  requirement_status: string
  requirement_version_id: number | null
  requirement_version: RequirementVersionReference | null
  requirement_baseline_known: boolean
  asset_type: RequirementAssetType
  asset_id: number
  asset: LinkedAssetReference
  asset_version_id: number | null
  asset_version: LinkedAssetVersionReference | null
  asset_version_known: boolean
  binding_note: string
  relation_type: string
  source: string
  confidence: number
  status: RequirementLinkStatus
  is_current_relation: boolean
  supersedes_link_id: number | null
  created_by: string | null
  created_at_time_basis: RequirementAuditTimeBasis
  created_at: string
  removed_by: string | null
  removed_at: string | null
  removed_at_time_basis: 'UTC' | null
  historical_scope: 'CURRENT_ASSOCIATION_NOT_RUN_SNAPSHOT'
}

export interface RequirementLinkPage {
  items: RequirementLink[]
  total: number
  page: number
  page_size: number
  has_more: boolean
}

export interface RequirementLinkCreatePayload {
  asset_type: RequirementAssetType
  asset_id: number
  requirement_version_id: number
  asset_version_id: number
  relation_type: 'COVERAGE'
  confidence: number
}

export interface RequirementImpactComparison {
  requirement_id: number
  from_version: RequirementVersionReference
  to_version: RequirementVersionReference
  content_changed: boolean
  additions: number
  deletions: number
}

export interface RequirementImpactItem extends RequirementLink {
  selected_scope_status: RequirementImpactStatus
  selected_scope_reason: string
  recorded_baseline_status: RequirementImpactStatus
  recorded_baseline_reason: string
}

export interface RequirementImpactPage {
  comparison: RequirementImpactComparison
  items: RequirementImpactItem[]
  total: number
  page: number
  page_size: number
  has_more: boolean
  scope_note: string
}

export interface RequirementTraceItem {
  source_id: number
  capture_id: number
  requirement_id: number
  requirement_version_id: number | null
  requirement_version_no: number | null
  requirement_version_binding: string
  target_type: string
  target_asset_id: number
  target_version_id: number
  asset_version_binding: string
  run_id: string
  run_code: string
  run_type: string
  run_status: string
  run_created_at: string
  run_ended_at: string | null
  case_run_id: number
  case_sequence_no: number
  case_status: string
  evidence_count: number
  captured_at: string
  report_path: string
  evidence_path: string
  historical_scope: 'RUN_CREATION_SNAPSHOT'
}

export interface RequirementTracePage {
  items: RequirementTraceItem[]
  total: number
  page: number
  page_size: number
  has_more: boolean
  scope_note: string
}
