import axios from 'axios'

interface IdentityBoundRequestConfig {
  __authIdentityEpochAtDispatch?: string
  __authStorageGenerationAtDispatch?: number
  __authTokenAtDispatch?: string
  url?: string
}

let authStorageGeneration = 0

function nextInvalidIdentityEpoch(): string {
  const random = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2)
  return `${Date.now().toString(36)}-${random}`
}

function clearRejectedIdentity(): void {
  ++authStorageGeneration
  localStorage.setItem('auth_identity_epoch', nextInvalidIdentityEpoch())
  localStorage.removeItem('access_token')
  localStorage.removeItem('current_user')
}

window.addEventListener('storage', (event) => {
  if (event.storageArea && event.storageArea !== localStorage) return
  if (event.key === null || event.key === 'access_token' || event.key === 'current_user'
    || event.key === 'auth_identity_epoch') {
    ++authStorageGeneration
  }
})

export interface ApiErrorBody {
  code: string
  message: string
  request_id: string
  details?: unknown
}

export const DEFAULT_REQUEST_TIMEOUT_MS = 15_000
export const AI_GENERATION_REQUEST_TIMEOUT_MS = 180_000
export const MODEL_CONNECTION_REQUEST_TIMEOUT_MS = 75_000

export function getApiErrorMessage(error: unknown, fallback: string): string {
  const transportError = error as {
    code?: string
    message?: string
    response?: { status?: number; data?: unknown }
  }
  const body = transportError.response?.data
  if (body && typeof body === 'object' && 'message' in body) {
    const message = (body as { message?: unknown }).message
    if (typeof message === 'string' && message.trim()) return message
  }
  if (transportError.code === 'ECONNABORTED') return `${fallback}：请求等待超时`
  if (/network error/i.test(transportError.message ?? '')) return `${fallback}：无法连接服务`
  const status = transportError.response?.status
  if (status !== undefined) {
    if (status >= 500) return `${fallback}：服务端暂时异常（HTTP ${status}）`
    if (status === 404) return `${fallback}：请求的功能或数据不存在`
    if (status === 403) return `${fallback}：当前账号没有操作权限`
    if (status === 401) return `${fallback}：登录状态已失效`
    return `${fallback}（HTTP ${status}）`
  }
  if (error instanceof Error && error.message.trim()
    && !/^request failed with status code \d+$/i.test(error.message)) return error.message
  return fallback
}

export const http = axios.create({
  baseURL: '/api/v1',
  timeout: DEFAULT_REQUEST_TIMEOUT_MS,
})

http.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  const identityConfig = config as typeof config & IdentityBoundRequestConfig
  identityConfig.__authTokenAtDispatch = token ?? ''
  identityConfig.__authIdentityEpochAtDispatch = localStorage.getItem('auth_identity_epoch') ?? ''
  identityConfig.__authStorageGenerationAtDispatch = authStorageGeneration
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && !error.config?.url?.endsWith('/auth/login')) {
      const identityConfig = error.config as IdentityBoundRequestConfig | undefined
      const requestStillUsesCurrentIdentity = identityConfig?.__authTokenAtDispatch
        === (localStorage.getItem('access_token') ?? '')
        && identityConfig?.__authIdentityEpochAtDispatch
        === (localStorage.getItem('auth_identity_epoch') ?? '')
        && identityConfig?.__authStorageGenerationAtDispatch === authStorageGeneration
      if (requestStillUsesCurrentIdentity) {
        clearRejectedIdentity()
        if (window.location.pathname !== '/login') {
          window.location.assign('/login')
        }
      }
    }
    return Promise.reject(error)
  },
)
