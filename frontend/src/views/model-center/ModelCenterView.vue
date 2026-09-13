<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  createConnection,
  createModel,
  deleteModel,
  getCatalogModels,
  getConnections,
  getModels,
  importCatalogModel,
  updateConnection,
  updateModel,
  verifyModelConnection,
} from '@/api/model-center'
import { getApiErrorMessage } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import { formatApiDateTime } from '@/utils/datetime'
import type {
  ModelCatalogImportPayload,
  ModelCatalogModel,
  ModelCatalogQuery,
  ModelCatalogResponse,
  ModelCategory,
  ModelConfiguration,
  ModelConfigurationCreatePayload,
  ModelConfigurationUpdatePayload,
  ModelConnectionVerification,
  ModelProviderConnection,
  ModelProviderConnectionAccessType,
  ModelProviderConnectionCreatePayload,
  ModelProviderConnectionUpdatePayload,
  ModelType,
} from '@/types/model-center'
import { RECORD_PAGE_SIZES, useClientPagination } from '@/composables/useClientPagination'

type ConnectionForm = Omit<ModelProviderConnectionCreatePayload, 'api_key'>
type ModelConfigurationForm = ModelConfigurationCreatePayload
type ProviderPreset = {
  accessType: ModelProviderConnectionAccessType
  value: string
  label: string
  suggestedName: string
  baseUrls: { label: string; value: string }[]
}

const accessTypeLabels: Record<ModelProviderConnectionAccessType, string> = {
  DIRECT: '官方直连',
  AGGREGATOR: '聚合平台',
  SELF_HOSTED: '本地/自托管',
}

