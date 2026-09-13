import { http } from './http'
import type {
  SchedulePreviewPayload,
  ScheduleTrigger,
  ScheduleWrite,
  TestSchedule,
} from '@/types/schedule'

export async function getSchedules(projectId: number): Promise<TestSchedule[]> {
  const response = await http.get<{ items: TestSchedule[] }>('/schedules', {
    params: { project_id: projectId },
  })
  return response.data.items
}

export async function createSchedule(payload: ScheduleWrite): Promise<TestSchedule> {
  const response = await http.post<TestSchedule>('/schedules', payload)
  return response.data
}

export async function updateSchedule(id: number, payload: ScheduleWrite): Promise<TestSchedule> {
  const response = await http.put<TestSchedule>(`/schedules/${id}`, payload)
  return response.data
}

export async function setScheduleArchived(id: number, archived: boolean): Promise<TestSchedule> {
  const action = archived ? 'archive' : 'restore'
  const response = await http.post<TestSchedule>(`/schedules/${id}/${action}`)
  return response.data
}

export async function previewSchedule(payload: SchedulePreviewPayload): Promise<string[]> {
  const response = await http.post<{ occurrences: string[] }>('/schedules/preview', payload)
  return response.data.occurrences
}

export async function getScheduleTriggers(projectId: number): Promise<ScheduleTrigger[]> {
  const response = await http.get<{ items: ScheduleTrigger[] }>('/schedules/triggers', {
    params: { project_id: projectId },
  })
  return response.data.items
}

export async function retryScheduleTrigger(id: string): Promise<ScheduleTrigger> {
  const response = await http.post<ScheduleTrigger>(
    `/schedules/triggers/${encodeURIComponent(id)}/retry`,
  )
  return response.data
}
