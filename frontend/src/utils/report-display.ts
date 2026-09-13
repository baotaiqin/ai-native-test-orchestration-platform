import type { RunNodeStatus, RunStatus, RunType } from '@/types/run'
import type { RecordingAvailability, ReportDataField, ReportDuration } from '@/types/reports'
import { parseApiDateTime } from '@/utils/datetime'

export function formatReportDate(value: string | null | undefined): string {
  if (!value) return '未记录'
  const date = parseApiDateTime(value)
  return Number.isNaN(date.getTime())
    ? '未记录'
    : date.toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' })
}

export function formatReportBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) return '未记录'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

export function formatReportMilliseconds(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '未记录'
  if (value < 1000) return `${value} ms`
  return `${(value / 1000).toFixed(value < 10_000 ? 2 : 1)} 秒`
}

export function formatReportDuration(duration: ReportDuration): string {
  if (duration.kind === 'UNAVAILABLE' || duration.milliseconds === null) return '未记录'
  const suffix = duration.kind === 'OBSERVED' ? '（当前观察值）' : ''
  return `${formatReportMilliseconds(duration.milliseconds)}${suffix}`
}

export function runTypeLabel(value: RunType): string {
  return value === 'API_CASE' ? 'API 用例' : value === 'SCENARIO' ? '编排场景' : 'Web 用例'
}

export function runStatusLabel(value: RunStatus | RunNodeStatus): string {
  const labels: Record<RunStatus | RunNodeStatus, string> = {
    CREATED: '已创建',
    QUEUED: '排队中',
    ASSIGNED: '已分配',
    RUNNING: '运行中',
    CANCELLING: '取消中',
    SUCCESS: '成功',
    FAILED: '失败',
    REVIEW: '待复核',
    CANCELLED: '已取消',
    TIMEOUT: '超时',
    SKIPPED: '已跳过',
  }
  return labels[value] ?? value
}

export function runStatusTagType(
  value: RunStatus | RunNodeStatus,
): 'success' | 'warning' | 'danger' | 'info' {
  if (value === 'SUCCESS') return 'success'
  if (value === 'FAILED' || value === 'TIMEOUT') return 'danger'
  if (value === 'REVIEW' || value === 'CANCELLING') return 'warning'
  return 'info'
}

export function availabilityLabel(value: RecordingAvailability): string {
  return value === 'RECORDED' ? '已记录' : value === 'NOT_APPLICABLE' ? '不适用' : '未记录'
}

export function displayReportValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '未记录'
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return '未记录'
  }
}

export function displayDataField(field: ReportDataField): string {
  if (field.availability !== 'RECORDED') return availabilityLabel(field.availability)
  return displayReportValue(field.value)
}

export function safeDownloadName(value: string): string {
  const normalized = value.replace(/[^A-Za-z0-9._-]+/g, '_').slice(0, 180)
  return normalized || 'evidence.bin'
}
