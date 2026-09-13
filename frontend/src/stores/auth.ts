import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import * as authApi from '@/api/auth'
import type { ChangePasswordPayload, CurrentUser, LoginPayload } from '@/types/auth'

const TOKEN_KEY = 'access_token'
const USER_KEY = 'current_user'
const EPOCH_KEY = 'auth_identity_epoch'
const STORED_EPOCH_FIELD = '__identity_epoch'

let epochCounter = 0

interface PersistedIdentity {
  token: string
  user: CurrentUser
  epoch: string
}

interface StoredCurrentUser extends CurrentUser {
  __identity_epoch?: unknown
}

interface IdentityWriteContext {
  id: number
  kind: 'logout' | 'change-password'
  token: string
  epoch: string
}

export interface ServerLogoutResult {
  revoked: boolean
  local_cleared: boolean
}

export interface PasswordChangeResult {
  changed: boolean
  reauthentication_required: boolean
  local_cleared: boolean
}

function newIdentityEpoch(): string {
  epochCounter += 1
  const random = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Math.random().toString(36).slice(2)}-${epochCounter}`
  return `${Date.now().toString(36)}-${random}`
}

function validCurrentUser(value: unknown): value is CurrentUser {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<CurrentUser>
  return typeof candidate.id === 'string' && Boolean(candidate.id)
    && typeof candidate.username === 'string'
    && typeof candidate.display_name === 'string'
    && Array.isArray(candidate.roles)
    && candidate.roles.every((role) => typeof role === 'string')
}

function storedUser(user: CurrentUser, epoch: string): CurrentUser & { __identity_epoch: string } {
  return { ...user, roles: [...user.roles], [STORED_EPOCH_FIELD]: epoch }
}

function sameAccountIdentity(currentUser: CurrentUser, nextUser: CurrentUser): boolean {
  return currentUser.id === nextUser.id && currentUser.username === nextUser.username
}

function sameCurrentUser(currentUser: CurrentUser, nextUser: CurrentUser): boolean {
  return sameAccountIdentity(currentUser, nextUser)
    && currentUser.display_name === nextUser.display_name
    && currentUser.roles.length === nextUser.roles.length
    && currentUser.roles.every((role, index) => role === nextUser.roles[index])
}

function persistIdentity(identity: PersistedIdentity): void {
  localStorage.setItem(EPOCH_KEY, identity.epoch)
  localStorage.setItem(TOKEN_KEY, identity.token)
  localStorage.setItem(USER_KEY, JSON.stringify(storedUser(identity.user, identity.epoch)))
}

function clearCorruptPersistedIdentity(): string {
  const epoch = newIdentityEpoch()
  localStorage.setItem(EPOCH_KEY, epoch)
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
  return epoch
}

function readPersistedIdentity(): PersistedIdentity | null {
  const token = localStorage.getItem(TOKEN_KEY) ?? ''
  const rawUser = localStorage.getItem(USER_KEY) ?? ''
  if (!token && !rawUser) return null
  if (!token || !rawUser) {
    clearCorruptPersistedIdentity()
    return null
  }
  try {
    const parsed: unknown = JSON.parse(rawUser)
    if (!validCurrentUser(parsed)) {
      clearCorruptPersistedIdentity()
      return null
    }
    const embeddedEpoch = (parsed as StoredCurrentUser).__identity_epoch
    const persistedEpoch = localStorage.getItem(EPOCH_KEY)
    const epoch = typeof embeddedEpoch === 'string' && embeddedEpoch
      ? embeddedEpoch
      : persistedEpoch || newIdentityEpoch()
    const currentUser: CurrentUser = {
      id: parsed.id,
      username: parsed.username,
      display_name: parsed.display_name,
      roles: [...parsed.roles],
    }
    if (persistedEpoch !== epoch || embeddedEpoch !== epoch) {
      persistIdentity({ token, user: currentUser, epoch })
    }
    return { token, user: currentUser, epoch }
  } catch {
    clearCorruptPersistedIdentity()
    return null
  }
}

const initialIdentity = readPersistedIdentity()

export const useAuthStore = defineStore('auth', () => {
  const token = ref(initialIdentity?.token ?? '')
  const user = ref<CurrentUser | null>(initialIdentity?.user ?? null)
  const identityEpoch = ref(initialIdentity?.epoch ?? localStorage.getItem(EPOCH_KEY) ?? newIdentityEpoch())
  const identityVerified = ref(false)
  const identityWrite = ref<IdentityWriteContext['kind'] | null>(null)
  const isAuthenticated = computed(() => Boolean(token.value && user.value))
  const isAdmin = computed(() => identityVerified.value && Boolean(user.value?.roles.includes('ADMIN')))
  let identityOperationSequence = 0
  let identityWriteSequence = 0
  let storageSyncTimer: number | null = null
  let refreshPromise: Promise<boolean> | null = null
  let refreshEpoch: string | null = null

  function persistedEpoch(): string {
    return localStorage.getItem(EPOCH_KEY) ?? ''
  }

  function identityMatches(expectedEpoch: string, expectedToken: string): boolean {
    return identityEpoch.value === expectedEpoch
      && token.value === expectedToken
      && persistedEpoch() === expectedEpoch
      && (localStorage.getItem(TOKEN_KEY) ?? '') === expectedToken
  }

  function invalidatePendingOperations(): void {
    identityOperationSequence += 1
    identityWriteSequence += 1
    identityWrite.value = null
    refreshPromise = null
    refreshEpoch = null
  }

  function commitNewIdentity(nextToken: string, nextUser: CurrentUser, verified: boolean): void {
    const nextEpoch = newIdentityEpoch()
    const committedUser = { ...nextUser, roles: [...nextUser.roles] }
    invalidatePendingOperations()
    token.value = nextToken
    user.value = committedUser
    identityEpoch.value = nextEpoch
    identityVerified.value = verified
    persistIdentity({ token: nextToken, user: committedUser, epoch: nextEpoch })
  }

  function commitRefreshedIdentity(
    expectedToken: string,
    expectedEpoch: string,
    nextUser: CurrentUser,
  ): void {
    const currentUser = user.value
    if (!currentUser || !sameAccountIdentity(currentUser, nextUser)) {
      commitNewIdentity(expectedToken, nextUser, true)
      return
    }
    if (sameCurrentUser(currentUser, nextUser)) {
      identityVerified.value = true
      return
    }
    const refreshedUser = { ...nextUser, roles: [...nextUser.roles] }
    invalidatePendingOperations()
    token.value = expectedToken
    user.value = refreshedUser
    identityEpoch.value = expectedEpoch
    identityVerified.value = true
    persistIdentity({ token: expectedToken, user: refreshedUser, epoch: expectedEpoch })
  }

  function signOut(expectedEpoch?: string): boolean {
    if (expectedEpoch !== undefined
      && (identityEpoch.value !== expectedEpoch || persistedEpoch() !== expectedEpoch)) return false
    if (!localStorage.getItem(TOKEN_KEY) && !localStorage.getItem(USER_KEY)) {
      invalidatePendingOperations()
      token.value = ''
      user.value = null
      identityEpoch.value = persistedEpoch() || identityEpoch.value
      identityVerified.value = false
      return true
    }
    const nextEpoch = clearCorruptPersistedIdentity()
    invalidatePendingOperations()
    token.value = ''
    user.value = null
    identityEpoch.value = nextEpoch
    identityVerified.value = false
    return true
  }

  function syncFromStorage(): void {
    const persisted = readPersistedIdentity()
    invalidatePendingOperations()
    token.value = persisted?.token ?? ''
    user.value = persisted?.user ?? null
    identityEpoch.value = persisted?.epoch ?? localStorage.getItem(EPOCH_KEY) ?? newIdentityEpoch()
    identityVerified.value = false
    if (persisted) void refreshUser().catch(() => undefined)
  }

  function onStorageChange(event: StorageEvent): void {
    if (event.storageArea && event.storageArea !== localStorage) return
    if (event.key !== null && ![TOKEN_KEY, USER_KEY, EPOCH_KEY].includes(event.key)) return
    if (storageSyncTimer !== null) window.clearTimeout(storageSyncTimer)
    storageSyncTimer = window.setTimeout(() => {
      storageSyncTimer = null
      syncFromStorage()
    }, 0)
  }

  async function signIn(payload: LoginPayload): Promise<boolean> {
    const operationSequence = ++identityOperationSequence
    const epochAtStart = persistedEpoch()
    const result = await authApi.login(payload)
    if (operationSequence !== identityOperationSequence || epochAtStart !== persistedEpoch()) return false
    commitNewIdentity(result.access_token, result.user, true)
    return true
  }

  async function refreshUser(): Promise<boolean> {
    if (!token.value || !user.value) return false
    const expectedEpoch = identityEpoch.value
    const expectedToken = token.value
    if (refreshPromise && refreshEpoch === expectedEpoch) return refreshPromise
    const operationSequence = ++identityOperationSequence
    identityVerified.value = false
    const operation = (async () => {
      const currentUser = await authApi.getCurrentUser()
      if (operationSequence !== identityOperationSequence
        || !identityMatches(expectedEpoch, expectedToken)) return false
      commitRefreshedIdentity(expectedToken, expectedEpoch, currentUser)
      return true
    })()
    refreshPromise = operation
    refreshEpoch = expectedEpoch
    return operation.finally(() => {
      if (refreshPromise === operation) {
        refreshPromise = null
        refreshEpoch = null
      }
    })
  }

  async function ensureCurrentUser(): Promise<boolean> {
    if (!isAuthenticated.value) return false
    if (identityVerified.value) return true
    return refreshUser()
  }

  function beginIdentityWrite(kind: IdentityWriteContext['kind']): IdentityWriteContext | null {
    if (identityWrite.value || !token.value || !identityVerified.value) return null
    const context = {
      id: ++identityWriteSequence,
      kind,
      token: token.value,
      epoch: identityEpoch.value,
    }
    identityWrite.value = kind
    return context
  }

  function identityWriteIsCurrent(context: IdentityWriteContext): boolean {
    return context.id === identityWriteSequence
      && context.kind === identityWrite.value
      && identityMatches(context.epoch, context.token)
      && identityVerified.value
  }

  async function logoutFromServer(): Promise<ServerLogoutResult> {
    const context = beginIdentityWrite('logout')
    if (!context) return { revoked: false, local_cleared: false }
    try {
      const result = await authApi.logout()
      if (!identityWriteIsCurrent(context)) return { revoked: result.revoked, local_cleared: false }
      if (!result.revoked) return { revoked: false, local_cleared: false }
      return { revoked: true, local_cleared: signOut(context.epoch) }
    } finally {
      if (context.id === identityWriteSequence && identityWrite.value === context.kind) {
        identityWrite.value = null
      }
    }
  }

  async function changePassword(payload: ChangePasswordPayload): Promise<PasswordChangeResult> {
    const context = beginIdentityWrite('change-password')
    if (!context) return { changed: false, reauthentication_required: false, local_cleared: false }
    try {
      const result = await authApi.changePassword(payload)
      if (!identityWriteIsCurrent(context)) return { ...result, local_cleared: false }
      const localCleared = result.changed && result.reauthentication_required
        ? signOut(context.epoch)
        : false
      return { ...result, local_cleared: localCleared }
    } finally {
      if (context.id === identityWriteSequence && identityWrite.value === context.kind) {
        identityWrite.value = null
      }
    }
  }

  window.addEventListener('storage', onStorageChange)

  return {
    token,
    user,
    identityEpoch,
    identityVerified,
    identityWrite,
    isAuthenticated,
    isAdmin,
    signIn,
    refreshUser,
    ensureCurrentUser,
    logoutFromServer,
    changePassword,
    signOut,
  }
})
