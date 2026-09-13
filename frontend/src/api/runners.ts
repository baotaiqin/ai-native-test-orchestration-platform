import { http } from '@/api/http'
import type {
  RegistrationTokenCreateRequest,
  RegistrationTokenCreateResponse,
  RegistrationTokenListResponse,
  RegistrationTokenMetadata,
  Runner,
  RunnerListResponse,
} from '@/types/runner'

export async function getRunners(): Promise<RunnerListResponse> {
  const response = await http.get<RunnerListResponse>('/runners')
  return response.data
}

export async function getRunner(runnerId: string): Promise<Runner> {
  const response = await http.get<Runner>(`/runners/${encodeURIComponent(runnerId)}`)
  return response.data
}

export async function revokeRunner(runnerId: string): Promise<Runner> {
  const response = await http.post<Runner>(`/runners/${encodeURIComponent(runnerId)}/revoke`)
  return response.data
}

export async function getRegistrationTokens(): Promise<RegistrationTokenListResponse> {
  const response = await http.get<RegistrationTokenListResponse>('/runners/registration-tokens')
  return response.data
}

export async function createRegistrationToken(
  payload: RegistrationTokenCreateRequest = {},
): Promise<RegistrationTokenCreateResponse> {
  const response = await http.post<RegistrationTokenCreateResponse>(
    '/runners/registration-tokens',
    payload,
  )
  return response.data
}

export async function revokeRegistrationToken(tokenId: number): Promise<RegistrationTokenMetadata> {
  const response = await http.post<RegistrationTokenMetadata>(
    `/runners/registration-tokens/${tokenId}/revoke`,
  )
  return response.data
}