const providerPresets: ProviderPreset[] = [
  { accessType: 'DIRECT', value: 'OPENAI', label: 'OpenAI', suggestedName: 'OpenAI 官方直连', baseUrls: [{ label: 'OpenAI 默认地址', value: 'https://api.openai.com/v1' }] },
  { accessType: 'DIRECT', value: 'DEEPSEEK', label: 'DeepSeek', suggestedName: 'DeepSeek 官方直连', baseUrls: [{ label: 'DeepSeek 默认地址', value: 'https://api.deepseek.com' }] },
  { accessType: 'DIRECT', value: 'ZHIPU', label: '智谱 AI', suggestedName: '智谱 AI 官方直连', baseUrls: [{ label: '智谱 AI 默认地址', value: 'https://open.bigmodel.cn/api/paas/v4' }] },
  { accessType: 'AGGREGATOR', value: 'ALIYUN_BAILIAN', label: '阿里云百炼', suggestedName: '阿里云百炼', baseUrls: [{ label: '中国大陆', value: 'https://dashscope.aliyuncs.com/compatible-mode/v1' }, { label: '国际', value: 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1' }] },
  { accessType: 'AGGREGATOR', value: 'OPENROUTER', label: 'OpenRouter', suggestedName: 'OpenRouter 聚合', baseUrls: [{ label: 'OpenRouter 默认地址', value: 'https://openrouter.ai/api/v1' }] },
  { accessType: 'AGGREGATOR', value: 'SILICONFLOW', label: '硅基流动', suggestedName: '硅基流动聚合', baseUrls: [{ label: '硅基流动默认地址', value: 'https://api.siliconflow.cn/v1' }] },
  { accessType: 'AGGREGATOR', value: 'VOLCENGINE_ARK', label: '火山方舟', suggestedName: '火山方舟聚合', baseUrls: [{ label: '北京默认地址', value: 'https://ark.cn-beijing.volces.com/api/v3' }] },
  { accessType: 'SELF_HOSTED', value: 'OLLAMA', label: 'Ollama', suggestedName: 'Ollama 本地服务', baseUrls: [{ label: '本机默认地址', value: 'http://127.0.0.1:11434/v1' }] },
  { accessType: 'SELF_HOSTED', value: 'VLLM', label: 'vLLM', suggestedName: 'vLLM 自托管服务', baseUrls: [{ label: '本机默认地址', value: 'http://127.0.0.1:8000/v1' }] },
]

const modelCategoryLabels: Record<ModelCategory, string> = {
  LLM: '大语言模型',
  VISION: '视觉模型',
  OMNI: '全模态模型',
  AUDIO: '语音模型',
  EMBEDDING: '向量模型',
  IMAGE_GENERATION: '图片生成',
  VIDEO_GENERATION: '视频生成',
  THREE_D: '3D 模型',
  OTHER: '其他',
}

const modelTypeLabels: Record<ModelType, string> = {
  TEXT: '文本', VISION: '视觉', EMBEDDING: '向量',
}

const capabilityOptions: { value: string; label: string }[] = [
  { value: 'TG', label: '文本生成' },
  { value: 'Reasoning', label: '深度推理' },
  { value: 'VU', label: '视觉理解' },
  { value: 'IG', label: '图片生成' },
  { value: 'VG', label: '视频生成' },
  { value: 'ASR', label: '语音识别' },
  { value: 'TTS', label: '文本转语音' },
  { value: 'TR', label: '文本向量' },
  { value: 'ME', label: '多模态向量' },
  { value: 'Realtime-Omni', label: '实时全模态' },
  { value: 'Multimodal-Omni', label: '多模态全模态' },
  { value: 'Realtime-Text-to-Speech', label: '实时文本转语音' },
  { value: 'Realtime-ASR', label: '实时语音识别' },
  { value: 'Realtime-Audio-Translate', label: '实时音频翻译' },
  { value: '3D-generation', label: '3D 生成' },
  { value: 'Realtime-Chatting', label: '实时对话' },
]

const authStore = useAuthStore()
const isAdmin = computed(() => authStore.user?.roles.includes('ADMIN') ?? false)
const activeTab = ref<'connections' | 'models'>('connections')

const connections = ref<ModelProviderConnection[]>([])
const models = ref<ModelConfiguration[]>([])
const {
  items: pagedConnections, total: connectionTotal, page: connectionPage,
  pageSize: connectionPageSize, changePage: changeConnectionPage,
  changePageSize: changeConnectionPageSize,
} = useClientPagination(connections)
const {
  items: pagedModels, total: modelTotal, page: modelPage,
  pageSize: modelPageSize, changePage: changeModelPage,
  changePageSize: changeModelPageSize,
} = useClientPagination(models)
const connectionsLoading = ref(false)
const modelsLoading = ref(false)
const pageRefreshing = computed(() => connectionsLoading.value || modelsLoading.value)

const connectionDialogVisible = ref(false)
const editingConnectionId = ref<number>()
const connectionForm = reactive<ConnectionForm>({
  name: '', access_type: 'DIRECT', provider: '', protocol_type: 'OPENAI_COMPATIBLE',
  base_url: '', enabled: true,
})
const connectionApiKeyInput = ref('')
const clearConnectionApiKeyRequested = ref(false)
const editingConnectionHasApiKey = ref(false)
const editingConnectionApiKeyMasked = ref<string | null>(null)

const modelDialogVisible = ref(false)
const editingModelId = ref<number>()
const modelForm = reactive<ModelConfigurationForm>({
  name: '', connection_id: 0, model_vendor: '', model_name: '', model_type: 'TEXT',
  supports_tool_call: false, supports_structured_output: true,
  max_context: 128000, timeout_seconds: 60, input_price: '0', output_price: '0',
  enabled: true,
})
const editingModelMetadata = ref<ModelConfiguration | null>(null)
const isCatalogModel = computed(() => {
  const source = editingModelMetadata.value?.metadata_source?.trim().toUpperCase()
  return Boolean(source && source !== 'MANUAL')
})
const manualMetadataSections = ref(['manual'])
const verificationResults = ref<Record<number, ModelConnectionVerification>>({})
const verifyingModelIds = ref<Set<number>>(new Set())
const deletingModelIds = ref<Set<number>>(new Set())

type CatalogFilters = {
  q: string
  capabilities: string[]
  tool_call: boolean
  structured_output: boolean
}

const catalogDialogVisible = ref(false)
const catalogConnectionId = ref<number>()
const catalogLoading = ref(false)
const catalogError = ref('')
const catalogResult = ref<ModelCatalogResponse>()
const catalogPage = ref(1)
const catalogPageSize = ref(10)
const catalogRequestSequence = ref(0)
const catalogFilters = reactive<CatalogFilters>({
  q: '', capabilities: [], tool_call: false, structured_output: false,
})
const catalogImportDialogVisible = ref(false)
const catalogImporting = ref(false)
const catalogImportModel = ref<ModelCatalogModel>()
const catalogImportForm = reactive({
  configuration_name: '', timeout_seconds: 60, enabled: true,
})

const enabledConnections = computed(() => connections.value.filter((connection) => connection.enabled))
const modelConnectionOptions = computed(() => {
  const current = connections.value.find((connection) => connection.id === modelForm.connection_id)
  if (current && !current.enabled) {
    return [current, ...enabledConnections.value.filter((connection) => connection.id !== current.id)]
  }
  return enabledConnections.value
})
const catalogConnection = computed(() => (
  connections.value.find((connection) => connection.id === catalogConnectionId.value)
))
const catalogSupported = computed(() => (
  catalogConnection.value?.provider.trim().toUpperCase() === 'ALIYUN_BAILIAN'
))
const catalogNeedsWorkspaceUrlWarning = computed(() => {
  if (!catalogSupported.value || !catalogConnection.value?.base_url) return false
  try {
    return new URL(catalogConnection.value.base_url).hostname.toLowerCase() === 'dashscope.aliyuncs.com'
  } catch {
    return false
  }
})

const providerPresetsForType = computed(() => (
  providerPresets.filter((preset) => preset.accessType === connectionForm.access_type)
))
const providerOptions = computed<ProviderPreset[]>(() => {
  if (!connectionForm.provider || providerPresetsForType.value.some((preset) => preset.value === connectionForm.provider)) {
    return providerPresetsForType.value
  }
  return [...providerPresetsForType.value, {
    accessType: connectionForm.access_type,
    value: connectionForm.provider,
    label: `${connectionForm.provider}（自定义）`,
    suggestedName: '',
    baseUrls: [],
  }]
})
const selectedProviderPreset = computed(() => (
  providerPresetsForType.value.find((preset) => preset.value === connectionForm.provider)
))
const baseUrlOptions = computed(() => {
  const options = (selectedProviderPreset.value?.baseUrls ?? []).map((option) => ({
    ...option,
    label: `${option.label} · ${option.value}`,
  }))
  if (connectionForm.base_url && !options.some((option) => option.value === connectionForm.base_url)) {
    return [...options, { label: `${connectionForm.base_url}（当前自定义）`, value: connectionForm.base_url }]
  }
  return options
})

function accessTypeLabel(type: ModelProviderConnectionAccessType): string {
  return accessTypeLabels[type] ?? type
}

function providerDisplayName(provider: string): string {
  return providerPresets.find((preset) => preset.value === provider)?.label ?? provider
}

function presetForProvider(provider: string): ProviderPreset | undefined {
  return providerPresetsForType.value.find((preset) => preset.value === provider)
}

function handleAccessTypeChange(): void {
  if (!presetForProvider(connectionForm.provider)) {
    connectionForm.provider = ''
    connectionForm.base_url = ''
  }
}

function handleProviderChange(value: string): void {
  const preset = presetForProvider(value)
  if (preset) {
    connectionForm.base_url = preset.baseUrls[0]?.value ?? ''
    if (!connectionForm.name.trim()) connectionForm.name = preset.suggestedName
  } else {
    connectionForm.base_url = ''
  }
}

function connectionLabel(connection: ModelProviderConnection): string {
  return `${connection.name} · ${providerDisplayName(connection.provider)} · ${accessTypeLabel(connection.access_type)}`
}

function connectionName(connectionId: number): string {
  const connection = connections.value.find((item) => item.id === connectionId)
  return connection ? connection.name : '渠道详情未加载'
}

function connectionType(connectionId: number): string {
  const connection = connections.value.find((item) => item.id === connectionId)
  return connection ? accessTypeLabel(connection.access_type) : '渠道不可用'
}

function formatDate(value: string | null): string {
  return formatApiDateTime(value, '未记录')
}

function officialText(value: string | null | undefined): string {
  return value?.trim() ? value : '官方未提供'
}

function officialNumber(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value)
    ? '官方未提供'
    : value.toLocaleString('zh-CN')
}

function officialList(values: string[] | null | undefined): string {
  return values?.length ? values.join('、') : '官方未提供'
}

function catalogDate(value: string | null | undefined): string {
  return formatApiDateTime(value, '官方未提供')
}

function pricingItemText(item: ModelCatalogModel['pricing_tiers'][number]['items'][number]): string {
  const name = item.price_name?.trim() ? `${item.price_name}：` : ''
  const type = item.type?.trim() || '未分类'
  const rawPrice = item.price === null || item.price === undefined ? '' : String(item.price)
  const price = rawPrice.trim() ? rawPrice : '官方未提供'
  const unit = item.price_unit?.trim() ? item.price_unit : '官方未提供'
  return `${name}${type} · ${price} · ${unit}`
}

function pricingTierText(tier: ModelCatalogModel['pricing_tiers'][number]): string {
  const range = tier.range_name?.trim() || '未分类区间'
  return `${range}：${tier.items.length ? tier.items.map(pricingItemText).join('；') : '官方未提供'}`
}

function metadataSourceLabel(source: string | null): string {
  return source && source.trim().toUpperCase() !== 'MANUAL' ? `官方目录（${source}）` : '手动维护'
}

function capabilityLabel(code: string): string {
  return capabilityOptions.find((option) => option.value === code)?.label ?? code
}

function modelCapabilityBadges(model: Pick<ModelConfiguration, 'capabilities' | 'supports_reasoning' | 'supports_tool_call' | 'supports_structured_output'>): string[] {
  const capabilities = (model.capabilities ?? []).map((capability) => capability.trim()).filter(Boolean)
  const badges = [...capabilities]
  if (model.supports_reasoning && !capabilities.some((capability) => capability.toLowerCase() === 'reasoning')) {
    badges.push('Reasoning')
  }
  if (model.supports_tool_call) badges.push('TOOL_CALL')
  if (model.supports_structured_output) badges.push('STRUCTURED_OUTPUT')
  return badges
}

function modelCapabilityLabel(code: string): string {
  if (code === 'TOOL_CALL') return 'Tool Call'
  if (code === 'STRUCTURED_OUTPUT') return '结构化输出'
  return capabilityLabel(code)
}

function capabilityList(values: string[] | null | undefined): string {
  return values?.length ? values.map(capabilityLabel).join('、') : '官方未提供'
}

function modelCategoryLabel(
  category: ModelCategory | null | undefined,
  modelType?: ModelType,
  metadataSource?: string | null,
): string {
  if (category) return modelCategoryLabels[category] ?? category
  if (metadataSource && metadataSource.trim().toUpperCase() !== 'MANUAL') return '官方未提供'
  return modelType ? `手动分类：${modelTypeLabels[modelType] ?? modelType}` : '官方未提供'
}

function resetCatalogFilters(): void {
  Object.assign(catalogFilters, {
    q: '', capabilities: [], tool_call: false, structured_output: false,
  })
  catalogPage.value = 1
}

function resetCatalogState(): void {
  catalogRequestSequence.value += 1
  catalogLoading.value = false
  catalogError.value = ''
  catalogResult.value = undefined
  catalogConnectionId.value = undefined
  resetCatalogFilters()
}

function openCatalogSelector(): void {
  resetCatalogFilters()
  catalogError.value = ''
  catalogResult.value = undefined
  catalogConnectionId.value = (
    enabledConnections.value.find((connection) => connection.provider.trim().toUpperCase() === 'ALIYUN_BAILIAN')
      ?? enabledConnections.value[0]
  )?.id
  catalogDialogVisible.value = true
}

function handleCatalogConnectionChange(): void {
  catalogRequestSequence.value += 1
  catalogPage.value = 1
  catalogError.value = ''
  catalogResult.value = undefined
}

function catalogQuery(): ModelCatalogQuery {
  const query: ModelCatalogQuery = { page: catalogPage.value, page_size: catalogPageSize.value }
  if (catalogFilters.q.trim()) query.q = catalogFilters.q.trim()
  if (catalogFilters.capabilities.length) query.capabilities = [...catalogFilters.capabilities]
  if (catalogFilters.tool_call) query.tool_call = true
  if (catalogFilters.structured_output) query.structured_output = true
  return query
}

async function searchCatalog(): Promise<void> {
  const sequence = catalogRequestSequence.value + 1
  catalogRequestSequence.value = sequence
  const connectionId = catalogConnectionId.value
  catalogError.value = ''
  catalogResult.value = undefined
  if (!connectionId) {
    catalogError.value = '请先选择一个已启用的接入渠道。'
    return
  }
  if (!catalogSupported.value) {
    catalogError.value = '该渠道暂不支持在线目录，可手动添加。'
    return
  }
  catalogLoading.value = true
  try {
    const result = await getCatalogModels(connectionId, catalogQuery())
    if (sequence === catalogRequestSequence.value && catalogDialogVisible.value) {
      catalogResult.value = result
    }
  } catch (error) {
    if (sequence === catalogRequestSequence.value && catalogDialogVisible.value) {
      catalogError.value = getApiErrorMessage(error, '模型目录加载失败，请稍后重试')
    }
  } finally {
    if (sequence === catalogRequestSequence.value) catalogLoading.value = false
  }
}

function changeCatalogPage(page: number): void {
  catalogPage.value = page
  void searchCatalog()
}

function changeCatalogPageSize(pageSize: number): void {
  catalogPageSize.value = pageSize
  catalogPage.value = 1
  void searchCatalog()
}

function openCatalogImport(model: ModelCatalogModel): void {
  if (!model.supported_by_platform || !catalogConnectionId.value) return
  catalogImportModel.value = model
  catalogImportForm.configuration_name = model.name || model.model_id
  catalogImportForm.timeout_seconds = 60
  catalogImportForm.enabled = true
  catalogImportDialogVisible.value = true
}

async function submitCatalogImport(): Promise<void> {
  const model = catalogImportModel.value
  const connectionId = catalogConnectionId.value
  if (!model || !connectionId) return
  const payload: ModelCatalogImportPayload = {
    model_id: model.model_id,
    configuration_name: catalogImportForm.configuration_name.trim() || model.name || model.model_id,
    timeout_seconds: catalogImportForm.timeout_seconds,
    enabled: catalogImportForm.enabled,
  }
  catalogImporting.value = true
  try {
    await importCatalogModel(connectionId, payload)
    catalogImportDialogVisible.value = false
    ElMessage.success(`已导入模型：${model.name || model.model_id}`)
    await loadModels()
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '模型导入失败，请稍后重试'))
  } finally {
    catalogImporting.value = false
  }
}

