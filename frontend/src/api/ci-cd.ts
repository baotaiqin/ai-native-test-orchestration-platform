import { http } from './http'
import type {
  CiTokenCreate,
  CiTokenCreated,
  CiTokenMetadata,
  WebhookDelivery,
  WebhookEndpoint,
  WebhookEndpointCreate,
} from '@/types/ci-cd'

export async function getCiTokens(projectId: number): Promise<CiTokenMetadata[]> {
  const response = await http.get<{ items: CiTokenMetadata[] }>('/ci-cd/tokens', {
    params: { project_id: projectId },
  })
  return response.data.items
}

export async function createCiToken(payload: CiTokenCreate): Promise<CiTokenCreated> {
  const response = await http.post<CiTokenCreated>('/ci-cd/tokens', payload)
  return response.data
}

export async function revokeCiToken(id: number): Promise<CiTokenMetadata> {
  const response = await http.post<CiTokenMetadata>(`/ci-cd/tokens/${id}/revoke`)
  return response.data
}

export async function getWebhookEndpoints(projectId: number): Promise<WebhookEndpoint[]> {
  const response = await http.get<{ items: WebhookEndpoint[] }>('/ci-cd/webhooks', {
    params: { project_id: projectId },
  })
  return response.data.items
}

export async function createWebhookEndpoint(
  payload: WebhookEndpointCreate,
): Promise<WebhookEndpoint> {
  const response = await http.post<WebhookEndpoint>('/ci-cd/webhooks', payload)
  return response.data
}

export async function setWebhookArchived(id: number, archived: boolean): Promise<WebhookEndpoint> {
  const action = archived ? 'archive' : 'restore'
  const response = await http.post<WebhookEndpoint>(`/ci-cd/webhooks/${id}/${action}`)
  return response.data
}

export async function getWebhookDeliveries(projectId: number): Promise<WebhookDelivery[]> {
  const response = await http.get<{ items: WebhookDelivery[] }>('/ci-cd/webhook-deliveries', {
    params: { project_id: projectId },
  })
  return response.data.items
}

export async function retryWebhookDelivery(id: string): Promise<WebhookDelivery> {
  const response = await http.post<WebhookDelivery>(
    `/ci-cd/webhook-deliveries/${encodeURIComponent(id)}/retry`,
  )
  return response.data
}
