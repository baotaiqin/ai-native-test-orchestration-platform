export type ProjectStatus = 'ACTIVE' | 'ARCHIVED'

export interface Project {
  id: number
  name: string
  code: string
  description: string | null
  status: ProjectStatus
  owner_id: string
  created_at: string
  updated_at: string
  archived_at: string | null
  archived_at_basis: 'UTC' | 'LEGACY_LOCAL_UNKNOWN' | null
  current_user_role: ProjectRole | null
}

export interface ProjectListResponse {
  items: Project[]
  total: number
}

export interface ProjectPayload {
  name: string
  code: string
  description?: string | null
}

export type ProjectRole = 'PROJECT_OWNER' | 'TESTER' | 'VIEWER'

export interface ProjectMember {
  project_id: number
  user_id: string
  role: ProjectRole
  created_at: string
}