async function loadConnections(): Promise<void> {
  connectionsLoading.value = true
  try {
    connections.value = await getConnections(isAdmin.value)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '接入渠道加载失败，请稍后重试'))
  } finally {
    connectionsLoading.value = false
  }
}

async function loadModels(): Promise<void> {
  modelsLoading.value = true
  try {
    models.value = await getModels(isAdmin.value)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '模型配置加载失败，请稍后重试'))
  } finally {
    modelsLoading.value = false
  }
}

async function loadAll(): Promise<void> {
  await Promise.all([loadConnections(), loadModels()])
}

function resetConnectionForm(): void {
  Object.assign(connectionForm, {
    name: '', access_type: 'DIRECT', provider: '', protocol_type: 'OPENAI_COMPATIBLE',
    base_url: '', enabled: true,
  })
}

function resetConnectionApiKeyState(): void {
  connectionApiKeyInput.value = ''
  clearConnectionApiKeyRequested.value = false
  editingConnectionHasApiKey.value = false
  editingConnectionApiKeyMasked.value = null
}

function openCreateConnection(): void {
  editingConnectionId.value = undefined
  resetConnectionForm()
  resetConnectionApiKeyState()
  connectionDialogVisible.value = true
}

function openEditConnection(connection: ModelProviderConnection): void {
  editingConnectionId.value = connection.id
  Object.assign(connectionForm, {
    name: connection.name,
    access_type: connection.access_type,
    provider: connection.provider,
    protocol_type: connection.protocol_type,
    base_url: connection.base_url,
    enabled: connection.enabled,
  })
  connectionApiKeyInput.value = ''
  clearConnectionApiKeyRequested.value = false
  editingConnectionHasApiKey.value = connection.has_api_key
  editingConnectionApiKeyMasked.value = connection.api_key_masked
  connectionDialogVisible.value = true
}

