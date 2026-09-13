import { getApiErrorMessage } from '@/api/http'
import type { SuggestedCase } from '@/types/test-case'

export const API_STEP_RETRY_LIMIT_ERROR_CODE = 'API_STEP_RETRY_LIMIT_EXCEEDED'
export const API_STEP_RETRY_LIMIT_ERROR_MESSAGE = 'V1 API Step 自动重试最多一次；请将 max_retries 设为 0 或 1，并创建符合 V1 的新版本'

export function isV1ApiStepMaxRetries(value: unknown): value is 0 | 1 {
  return typeof value === 'number'
    && Number.isInteger(value)
    && (value === 0 || value === 1)
}

export function apiStepMaxRetries(content: SuggestedCase): unknown {
  if (content.case_type !== 'API' || !content.request) return 0
  const policy = content.request.retry_policy as unknown
  if (!policy || typeof policy !== 'object') return 0
  if (!Object.prototype.hasOwnProperty.call(policy, 'max_retries')) return 0
  return (policy as { max_retries?: unknown }).max_retries
}

export function v1RetryPolicyError(content: SuggestedCase): string | null {
  if (content.case_type !== 'API') return null
  return isV1ApiStepMaxRetries(apiStepMaxRetries(content))
    ? null
    : API_STEP_RETRY_LIMIT_ERROR_MESSAGE
}

function nestedRetryLimitMessage(value: unknown, depth = 0, seen = new WeakSet<object>()): string | null {
  if (depth > 8 || !value || typeof value !== 'object') return null
  if (seen.has(value)) return null
  seen.add(value)

  const record = value as Record<string, unknown>
  if (record.code === API_STEP_RETRY_LIMIT_ERROR_CODE) {
    return typeof record.message === 'string' && record.message.trim()
      ? record.message
      : API_STEP_RETRY_LIMIT_ERROR_MESSAGE
  }
  for (const child of Object.values(record)) {
    if (Array.isArray(child)) {
      for (const item of child) {
        const message = nestedRetryLimitMessage(item, depth + 1, seen)
        if (message) return message
      }
    } else {
      const message = nestedRetryLimitMessage(child, depth + 1, seen)
      if (message) return message
    }
  }
  return null
}

export function retryAwareApiErrorMessage(error: unknown, fallback: string): string {
  const responseData = (error as { response?: { data?: unknown } }).response?.data
  return nestedRetryLimitMessage(responseData)
    ?? nestedRetryLimitMessage(error)
    ?? getApiErrorMessage(error, fallback)
}
