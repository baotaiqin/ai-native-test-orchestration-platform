import type { RunEventResponse } from '@/types/run-event'

const RUN_EVENT_STREAM_PATH = '/api/v1/runs'

export interface ParsedSseFrame {
  id: string | null
  event: string | null
  data: string
}

export interface RunEventStreamOptions {
  runId: string
  afterId?: string | null
  signal: AbortSignal
  onOpen?: () => void
  onRunStatus: (event: RunEventResponse, frameId: string | null) => void
}

export class RunEventStreamHttpError extends Error {
  readonly status: number

  constructor(status: number) {
    super(`Run event stream request failed with status ${status}`)
    this.name = 'RunEventStreamHttpError'
    this.status = status
  }
}

export class RunEventStreamProtocolError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'RunEventStreamProtocolError'
  }
}

const RUN_STATUSES = new Set([
  'CREATED', 'QUEUED', 'ASSIGNED', 'RUNNING', 'CANCELLING',
  'SUCCESS', 'FAILED', 'CANCELLED', 'TIMEOUT',
])

function isRunStatus(value: unknown): boolean {
  return typeof value === 'string' && RUN_STATUSES.has(value)
}

function isRunEventResponse(value: unknown): value is RunEventResponse {
  if (!value || typeof value !== 'object') return false
  const event = value as Record<string, unknown>
  return event.schema_version === 1
    && typeof event.id === 'string'
    && typeof event.event_type === 'string'
    && typeof event.project_id === 'number'
    && typeof event.run_id === 'string'
    && isRunStatus(event.to_status)
    && (event.from_status === null || isRunStatus(event.from_status))
    && typeof event.occurred_at === 'string'
}

function parseSseFrame(frameText: string): ParsedSseFrame | null {
  let id: string | null = null
  let event: string | null = null
  const dataLines: string[] = []

  for (const line of frameText.split(/\r\n|\r|\n/)) {
    if (!line || line.startsWith(':')) continue
    const separatorIndex = line.indexOf(':')
    const field = separatorIndex === -1 ? line : line.slice(0, separatorIndex)
    let value = separatorIndex === -1 ? '' : line.slice(separatorIndex + 1)
    if (value.startsWith(' ')) value = value.slice(1)

    if (field === 'id') id = value
    else if (field === 'event') event = value
    else if (field === 'data') dataLines.push(value)
  }

  if (id === null && event === null && dataLines.length === 0) return null
  return { id, event, data: dataLines.join('\n') }
}

export class SseFrameParser {
  private buffer = ''

  push(chunk: string): ParsedSseFrame[] {
    this.buffer += chunk
    const frames: ParsedSseFrame[] = []

    while (true) {
      const boundary = /\r\n\r\n|\n\n|\r\r/.exec(this.buffer)
      if (!boundary || boundary.index === undefined) break
      const frameText = this.buffer.slice(0, boundary.index)
      this.buffer = this.buffer.slice(boundary.index + boundary[0].length)
      const frame = parseSseFrame(frameText)
      if (frame) frames.push(frame)
    }

    return frames
  }
}

function eventStreamUrl(runId: string, afterId: string | null | undefined): string {
  const suffix = afterId ? `?after_id=${encodeURIComponent(afterId)}` : ''
  return `${RUN_EVENT_STREAM_PATH}/${encodeURIComponent(runId)}/events/stream${suffix}`
}

export async function consumeRunEventStream(options: RunEventStreamOptions): Promise<void> {
  const token = localStorage.getItem('access_token')
  const headers = new Headers({
    Accept: 'text/event-stream',
    'Cache-Control': 'no-cache',
  })
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(eventStreamUrl(options.runId, options.afterId), {
    method: 'GET',
    headers,
    credentials: 'same-origin',
    cache: 'no-store',
    signal: options.signal,
  })
  if (!response.ok) throw new RunEventStreamHttpError(response.status)
  if (!response.body) throw new RunEventStreamProtocolError('实时事件流不可用')

  options.onOpen?.()
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const parser = new SseFrameParser()

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      const chunk = decoder.decode(value, { stream: true })
      for (const frame of parser.push(chunk)) {
        if (frame.event === 'stream_error') {
          throw new RunEventStreamProtocolError('实时事件流暂时不可用')
        }
        if (frame.event !== 'run_status' || !frame.data) continue
        let parsed: unknown
        try {
          parsed = JSON.parse(frame.data)
        } catch {
          throw new RunEventStreamProtocolError('实时事件格式无效')
        }
        if (!isRunEventResponse(parsed)) throw new RunEventStreamProtocolError('实时事件格式无效')
        options.onRunStatus(parsed, frame.id)
      }
    }
    const trailingChunk = decoder.decode()
    for (const frame of parser.push(trailingChunk)) {
      if (frame.event === 'stream_error') {
        throw new RunEventStreamProtocolError('实时事件流暂时不可用')
      }
      if (frame.event !== 'run_status' || !frame.data) continue
      let parsed: unknown
      try {
        parsed = JSON.parse(frame.data)
      } catch {
        throw new RunEventStreamProtocolError('实时事件格式无效')
      }
      if (!isRunEventResponse(parsed)) throw new RunEventStreamProtocolError('实时事件格式无效')
      options.onRunStatus(parsed, frame.id)
    }
  } finally {
    reader.releaseLock()
  }
}