async function requestClearConnectionApiKey(): Promise<void> {
  if (!editingConnectionId.value || !editingConnectionHasApiKey.value || clearConnectionApiKeyRequested.value) return
  try {
    await ElMessageBox.confirm(
      '清除后该接入渠道将不再使用当前 API Key，点击“保存”后生效；旧值不会再次显示或恢复。',
      '确认清除 API Key',
      { type: 'warning', confirmButtonText: '确认清除', cancelButtonText: '取消' },
    )
    clearConnectionApiKeyRequested.value = true
    connectionApiKeyInput.value = ''
    ElMessage.success('已标记清除，点击保存后生效')
  } catch {
    // 用户取消确认时不改变当前配置。
  }
}

async function submitConnection(): Promise<void> {
  if (!connectionForm.name.trim() || !connectionForm.provider.trim() || !connectionForm.base_url.trim()) {
    ElMessage.warning('请填写渠道名称、服务商和 Base URL')
    return
  }
  const currentId = editingConnectionId.value
  const enteredApiKey = connectionApiKeyInput.value.trim() ? connectionApiKeyInput.value : undefined
  try {
    if (currentId) {
      const payload: ModelProviderConnectionUpdatePayload = { ...connectionForm }
      if (clearConnectionApiKeyRequested.value) payload.api_key = null
      else if (enteredApiKey !== undefined) payload.api_key = enteredApiKey
      await updateConnection(currentId, payload)
    } else {
      const payload: ModelProviderConnectionCreatePayload = {
        ...connectionForm,
        api_key: enteredApiKey ?? null,
      }
      await createConnection(payload)
    }
    connectionDialogVisible.value = false
    ElMessage.success(currentId ? '接入渠道已更新' : '接入渠道已创建')
    await loadConnections()
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '接入渠道保存失败，请检查配置后重试'))
  }
}

async function toggleConnection(connection: ModelProviderConnection): Promise<void> {
  try {
    await updateConnection(connection.id, { enabled: !connection.enabled })
    await loadConnections()
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '接入渠道状态更新失败，请稍后重试'))
  }
}

function resetModelForm(): void {
  Object.assign(modelForm, {
    name: '', connection_id: 0, model_vendor: '', model_name: '', model_type: 'TEXT',
    supports_tool_call: false, supports_structured_output: true,
    max_context: 128000, timeout_seconds: 60, input_price: '0', output_price: '0',
    enabled: true,
  })
}

function openCreateModel(): void {
  editingModelId.value = undefined
  editingModelMetadata.value = null
  manualMetadataSections.value = ['manual']
  resetModelForm()
  modelDialogVisible.value = true
}

function openEditModel(model: ModelConfiguration): void {
  editingModelId.value = model.id
  editingModelMetadata.value = model
  manualMetadataSections.value = ['manual']
  Object.assign(modelForm, {
    name: model.name,
    connection_id: model.connection_id,
    model_vendor: model.model_vendor,
    model_name: model.model_name,
    model_type: model.model_type,
    supports_tool_call: model.supports_tool_call,
    supports_structured_output: model.supports_structured_output,
    max_context: model.max_context,
    timeout_seconds: model.timeout_seconds,
    input_price: model.input_price,
    output_price: model.output_price,
    enabled: model.enabled,
  })
  modelDialogVisible.value = true
}

async function submitModel(): Promise<void> {
  if (!modelForm.name.trim() || !modelForm.model_vendor.trim() || !modelForm.model_name.trim()) {
    ElMessage.warning('请填写配置名称、模型归属和模型标识')
    return
  }
  if (!enabledConnections.value.some((item) => item.id === modelForm.connection_id)) {
    ElMessage.warning('请选择已启用的接入渠道')
    return
  }
  const currentId = editingModelId.value
  try {
    if (currentId) {
      const payload: ModelConfigurationUpdatePayload = isCatalogModel.value
        ? { name: modelForm.name, timeout_seconds: modelForm.timeout_seconds, enabled: modelForm.enabled }
        : { ...modelForm }
      await updateModel(currentId, payload)
    } else {
      await createModel({ ...modelForm })
    }
    if (currentId) clearVerification(currentId)
    modelDialogVisible.value = false
    ElMessage.success(currentId ? '模型配置已更新' : '模型配置已创建')
    await loadModels()
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '模型配置保存失败，请检查配置后重试'))
  }
}

async function toggleModel(model: ModelConfiguration): Promise<void> {
  try {
    await updateModel(model.id, { enabled: !model.enabled })
    clearVerification(model.id)
    await loadModels()
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '模型配置状态更新失败，请稍后重试'))
  }
}

function isDeletingModel(modelId: number): boolean {
  return deletingModelIds.value.has(modelId)
}

function setDeletingModel(modelId: number, deleting: boolean): void {
  const next = new Set(deletingModelIds.value)
  if (deleting) next.add(modelId)
  else next.delete(modelId)
  deletingModelIds.value = next
}

async function deleteModelConfiguration(model: ModelConfiguration): Promise<void> {
  if (!isAdmin.value || isDeletingModel(model.id)) return
  setDeletingModel(model.id, true)
  try {
    await ElMessageBox.confirm(
      `删除模型配置“${model.name}”不可恢复。若它是任务的主模型，对应任务绑定会整条解除；若它是 fallback，相关 fallback 绑定会自动移除。不会删除接入渠道或 API Key。若存在历史 AI 调用引用，服务端会拒绝删除，并建议先停用模型。`,
      '确认删除模型配置',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' },
    )
  } catch {
    setDeletingModel(model.id, false)
    return
  }

  try {
    await deleteModel(model.id)
    clearVerification(model.id)
    if (editingModelId.value === model.id) {
      modelDialogVisible.value = false
      editingModelId.value = undefined
      editingModelMetadata.value = null
      resetModelForm()
    }
    ElMessage.success('模型配置已删除，相关任务绑定已自动更新')
    await loadModels()
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '模型配置删除失败，请稍后重试'))
  } finally {
    setDeletingModel(model.id, false)
  }
}

function isVerifying(modelId: number): boolean {
  return verifyingModelIds.value.has(modelId)
}

function setVerifying(modelId: number, verifying: boolean): void {
  const next = new Set(verifyingModelIds.value)
  if (verifying) next.add(modelId)
  else next.delete(modelId)
  verifyingModelIds.value = next
}

function clearVerification(modelId: number): void {
  const next = { ...verificationResults.value }
  delete next[modelId]
  verificationResults.value = next
}

function verificationSucceeded(result: ModelConnectionVerification): boolean {
  return result.success && result.status === 'SUCCESS'
}

function formatVerificationDuration(durationMs: number): string {
  return Number.isFinite(durationMs) && durationMs >= 0 ? `${Math.round(durationMs)} ms` : '—'
}

