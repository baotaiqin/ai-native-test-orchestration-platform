import { getApiErrorMessage, type ApiErrorBody } from '@/api/http'
import { ElMessage } from 'element-plus'

interface AiApiError {
  code?: string
  message?: string
  response?: {
    status?: number
    data?: ApiErrorBody
  }
}

export function getAiGenerationErrorMessage(error: unknown, fallback = 'AI 生成失败'): string {
  const apiError = error as AiApiError
  const response = apiError.response
  const message = getApiErrorMessage(error, fallback)
  const details = response?.data?.details
  const errorType = details && typeof details === 'object' && 'error_type' in details
    ? String((details as { error_type?: unknown }).error_type ?? '')
    : ''

  if (apiError.code === 'ECONNABORTED' || /timeout(?: of)? \d+ms exceeded/i.test(apiError.message ?? '')) {
    return 'AI 请求等待超过 180 秒。模型任务可能仍在后端执行，请先关闭弹窗并刷新生成记录；确认没有新记录后再重试，避免重复消耗模型额度。'
  }

  if (errorType === 'PROVIDER_REQUEST_ERROR') {
    return `${message}。模型连接正常，但服务商拒绝了业务请求；平台已自动尝试兼容模式，请检查模型名和服务商限制。`
  }
  if (errorType === 'TIMEOUT') return `${message}。请适当增加模型超时时间后重试。`
  if (errorType === 'NETWORK_ERROR') return `${message}。请检查后端到模型服务的网络或代理。`
  if (errorType === 'SECRET_UNAVAILABLE') return `${message}。请在模型中心重新保存 API Key。`
  if (response?.status === 422 && response.data?.code === 'AI_STRUCTURED_OUTPUT_INVALID') {
    return `${message}。可在“AI 输出与审计”查看具体 Schema 校验错误。`
  }
  return message
}

export function showAiGenerationError(error: unknown, fallback = 'AI 生成失败'): string {
  const message = getAiGenerationErrorMessage(error, fallback)
  ElMessage({
    type: 'error',
    message,
    duration: 8_000,
    showClose: true,
  })
  return message
}
