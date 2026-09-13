const EXPLICIT_TIME_ZONE = /(Z|[+-]\d{2}:?\d{2})$/i
const ISO_DATE_TIME_WITHOUT_ZONE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/

/**
 * Backend DATETIME values are stored in UTC but legacy responses omit the zone.
 * Treat only zone-less ISO date-times as UTC; explicit offsets and Date objects
 * keep their normal JavaScript semantics.
 */
export function parseApiDateTime(value: string | Date): Date {
  if (value instanceof Date) return value
  const normalized = ISO_DATE_TIME_WITHOUT_ZONE.test(value) && !EXPLICIT_TIME_ZONE.test(value)
    ? `${value}Z`
    : value
  return new Date(normalized)
}

export function formatApiDateTime(
  value: string | Date | null | undefined,
  fallback = '—',
): string {
  if (!value) return fallback
  const parsed = parseApiDateTime(value)
  if (Number.isNaN(parsed.getTime())) return fallback
  return parsed.toLocaleString('zh-CN', { hour12: false })
}

export function apiDateTimeMs(value: string | Date | null | undefined): number {
  if (!value) return Number.NaN
  return parseApiDateTime(value).getTime()
}