async function verifyConnection(model: ModelConfiguration): Promise<void> {
  if (!isAdmin.value || isVerifying(model.id)) return
  setVerifying(model.id, true)
  try {
    const result = await verifyModelConnection(model.id)
    verificationResults.value = { ...verificationResults.value, [model.id]: result }
    const statusText = verificationSucceeded(result) ? '连接验证成功' : '连接验证失败'
    const errorType = result.error_type ? `（${result.error_type}）` : ''
    const summary = result.summary.trim() || '服务端未提供验证摘要'
    const message = `${model.name}：${statusText}，${summary}${errorType}，耗时 ${formatVerificationDuration(result.duration_ms)}`
    if (verificationSucceeded(result)) ElMessage.success(message)
    else ElMessage.warning(message)
  } catch (error) {
    clearVerification(model.id)
    ElMessage.error(`${model.name}：${getApiErrorMessage(error, '连接验证失败，请稍后重试')}`)
  } finally {
    setVerifying(model.id, false)
  }
}

watch(connectionApiKeyInput, (value) => {
  if (value.trim()) clearConnectionApiKeyRequested.value = false
})

onMounted(loadAll)
</script>

<template>
  <div class="projects-page">
    <header class="page-heading">
      <div><span class="eyebrow dark">模型管理</span><h1>模型中心</h1><p>按“接入渠道 → 模型配置 → 项目任务绑定”管理 AI 模型；API Key 仅加密保存且不会回显。</p></div>
      <div class="requirement-actions"><el-button :icon="Refresh" :loading="pageRefreshing" @click="loadAll">刷新</el-button><el-button v-if="isAdmin && activeTab === 'connections'" type="primary" :icon="Plus" @click="openCreateConnection">添加接入渠道</el-button><template v-if="isAdmin && activeTab === 'models'"><el-button type="primary" :icon="Plus" :disabled="enabledConnections.length === 0" @click="openCatalogSelector">从渠道选择模型</el-button><el-button plain :icon="Plus" :disabled="enabledConnections.length === 0" @click="openCreateModel">手动添加</el-button></template></div>
    </header>

    <el-tabs v-model="activeTab" class="settings-tabs">
      <el-tab-pane label="接入渠道" name="connections">
        <section class="project-table-card">
          <el-alert v-if="!connectionsLoading && !connections.length" title="暂无接入渠道，请先添加并启用一个渠道。" type="info" show-icon :closable="false" />
          <el-table v-else v-loading="connectionsLoading" :data="pagedConnections">
            <el-table-column prop="name" label="渠道名称" min-width="180" />
            <el-table-column label="接入类型" width="130"><template #default="{ row }: { row: ModelProviderConnection }">{{ accessTypeLabel(row.access_type) }}</template></el-table-column>
            <el-table-column label="服务商" min-width="160"><template #default="{ row }: { row: ModelProviderConnection }">{{ providerDisplayName(row.provider) }}</template></el-table-column>
            <el-table-column prop="base_url" label="Base URL" min-width="240" />
            <el-table-column prop="protocol_type" label="协议" min-width="170" />
            <el-table-column label="API Key" min-width="190"><template #default="{ row }: { row: ModelProviderConnection }"><div class="api-key-state"><el-tag :type="row.has_api_key ? 'success' : 'info'" size="small">{{ row.has_api_key ? '已配置' : '未配置' }}</el-tag><span v-if="row.has_api_key && row.api_key_masked" class="muted">{{ row.api_key_masked }}</span><span class="muted">轮换：{{ formatDate(row.api_key_rotated_at) }}</span></div></template></el-table-column>
            <el-table-column label="状态" width="100"><template #default="{ row }: { row: ModelProviderConnection }"><el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag></template></el-table-column>
            <el-table-column label="操作" :width="isAdmin ? 220 : 80"><template #default="{ row }: { row: ModelProviderConnection }"><template v-if="isAdmin"><el-button link type="primary" @click="openEditConnection(row)">编辑</el-button><el-button link :type="row.enabled ? 'danger' : 'success'" @click="toggleConnection(row)">{{ row.enabled ? '停用' : '启用' }}</el-button></template><span v-else class="muted">只读</span></template></el-table-column>
          </el-table>
          <el-pagination v-if="connectionTotal" class="records-pagination" layout="total, sizes, prev, pager, next" :current-page="connectionPage" :page-size="connectionPageSize" :page-sizes="[...RECORD_PAGE_SIZES]" :total="connectionTotal" @current-change="changeConnectionPage" @size-change="changeConnectionPageSize" />
        </section>
      </el-tab-pane>

      <el-tab-pane label="模型配置" name="models">
        <el-alert v-if="!enabledConnections.length" title="请先添加并启用接入渠道，再添加模型配置。" type="info" show-icon :closable="false" class="configuration-notice" />
        <section class="project-table-card">
          <el-alert v-if="!modelsLoading && !models.length" title="暂无模型配置，请先选择已启用的接入渠道并添加模型。" type="info" show-icon :closable="false" />
          <el-table v-else v-loading="modelsLoading" :data="pagedModels">
            <el-table-column prop="name" label="配置名称" min-width="170" />
            <el-table-column prop="model_vendor" label="模型归属" min-width="150" />
            <el-table-column prop="model_name" label="模型标识" min-width="170" />
            <el-table-column label="接入渠道" min-width="180"><template #default="{ row }: { row: ModelConfiguration }">{{ connectionName(row.connection_id) }}</template></el-table-column>
            <el-table-column label="渠道类型" width="130"><template #default="{ row }: { row: ModelConfiguration }">{{ connectionType(row.connection_id) }}</template></el-table-column>
            <el-table-column label="模型主分类" min-width="140"><template #default="{ row }: { row: ModelConfiguration }">{{ modelCategoryLabel(row.model_category, row.model_type, row.metadata_source) }}</template></el-table-column>
            <el-table-column label="平台调用类型" width="130"><template #default="{ row }: { row: ModelConfiguration }">{{ modelTypeLabels[row.model_type] }}</template></el-table-column>
            <el-table-column label="能力" min-width="210"><template #default="{ row }: { row: ModelConfiguration }"><el-tag v-for="capability in modelCapabilityBadges(row)" :key="`model-capability-${row.id}-${capability}`" size="small" effect="plain" :title="capability">{{ modelCapabilityLabel(capability) }}</el-tag></template></el-table-column>
            <el-table-column label="上下文" width="120"><template #default="{ row }: { row: ModelConfiguration }">{{ officialNumber(row.max_context) }}</template></el-table-column>
            <el-table-column label="元数据" min-width="150"><template #default="{ row }: { row: ModelConfiguration }"><div>{{ metadataSourceLabel(row.metadata_source) }}</div><span v-if="row.metadata_synced_at" class="muted">同步：{{ catalogDate(row.metadata_synced_at) }}</span></template></el-table-column>
            <el-table-column label="状态" width="100"><template #default="{ row }: { row: ModelConfiguration }"><el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag></template></el-table-column>
            <el-table-column v-if="isAdmin" label="连接验证" min-width="270"><template #default="{ row }: { row: ModelConfiguration }"><div v-if="verificationResults[row.id]" class="verification-result"><el-tag :type="verificationSucceeded(verificationResults[row.id]) ? 'success' : 'danger'" size="small">{{ verificationSucceeded(verificationResults[row.id]) ? '成功' : '失败' }}</el-tag><span>{{ verificationResults[row.id].summary || '服务端未提供验证摘要' }}</span><span class="verification-meta">{{ formatVerificationDuration(verificationResults[row.id].duration_ms) }}<template v-if="verificationResults[row.id].error_type"> · {{ verificationResults[row.id].error_type }}</template></span></div><span v-else class="muted">尚未验证</span></template></el-table-column>
            <el-table-column label="操作" :width="isAdmin ? 330 : 80"><template #default="{ row }: { row: ModelConfiguration }"><template v-if="isAdmin"><el-button link type="primary" :loading="isVerifying(row.id)" :disabled="isVerifying(row.id) || isDeletingModel(row.id)" @click="verifyConnection(row)">验证连接</el-button><el-button link type="primary" :disabled="isDeletingModel(row.id)" @click="openEditModel(row)">编辑</el-button><el-button link :type="row.enabled ? 'danger' : 'success'" :disabled="isDeletingModel(row.id)" @click="toggleModel(row)">{{ row.enabled ? '停用' : '启用' }}</el-button><el-button link type="danger" :loading="isDeletingModel(row.id)" :disabled="isDeletingModel(row.id)" @click="deleteModelConfiguration(row)">删除</el-button></template><span v-else class="muted">只读</span></template></el-table-column>
          </el-table>
          <el-pagination v-if="modelTotal" class="records-pagination" layout="total, sizes, prev, pager, next" :current-page="modelPage" :page-size="modelPageSize" :page-sizes="[...RECORD_PAGE_SIZES]" :total="modelTotal" @current-change="changeModelPage" @size-change="changeModelPageSize" />
        </section>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="catalogDialogVisible" title="从渠道选择模型" width="1100px" top="5vh" @closed="resetCatalogState">
      <div class="catalog-toolbar"><el-select v-model="catalogConnectionId" placeholder="选择已启用渠道" @change="handleCatalogConnectionChange"><el-option v-for="connection in enabledConnections" :key="connection.id" :label="connectionLabel(connection)" :value="connection.id" /></el-select><el-input v-model="catalogFilters.q" clearable placeholder="搜索模型名称或 ID" @keyup.enter="searchCatalog" /><el-select v-model="catalogFilters.capabilities" multiple collapse-tags collapse-tags-tooltip clearable placeholder="官方能力/模态"><el-option v-for="option in capabilityOptions" :key="option.value" :label="`${option.label}（${option.value}）`" :value="option.value" /></el-select><el-checkbox v-model="catalogFilters.tool_call">仅支持 Tool Call</el-checkbox><el-checkbox v-model="catalogFilters.structured_output">仅支持结构化输出</el-checkbox><el-button type="primary" :loading="catalogLoading" @click="searchCatalog">搜索</el-button></div>
      <el-alert v-if="catalogConnection && !catalogSupported" title="该渠道暂不支持在线目录，可手动添加。" type="info" show-icon :closable="false" />
      <el-alert v-if="catalogNeedsWorkspaceUrlWarning" title="百炼目录可能需要业务空间专属域名。若搜索失败，请到接入渠道编辑 Base URL 为百炼控制台复制的业务空间专属地址，例如 https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1；不会自动替换或猜测 WorkspaceId。" type="warning" show-icon :closable="false" />
      <el-alert v-if="catalogError" :title="catalogError" type="error" show-icon :closable="false" />
      <div v-loading="catalogLoading" class="catalog-results">
        <el-empty v-if="!catalogLoading && catalogResult && !catalogResult.items.length" description="没有符合条件的模型" />
        <div v-else-if="catalogResult?.items.length" class="catalog-model-list">
          <article v-for="model in catalogResult.items" :key="model.model_id" class="catalog-model-card">
            <header class="catalog-model-heading"><div><strong>{{ model.name || model.model_id }}</strong><code>{{ model.model_id }}</code></div><el-tag :type="model.supported_by_platform ? 'success' : 'info'" size="small">{{ model.supported_by_platform ? '可导入' : '暂不支持' }}</el-tag></header>
            <div class="catalog-tags"><el-tag size="small" effect="plain">{{ modelCategoryLabel(model.model_category) }}</el-tag><el-tag v-if="model.supports_tool_call" size="small">Tool Call</el-tag><el-tag v-if="model.supports_structured_output" size="small" type="success">结构化输出</el-tag><el-tag v-for="capability in model.capabilities" :key="`capability-${model.model_id}-${capability}`" size="small" effect="plain" :title="capability">{{ capabilityLabel(capability) }}</el-tag></div>
            <p class="catalog-description">{{ officialText(model.description) }}</p>
            <div class="catalog-meta-grid"><div><span>厂商</span><strong>{{ officialText(model.provider) }}</strong></div><div><span>推理服务商</span><strong>{{ officialText(model.inference_provider) }}</strong></div><div><span>上下文</span><strong>{{ officialNumber(model.context_window) }}</strong></div><div><span>最大输入</span><strong>{{ officialNumber(model.max_input_tokens) }}</strong></div><div><span>最大输出</span><strong>{{ officialNumber(model.max_output_tokens) }}</strong></div><div><span>最大推理</span><strong>{{ officialNumber(model.max_reasoning_tokens) }}</strong></div><div><span>输入模态</span><strong>{{ officialList(model.input_modalities) }}</strong></div><div><span>输出模态</span><strong>{{ officialList(model.output_modalities) }}</strong></div></div>
            <div class="catalog-secondary"><span>能力：{{ capabilityList(model.capabilities) }}</span><span>特性：{{ officialList(model.features) }}</span><span>平台调用类型：{{ modelTypeLabels[model.model_type] }}</span><span>推理输入/输出：{{ officialNumber(model.reasoning_max_input_tokens) }} / {{ officialNumber(model.reasoning_max_output_tokens) }}</span><span>发布时间：{{ catalogDate(model.published_time) }}</span></div>
            <div class="catalog-pricing"><strong>分层价格</strong><span v-if="!model.pricing_tiers.length">官方未提供</span><span v-for="tier in model.pricing_tiers" :key="`${model.model_id}-${tier.range_name}`">{{ pricingTierText(tier) }}</span></div>
            <footer class="catalog-model-footer"><span>来源：{{ officialText(catalogResult?.source) }} · 同步：{{ catalogDate(catalogResult?.fetched_at) }}</span><div><span v-if="!model.supported_by_platform" class="catalog-unsupported-reason">{{ model.unsupported_reason || '平台暂不支持该模型' }}</span><el-button type="primary" size="small" :disabled="!model.supported_by_platform" @click="openCatalogImport(model)">导入模型</el-button></div></footer>
          </article>
        </div>
      </div>
      <el-pagination v-if="catalogResult?.total" class="catalog-pagination" background layout="total, sizes, prev, pager, next" :current-page="catalogResult.page" :page-size="catalogResult.page_size" :page-sizes="[...RECORD_PAGE_SIZES]" :total="catalogResult.total" @current-change="changeCatalogPage" @size-change="changeCatalogPageSize" />
    </el-dialog>

    <el-dialog v-model="catalogImportDialogVisible" title="导入目录模型" width="560px">
      <el-form label-position="top"><el-form-item label="官方模型"><div>{{ catalogImportModel?.name || '—' }}<code v-if="catalogImportModel">{{ catalogImportModel.model_id }}</code></div></el-form-item><el-form-item label="配置名称"><el-input v-model="catalogImportForm.configuration_name" placeholder="默认使用官方模型名称" /></el-form-item><el-form-item label="超时（秒）"><el-input-number v-model="catalogImportForm.timeout_seconds" :min="1" :max="600" /></el-form-item><el-form-item label="状态"><el-checkbox v-model="catalogImportForm.enabled">导入后启用</el-checkbox></el-form-item></el-form>
      <template #footer><el-button @click="catalogImportDialogVisible = false">取消</el-button><el-button type="primary" :loading="catalogImporting" @click="submitCatalogImport">确认导入</el-button></template>
    </el-dialog>

    <el-dialog v-model="connectionDialogVisible" :title="editingConnectionId ? '编辑接入渠道' : '添加接入渠道'" width="760px" @closed="resetConnectionApiKeyState">
      <el-form label-position="top">
        <div class="config-form-grid"><el-form-item label="渠道名称"><el-input v-model="connectionForm.name" /><span class="field-hint">渠道名称是用户自定义备注。</span></el-form-item><el-form-item label="接入类型"><el-select v-model="connectionForm.access_type" @change="handleAccessTypeChange"><el-option v-for="(label, value) in accessTypeLabels" :key="value" :label="label" :value="value" /></el-select></el-form-item><el-form-item label="服务商"><el-select v-model="connectionForm.provider" filterable allow-create default-first-option placeholder="选择或输入服务商" @change="handleProviderChange"><el-option v-for="preset in providerOptions" :key="preset.value" :label="preset.label" :value="preset.value" /></el-select><span class="field-hint">可选择预设，也可输入自定义服务商值。</span></el-form-item><el-form-item label="协议"><el-input :model-value="connectionForm.protocol_type" disabled /></el-form-item><el-form-item label="Base URL"><el-select v-model="connectionForm.base_url" filterable allow-create default-first-option placeholder="选择或输入完整 Base URL"><el-option v-for="option in baseUrlOptions" :key="option.value" :label="option.label" :value="option.value" /></el-select><span class="field-hint">不要包含 /chat/completions，后端会自动追加。</span><span v-if="connectionForm.provider.trim().toUpperCase() === 'ALIYUN_BAILIAN'" class="field-hint">若目录搜索失败，请改为百炼控制台复制的业务空间专属地址，例如 https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1。</span></el-form-item>
          <el-form-item label="API Key"><div v-if="editingConnectionId" class="api-key-state">当前状态：<el-tag :type="editingConnectionHasApiKey ? 'success' : 'info'" size="small">{{ editingConnectionHasApiKey ? '已配置' : '未配置' }}</el-tag><code v-if="editingConnectionHasApiKey" class="api-key-mask">{{ editingConnectionApiKeyMasked ?? '••••••••' }}</code></div><el-input v-model="connectionApiKeyInput" type="password" show-password autocomplete="new-password" :placeholder="editingConnectionHasApiKey ? '已存在凭据；输入新值才会替换' : '可留空（免鉴权本地服务）'" /><span class="field-hint">现有值以脱敏摘要显示，不返回完整密钥。本地免鉴权服务可留空。{{ editingConnectionId ? '输入新值将轮换；不输入则保留当前值。' : '新建时留空表示不配置 API Key。' }}</span><div v-if="editingConnectionId && editingConnectionHasApiKey"><el-button link type="danger" :disabled="clearConnectionApiKeyRequested" @click="requestClearConnectionApiKey">{{ clearConnectionApiKeyRequested ? '已选择清除' : '清除 API Key' }}</el-button><span v-if="clearConnectionApiKeyRequested" class="field-hint">已确认清除，点击保存后生效。</span></div></el-form-item>
          <el-form-item label="状态"><el-checkbox v-model="connectionForm.enabled">启用此接入渠道</el-checkbox></el-form-item>
        </div>
      </el-form>
      <template #footer><el-button @click="connectionDialogVisible = false">取消</el-button><el-button type="primary" @click="submitConnection">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="modelDialogVisible" :title="editingModelId ? '编辑模型配置' : '添加模型配置'" width="900px">
      <el-form label-position="top">
        <el-alert v-if="isCatalogModel" title="以下官方元数据来自渠道目录，仅供查看；可编辑的运行参数为配置名称、超时和启用状态。" type="info" show-icon :closable="false" />
        <section v-if="isCatalogModel && editingModelMetadata" class="model-metadata-panel"><div class="model-metadata-grid"><div><span>数据来源</span><strong>{{ metadataSourceLabel(editingModelMetadata.metadata_source) }}</strong></div><div><span>同步时间</span><strong>{{ catalogDate(editingModelMetadata.metadata_synced_at) }}</strong></div><div><span>官方模型归属</span><strong>{{ officialText(editingModelMetadata.model_vendor) }}</strong></div><div><span>官方模型标识</span><strong>{{ officialText(editingModelMetadata.model_name) }}</strong></div><div><span>模型主分类</span><strong>{{ modelCategoryLabel(editingModelMetadata.model_category, editingModelMetadata.model_type, editingModelMetadata.metadata_source) }}</strong></div><div><span>平台调用类型</span><strong>{{ modelTypeLabels[editingModelMetadata.model_type] }}</strong></div><div><span>发布时间</span><strong>{{ catalogDate(editingModelMetadata.published_at) }}</strong></div><div><span>上下文</span><strong>{{ officialNumber(editingModelMetadata.max_context) }}</strong></div><div><span>最大输入</span><strong>{{ officialNumber(editingModelMetadata.max_input_tokens) }}</strong></div><div><span>最大输出</span><strong>{{ officialNumber(editingModelMetadata.max_output_tokens) }}</strong></div><div><span>最大推理</span><strong>{{ officialNumber(editingModelMetadata.max_reasoning_tokens) }}</strong></div><div><span>推理输入/输出</span><strong>{{ officialNumber(editingModelMetadata.reasoning_max_input_tokens) }} / {{ officialNumber(editingModelMetadata.reasoning_max_output_tokens) }}</strong></div><div><span>推理能力</span><strong>{{ editingModelMetadata.supports_reasoning ? '支持' : '不支持' }}</strong></div><div><span>输入模态</span><strong>{{ officialList(editingModelMetadata.input_modalities) }}</strong></div><div><span>输出模态</span><strong>{{ officialList(editingModelMetadata.output_modalities) }}</strong></div><div><span>Tool Call</span><strong>{{ editingModelMetadata.supports_tool_call ? '支持' : '不支持' }}</strong></div><div><span>结构化输出</span><strong>{{ editingModelMetadata.supports_structured_output ? '支持' : '不支持' }}</strong></div><div class="model-metadata-wide"><span>官方描述</span><strong>{{ officialText(editingModelMetadata.description) }}</strong></div><div class="model-metadata-wide"><span>能力</span><strong>{{ capabilityList(editingModelMetadata.capabilities) }}</strong></div><div class="model-metadata-wide"><span>特性</span><strong>{{ officialList(editingModelMetadata.features) }}</strong></div><div class="model-metadata-wide"><span>分层价格</span><strong><span v-if="!editingModelMetadata.pricing_tiers.length">官方未提供</span><span v-for="tier in editingModelMetadata.pricing_tiers" :key="`${editingModelMetadata.id}-${tier.range_name}`">{{ pricingTierText(tier) }}</span></strong></div></div></section>
        <div class="config-form-grid"><el-form-item label="配置名称"><el-input v-model="modelForm.name" /><span class="field-hint">这是本平台中的显示别名。</span></el-form-item><el-form-item label="接入渠道"><el-select v-model="modelForm.connection_id" placeholder="选择已启用接入渠道" :disabled="isCatalogModel"><el-option v-for="connection in modelConnectionOptions" :key="connection.id" :label="connectionLabel(connection)" :value="connection.id" :disabled="!connection.enabled">{{ connectionLabel(connection) }}{{ connection.enabled ? '' : '（已停用，请更换）' }}</el-option></el-select><span class="field-hint">模型通过所选渠道访问；只能保存到已启用渠道。</span></el-form-item><el-form-item label="超时（秒）"><el-input-number v-model="modelForm.timeout_seconds" :min="1" :max="600" /></el-form-item><el-form-item label="状态"><el-checkbox v-model="modelForm.enabled">启用此模型配置</el-checkbox></el-form-item></div>
        <el-collapse v-if="!isCatalogModel" v-model="manualMetadataSections"><el-collapse-item title="高级 / 手动维护元数据" name="manual"><span class="field-hint">手动模型的归属、标识、类型、上下文、价格和能力由管理员维护，不会自动从渠道目录同步。</span><div class="config-form-grid"><el-form-item label="模型归属"><el-input v-model="modelForm.model_vendor" placeholder="例如 OpenAI、DeepSeek" /></el-form-item><el-form-item label="模型标识"><el-input v-model="modelForm.model_name" /></el-form-item><el-form-item label="模型类型"><el-select v-model="modelForm.model_type"><el-option value="TEXT" /><el-option value="VISION" /><el-option value="EMBEDDING" /></el-select></el-form-item><el-form-item label="最大上下文"><el-input-number v-model="modelForm.max_context" :min="1024" /></el-form-item><el-form-item label="输入价格"><el-input v-model="modelForm.input_price" /></el-form-item><el-form-item label="输出价格"><el-input v-model="modelForm.output_price" /></el-form-item><el-form-item label="能力"><el-checkbox v-model="modelForm.supports_structured_output">结构化输出</el-checkbox><el-checkbox v-model="modelForm.supports_tool_call">Tool Call</el-checkbox></el-form-item></div></el-collapse-item></el-collapse>
      </el-form>
      <template #footer><el-button @click="modelDialogVisible = false">取消</el-button><el-button type="primary" @click="submitModel">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.api-key-state { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }
