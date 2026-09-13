import { http, MODEL_CONNECTION_REQUEST_TIMEOUT_MS } from '@/api/http'
import type {
  AiTaskType,
  ModelConfiguration,
  ModelConfigurationCreatePayload,
  ModelConfigurationUpdatePayload,
  ModelConnectionVerification,
  ModelCatalogImportPayload,
  ModelCatalogQuery,
  ModelCatalogResponse,
  ModelProviderConnection,
  ModelProviderConnectionCreatePayload,
  ModelProviderConnectionUpdatePayload,
  ProjectModelBinding,
  ProjectModelBindingBulkApplyResult,
} from '@/types/model-center'

export async function getConnections(includeDisabled = false): Promise<ModelProviderConnection[]> {
  const response = await http.get<{ items: ModelProviderConnection[] }>(
    '/model-center/connections', { params: { include_disabled: includeDisabled } },
  )
  return response.data.items
}

export async function createConnection(
  payload: ModelProviderConnectionCreatePayload,
): Promise<ModelProviderConnection> {
  const response = await http.post<ModelProviderConnection>('/model-center/connections', payload)
  return response.data
}

export async function updateConnection(
  id: number, payload: ModelProviderConnectionUpdatePayload,
): Promise<ModelProviderConnection> {
  const response = await http.patch<ModelProviderConnection>(
    `/model-center/connections/${id}`, payload,
  )
  return response.data
}

export async function getModels(includeDisabled = false): Promise<ModelConfiguration[]> {
  const response = await http.get<{ items: ModelConfiguration[] }>('/model-center', {
    params: { include_disabled: includeDisabled },
  })
  return response.data.items
}

export async function createModel(
  payload: ModelConfigurationCreatePayload,
): Promise<ModelConfiguration> {
  const response = await http.post<ModelConfiguration>('/model-center', payload)
  return response.data
}

export async function updateModel(
  id: number, payload: ModelConfigurationUpdatePayload,
): Promise<ModelConfiguration> {
  const response = await http.patch<ModelConfiguration>(`/model-center/${id}`, payload)
  return response.data
}

export async function deleteModel(id: number): Promise<void> {
  await http.delete(`/model-center/${id}`)
}

export async function verifyModelConnection(id: number): Promise<ModelConnectionVerification> {
  const response = await http.post<ModelConnectionVerification>(
    `/model-center/${id}/verify-connection`,
    undefined,
    { timeout: MODEL_CONNECTION_REQUEST_TIMEOUT_MS },
  )
  return response.data
}

export async function getCatalogModels(
  connectionId: number, params: ModelCatalogQuery,
): Promise<ModelCatalogResponse> {
  const searchParams = new URLSearchParams()
  if (params.q) searchParams.set('q', params.q)
  for (const capability of params.capabilities ?? []) searchParams.append('capabilities', capability)
  if (params.tool_call !== undefined) searchParams.set('tool_call', String(params.tool_call))
  if (params.structured_output !== undefined) searchParams.set('structured_output', String(params.structured_output))
  if (params.page !== undefined) searchParams.set('page', String(params.page))
  if (params.page_size !== undefined) searchParams.set('page_size', String(params.page_size))
  const response = await http.get<ModelCatalogResponse>(
    `/model-center/connections/${connectionId}/catalog/models`, { params: searchParams },
  )
  return response.data
}

export async function importCatalogModel(
  connectionId: number, payload: ModelCatalogImportPayload,
): Promise<ModelConfiguration> {
  const response = await http.post<ModelConfiguration>(
    `/model-center/connections/${connectionId}/catalog/models/import`, payload,
  )
  return response.data
}

export async function getModelBindings(projectId: number): Promise<ProjectModelBinding[]> {
  const response = await http.get<{ items: ProjectModelBinding[] }>(
    '/model-center/bindings/project', { params: { project_id: projectId } },
  )
  return response.data.items
}

export async function saveModelBinding(payload: {
  project_id: number
  task_type: AiTaskType
  primary_model_id: number
  fallback_model_id: number | null
  max_fallback: number
}): Promise<ProjectModelBinding> {
  const response = await http.put<ProjectModelBinding>(
    '/model-center/bindings/project', payload,
  )
  return response.data
}

export async function bulkApplyModelBinding(payload: {
  project_id: number
  model_id: number
  role: 'PRIMARY' | 'FALLBACK'
}): Promise<ProjectModelBindingBulkApplyResult> {
  const response = await http.put<ProjectModelBindingBulkApplyResult>(
    '/model-center/bindings/project/bulk-apply', payload,
  )
  return response.data
}
