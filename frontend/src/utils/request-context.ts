import type { CurrentUser } from '@/types/auth'

export interface RequestIdentity {
  token: string
  user: CurrentUser
  fingerprint: string
}

function isCurrentUser(value: unknown): value is CurrentUser {
  if (!value || typeof value !== 'object') return false
  const user = value as Partial<CurrentUser>
  return typeof user.id === 'string' && Boolean(user.id)
    && typeof user.username === 'string'
    && typeof user.display_name === 'string'
    && Array.isArray(user.roles)
    && user.roles.every((role) => typeof role === 'string')
}

export function readRequestIdentity(): RequestIdentity | null {
  const token = localStorage.getItem('access_token') ?? ''
  const rawUser = localStorage.getItem('current_user') ?? ''
  if (!token || !rawUser) return null
  try {
    const user: unknown = JSON.parse(rawUser)
    if (!isCurrentUser(user)) return null
    return { token, user, fingerprint: `${token}\u0000${rawUser}` }
  } catch {
    return null
  }
}

export function identityIsCurrent(identity: RequestIdentity): boolean {
  return readRequestIdentity()?.fingerprint === identity.fingerprint
}

export function isIdentityStorageEvent(event: StorageEvent): boolean {
  return event.storageArea === localStorage
    && (event.key === 'access_token' || event.key === 'current_user' || event.key === null)
}
