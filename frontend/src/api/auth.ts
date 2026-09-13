import { http } from './http'
import type {
  ChangePasswordPayload,
  ChangePasswordResponse,
  CurrentUser,
  LoginPayload,
  LogoutResponse,
  ManagedUser,
  TokenResponse,
  UserCreatePayload,
  UserListQuery,
  UserListResponse,
  UserUpdatePayload,
} from '@/types/auth'

export async function login(payload: LoginPayload): Promise<TokenResponse> {
  const response = await http.post<TokenResponse>('/auth/login', payload)
  return response.data
}

export async function getCurrentUser(): Promise<CurrentUser> {
  const response = await http.get<CurrentUser>('/auth/me')
  return response.data
}

export async function logout(): Promise<LogoutResponse> {
  const response = await http.post<LogoutResponse>('/auth/logout')
  return response.data
}

export async function changePassword(payload: ChangePasswordPayload): Promise<ChangePasswordResponse> {
  const response = await http.post<ChangePasswordResponse>('/auth/change-password', payload)
  return response.data
}

export async function getUsers(query: UserListQuery = {}): Promise<UserListResponse> {
  const response = await http.get<UserListResponse>('/auth/users', {
    params: { page: query.page ?? 1, page_size: query.page_size ?? 20 },
  })
  return response.data
}

export async function createUser(payload: UserCreatePayload): Promise<ManagedUser> {
  const response = await http.post<ManagedUser>('/auth/users', payload)
  return response.data
}

export async function updateUser(userId: string, payload: UserUpdatePayload): Promise<ManagedUser> {
  const response = await http.patch<ManagedUser>(`/auth/users/${encodeURIComponent(userId)}`, payload)
  return response.data
}
