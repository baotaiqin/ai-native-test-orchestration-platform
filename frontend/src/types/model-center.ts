export type ModelType = 'TEXT' | 'VISION' | 'EMBEDDING'
export type ModelCategory =
  | 'LLM' | 'VISION' | 'OMNI' | 'AUDIO' | 'EMBEDDING'
  | 'IMAGE_GENERATION' | 'VIDEO_GENERATION' | 'THREE_D' | 'OTHER'
export type ModelConnectionVerificationStatus = 'SUCCESS' | 'FAILED'
export type ModelProviderConnectionAccessType = 'DIRECT' | 'AGGREGATOR' | 'SELF_HOSTED'
export type ModelProviderConnectionProtocolType = 'OPENAI_COMPATIBLE'

export type AiTaskType =
  | 'REQUIREMENT_REVIEW' | 'API_DOC_REVIEW' | 'API_TEST_DESIGN' | 'API_CASE_GENERATE'
  | 'API_SCENARIO_GENERATE'
  | 'API_SCENARIO_PLAN'
  | 'WEB_CASE_GENERATE' | 'WEB_TEST_PLAN' | 'WEB_EXPLORATION_DECISION'
  | 'WEB_PLAN_RECONCILE' | 'AI_ASSERTION' | 'LOCATOR_HEALING'
  | 'WEB_FAILURE_ANALYSIS' | 'PERFORMANCE_ANALYSIS' | 'DEFECT_DRAFT'
  | 'IMPACT_ANALYSIS'

export interface ModelProviderConnection {
  id: number
  name: string
  access_type: ModelProviderConnectionAccessType
  provider: string
  protocol_type: ModelProviderConnectionProtocolType
  base_url: string
  has_api_key: boolean
  api_key_masked: string | null
  api_key_rotated_at: string | null
  enabled: boolean
  created_by: string
  created_at: string
  updated_at: string
}

export interface ModelConfiguration {
  id: number
  name: string
  connection_id: number
  model_vendor: string
  model_name: string
  model_category: ModelCategory | null
  model_type: ModelType
  supports_tool_call: boolean
  supports_structured_output: boolean
  max_context: number | null
  timeout_seconds: number
  input_price: string
  output_price: string
  enabled: boolean
  metadata_source: string | null
  metadata_synced_at: string | null
  description: string | null
  supports_reasoning: boolean
  max_input_tokens: number | null
  max_output_tokens: number | null
  max_reasoning_tokens: number | null
  reasoning_max_input_tokens: number | null
  reasoning_max_output_tokens: number | null
  input_modalities: string[]
  output_modalities: string[]
  capabilities: string[]
  features: string[]
  pricing_tiers: ModelCatalogPricingTier[]
  published_at: string | null
  created_by: string
  created_at: string
  updated_at: string
}

export interface ModelCatalogPricingItem {
  type: string
  price: string | null
  price_unit: string | null
  price_name: string | null
}

export interface ModelCatalogPricingTier {
  range_name: string
  items: ModelCatalogPricingItem[]
}

export interface ModelCatalogModel {
  model_id: string
  name: string
  description: string | null
  provider: string
  inference_provider: string | null
  model_category: ModelCategory
  model_type: ModelType
  capabilities: string[]
  features: string[]
  input_modalities: string[]
  output_modalities: string[]
  supports_reasoning: boolean
  supports_tool_call: boolean
  supports_structured_output: boolean
  context_window: number | null
  max_input_tokens: number | null
  max_output_tokens: number | null
  max_reasoning_tokens: number | null
  reasoning_max_input_tokens: number | null
  reasoning_max_output_tokens: number | null
  pricing_tiers: ModelCatalogPricingTier[]
  published_time: string | null
  supported_by_platform: boolean
  unsupported_reason: string | null
}

export interface ModelCatalogResponse {
  connection_id: number
  source: string
  fetched_at: string
  total: number
  page: number
  page_size: number
  items: ModelCatalogModel[]
}

export interface ModelCatalogQuery {
  q?: string
  capabilities?: string[]
  tool_call?: boolean
  structured_output?: boolean
  page?: number
  page_size?: number
}

export interface ModelCatalogImportPayload {
  model_id: string
  configuration_name?: string
  timeout_seconds?: number
  enabled?: boolean
}

export interface ModelConnectionVerification {
  model_id: number
  success: boolean
  status: ModelConnectionVerificationStatus
  duration_ms: number
  summary: string
  error_type: string | null
}

type ModelProviderConnectionEditableFields = Pick<
  ModelProviderConnection,
  'name' | 'access_type' | 'provider' | 'protocol_type' | 'base_url' | 'enabled'
>

export type ModelProviderConnectionCreatePayload = ModelProviderConnectionEditableFields & {
  api_key?: string | null
}

export type ModelProviderConnectionUpdatePayload =
  Partial<ModelProviderConnectionEditableFields> & { api_key?: string | null }

type ModelConfigurationEditableFields = Omit<
  ModelConfiguration,
  | 'id'
  | 'model_category'
  | 'metadata_source'
  | 'metadata_synced_at'
  | 'description'
  | 'supports_reasoning'
  | 'max_input_tokens'
  | 'max_output_tokens'
  | 'max_reasoning_tokens'
  | 'reasoning_max_input_tokens'
  | 'reasoning_max_output_tokens'
  | 'input_modalities'
  | 'output_modalities'
  | 'capabilities'
  | 'features'
  | 'pricing_tiers'
  | 'published_at'
  | 'created_by'
  | 'created_at'
  | 'updated_at'
>

export type ModelConfigurationCreatePayload = ModelConfigurationEditableFields

export type ModelConfigurationUpdatePayload = Partial<ModelConfigurationEditableFields>

export type ModelConfigurationPayload = ModelConfigurationCreatePayload

export interface ProjectModelBinding {
  id: number
  project_id: number
  task_type: AiTaskType
  primary_model_id: number
  fallback_model_id: number | null
  max_fallback: number
  updated_by: string
  created_at: string
  updated_at: string
}

export interface ProjectModelBindingBulkApplyResult {
  project_id: number
  model_id: number
  role: 'PRIMARY' | 'FALLBACK'
  applied_count: number
  unchanged_count: number
  skipped_count: number
  items: Array<{
    task_type: AiTaskType
    status: 'APPLIED' | 'UNCHANGED' | 'SKIPPED'
    message: string
  }>
}
