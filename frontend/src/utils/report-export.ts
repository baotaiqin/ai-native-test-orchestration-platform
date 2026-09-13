import type { CurrentUser } from '@/types/auth'
import type { ReportExportFormat, ReportExportResponse } from '@/types/reports'

const ERROR_BODY_LIMIT_BYTES = 64 * 1024
const HEADER_LIMIT_CHARS = 4096
const MESSAGE_LIMIT_CHARS = 500

const formatMetadata: Record<ReportExportFormat, { extension: string; mime: string }> = {
  markdown: { extension: '.md', mime: 'text/markdown' },
  html: { extension: '.html', mime: 'text/html' },
}

interface TransportError {
  code?: unknown
  message?: unknown
  response?: {
    status?: number
    data?: unknown
  }
}

export interface PreparedReportExport {
  blob: Blob
  fileName: string
}

export interface ReportAuthIdentity {
  token: string
  user: CurrentUser
  fingerprint: string
}

class ReportExportValidationError extends Error {}

function isCurrentUser(value: unknown): value is CurrentUser {
  if (!value || typeof value !== 'object') return false
  const user = value as Partial<CurrentUser>
  return typeof user.id === 'string' && Boolean(user.id)
    && typeof user.username === 'string'
    && typeof user.display_name === 'string'
    && Array.isArray(user.roles)
    && user.roles.every((role) => typeof role === 'string')
}

export function readReportAuthIdentity(): ReportAuthIdentity | null {
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

function safeMessage(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const normalized = value.replace(/[\u0000-\u001f\u007f]+/g, ' ').trim()
  if (!normalized) return null
  return normalized.slice(0, MESSAGE_LIMIT_CHARS)
}

async function boundedApiMessage(data: unknown): Promise<string | null> {
  if (data instanceof Blob) {
    if (data.size === 0 || data.size > ERROR_BODY_LIMIT_BYTES) return null
    try {
      const parsed = JSON.parse(await data.text()) as { message?: unknown }
      return safeMessage(parsed?.message)
    } catch {
      return null
    }
  }
  if (data && typeof data === 'object') {
    return safeMessage((data as { message?: unknown }).message)
  }
  return null
}

function contentTypeMatches(contentType: string, format: ReportExportFormat): boolean {
  const mime = contentType.split(';', 1)[0]?.trim().toLowerCase()
  return mime === formatMetadata[format].mime
}

async function isApiJsonBody(blob: Blob): Promise<boolean> {
  if (blob.size === 0 || blob.size > ERROR_BODY_LIMIT_BYTES) return false
  try {
    const parsed = JSON.parse(await blob.text())
    return Boolean(parsed && typeof parsed === 'object')
  } catch {
    return false
  }
}

function decodedDispositionFileName(contentDisposition: string): string | null {
  const header = contentDisposition.slice(0, HEADER_LIMIT_CHARS)
  const encodedMatch = header.match(/(?:^|;)\s*filename\*\s*=\s*(?:UTF-8'')?([^;]+)/i)
  if (encodedMatch?.[1]) {
    const encoded = encodedMatch[1].trim().replace(/^"|"$/g, '')
    try {
      return decodeURIComponent(encoded)
    } catch {
      return null
    }
  }
  const quotedMatch = header.match(/(?:^|;)\s*filename\s*=\s*"([^"]*)"/i)
  if (quotedMatch?.[1]) return quotedMatch[1]
  const plainMatch = header.match(/(?:^|;)\s*filename\s*=\s*([^;]+)/i)
  return plainMatch?.[1]?.trim() ?? null
}

function isSafeFileName(value: string, extension: string): boolean {
  return value.length <= 180
    && /^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(value)
    && value.toLowerCase().endsWith(extension)
}

function fallbackFileName(runId: string, extension: string): string {
  const safeRunId = runId
    .replace(/[^A-Za-z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80) || 'run'
  return `ai-test-report-${safeRunId}${extension}`
}

export async function prepareReportExport(
  response: ReportExportResponse,
  format: ReportExportFormat,
  runId: string,
): Promise<PreparedReportExport> {
  if (!(response.blob instanceof Blob)
    || !contentTypeMatches(response.contentType, format)
    || await isApiJsonBody(response.blob)) {
    throw new ReportExportValidationError('服务器返回的报告格式与所选下载格式不匹配，未保存文件。')
  }

  const { extension } = formatMetadata[format]
  const suppliedName = decodedDispositionFileName(response.contentDisposition)
  return {
    blob: response.blob,
    fileName: suppliedName && isSafeFileName(suppliedName, extension)
      ? suppliedName
      : fallbackFileName(runId, extension),
  }
}

export function triggerReportDownload(exported: PreparedReportExport): void {
  const objectUrl = URL.createObjectURL(exported.blob)
  const anchor = document.createElement('a')
  try {
    anchor.href = objectUrl
    anchor.download = exported.fileName
    anchor.rel = 'noopener'
    document.body.appendChild(anchor)
    anchor.click()
  } finally {
    anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0)
  }
}

export async function reportExportErrorMessage(error: unknown): Promise<string | null> {
  if (error instanceof ReportExportValidationError) return error.message

  const transport = error as TransportError
  const status = transport.response?.status
  if (status === 401) return null
  const backendMessage = await boundedApiMessage(transport.response?.data)
  if (backendMessage) return backendMessage
  if (status === 403 || status === 404) return '报告不存在，或当前用户无权导出。'
  if (status === 409) return '报告快照发生变化或包含无法编码的内容，请刷新后重试。'
  if (status === 413) return '完整报告超过同步导出上限，请使用分页页面查看。'
  if (status === 422) return '报告导出格式无效，请重新选择。'
  if (status) return '报告导出失败，请稍后重试。'
  if (transport.code === 'ECONNABORTED'
    || (typeof transport.message === 'string' && /timeout/i.test(transport.message))) {
    return '报告导出请求超时，请稍后重试。'
  }
  return '网络连接失败，报告未下载。'
}
