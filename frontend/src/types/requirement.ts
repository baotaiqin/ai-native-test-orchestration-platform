export type RequirementType = 'FEATURE' | 'RULE' | 'ACCEPTANCE_CRITERIA' | 'SECTION'
export type RequirementVerificationType = 'AUTO' | 'API' | 'WEB' | 'PERFORMANCE' | 'PLATFORM' | 'MANUAL'
export type RequirementAutomationReadiness = 'READY' | 'NEEDS_CLARIFICATION' | 'MANUAL_ONLY'

export interface RequirementVersion {
  id: number
  requirement_id: number
  version_no: number
  markdown_content: string
  content_hash: string
  source_type: 'MANUAL' | 'MARKDOWN'
  source_filename: string | null
  change_summary: string | null
  created_by: string
  created_at: string
}

export interface Requirement {
  id: number
  project_id: number
  parent_id: number | null
  code: string
  title: string
  type: RequirementType
  verification_type: RequirementVerificationType
  automation_readiness: RequirementAutomationReadiness
  order_index: number
  status: 'ACTIVE' | 'ARCHIVED'
  current_version_id: number | null
  created_by: string
  created_at: string
  updated_at: string
  current_version: RequirementVersion | null
}

export interface RequirementTreeNode extends Requirement {
  outline_number: string
  children: RequirementTreeNode[]
}

export interface RequirementDocumentSnapshotNode {
  requirement_id: number
  code: string
  parent_id: number | null
  outline_number: string
  title: string
  type: RequirementType
  verification_type: RequirementVerificationType
  automation_readiness: RequirementAutomationReadiness
  order_index: number
  markdown_content: string
  technical_version_id: number | null
  technical_version_no: number | null
}

export interface RequirementDocumentVersion {
  id: number
  project_id: number
  version_no: number
  snapshot: RequirementDocumentSnapshotNode[]
  content_hash: string
  source_type: 'MANUAL' | 'MARKDOWN'
  source_filename: string | null
  change_summary: string | null
  source_review_id: number | null
  created_by: string
  created_at: string
}

export interface RequirementDocumentDraftNode {
  client_id: string
  requirement_id?: number
  parent_client_id: string | null
  title: string
  type: RequirementType
  verification_type: RequirementVerificationType
  automation_readiness: RequirementAutomationReadiness
  markdown_content: string
  order_index: number
}

export interface RequirementTreeState {
  items: RequirementTreeNode[]
  total: number
  current_document_version: RequirementDocumentVersion | null
}

export interface MarkdownPreviewNode {
  temp_id: string
  parent_temp_id: string | null
  title: string
  level: number
  markdown_content: string
  order_index: number
}

export interface RequirementDiff {
  requirement_id: number
  from_version: number
  to_version: number
  unified_diff: string
  additions: number
  deletions: number
}
