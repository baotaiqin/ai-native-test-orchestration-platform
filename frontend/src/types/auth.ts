export interface LoginPayload {
  username: string
  password: string
}

export interface CurrentUser {
  id: string
  username: string
  display_name: string
  roles: string[]
}

export interface TokenResponse {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  user: CurrentUser
}

export type PlatformRole = 'ADMIN' | 'USER'
export type UserStatus = 'ACTIVE' | 'DISABLED'

export interface LogoutResponse {
  revoked: boolean
}

export interface ChangePasswordPayload {
  current_password: string
  new_password: string
}

export interface ChangePasswordResponse {
  changed: boolean
  reauthentication_required: boolean
}

export interface ManagedUser {
  id: string
  username: string
  display_name: string
  status: UserStatus
  platform_role: PlatformRole
  auth_version: number
  created_at: string
  updated_at: string
}

export interface UserListQuery {
  page?: number
  page_size?: number
}

export interface UserListResponse {
  items: ManagedUser[]
  total: number
  page: number
  page_size: number
}

export interface UserCreatePayload {
  username: string
  display_name: string
  password: string
  platform_role: PlatformRole
  status: UserStatus
}

export interface UserUpdatePayload {
  display_name?: string
  platform_role?: PlatformRole
  status?: UserStatus
}