.api-key-mask { padding: 2px 7px; border-radius: 5px; color: #52627a; background: #f0f3f8; font-family: "JetBrains Mono", Consolas, monospace; font-size: 12px; }
.catalog-toolbar { display: grid; grid-template-columns: minmax(210px, 1.1fr) minmax(180px, 1.5fr) minmax(130px, .7fr); gap: 12px; align-items: center; margin-bottom: 16px; }
.catalog-toolbar .el-checkbox { margin-right: 0; }
.catalog-results { min-height: 260px; }
.catalog-model-list { display: grid; gap: 14px; margin-top: 16px; }
.catalog-model-card { padding: 16px; border: 1px solid #e4eaf3; border-radius: 12px; background: #fff; }
.catalog-model-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.catalog-model-heading > div { min-width: 0; }
.catalog-model-heading strong { display: block; color: #213557; font-size: 16px; }
.catalog-model-heading code, .catalog-import-model code { display: block; margin-top: 5px; overflow-wrap: anywhere; color: #7c8799; font-family: "JetBrains Mono", Consolas, monospace; font-size: 12px; }
.catalog-tags { display: flex; flex-wrap: wrap; gap: 6px; margin: 12px 0; }
.catalog-description { margin: 0 0 14px; color: #53627b; line-height: 1.6; white-space: pre-wrap; }
.catalog-meta-grid, .model-metadata-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.catalog-meta-grid > div, .model-metadata-grid > div { min-width: 0; padding: 10px; border-radius: 8px; background: #f7f9fc; }
.catalog-meta-grid span, .model-metadata-grid span { display: block; margin-bottom: 5px; color: #8994a6; font-size: 12px; }
.catalog-meta-grid strong, .model-metadata-grid strong { display: block; overflow-wrap: anywhere; color: #34445f; font-size: 13px; line-height: 1.45; }
.catalog-secondary, .catalog-pricing { display: grid; gap: 6px; margin-top: 14px; color: #68758c; font-size: 12px; line-height: 1.5; }
.catalog-pricing { padding-top: 12px; border-top: 1px solid #edf0f5; }
.catalog-pricing strong { color: #465875; }
.catalog-model-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-top: 16px; color: #8994a6; font-size: 12px; }
.catalog-model-footer > div { display: flex; align-items: center; gap: 10px; }
.catalog-unsupported-reason { max-width: 520px; color: #b7791f; }
.catalog-pagination { justify-content: center; margin-top: 18px; }
.configuration-notice { margin-bottom: 14px; }
.model-metadata-panel { margin: 16px 0; padding: 14px; border: 1px solid #e4eaf3; border-radius: 10px; background: #fbfcfe; }
.model-metadata-wide { grid-column: 1 / -1; }

@media (max-width: 900px) {
  .page-heading { align-items: flex-start; flex-direction: column; gap: 14px; }
  .requirement-actions { flex-wrap: wrap; }
  .catalog-toolbar { grid-template-columns: 1fr; }
  .catalog-meta-grid, .model-metadata-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 560px) {
  .catalog-meta-grid, .model-metadata-grid { grid-template-columns: 1fr; }
  .model-metadata-wide { grid-column: auto; }
  .catalog-model-footer > div { align-items: flex-start; flex-direction: column; }
}
</style>
