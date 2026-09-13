<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, VideoPlay } from '@element-plus/icons-vue'

import { getApiDefinitions } from '@/api/api-definitions'
import { getApiErrorMessage } from '@/api/http'
import { getPrompts } from '@/api/prompt-center'
import { getRequirementDocumentVersions } from '@/api/requirements'
import { getRunners } from '@/api/runners'
import { getSecrets } from '@/api/secrets'
import { getWebRecordings } from '@/api/web-recordings'
import {
  createWebDesignRevision,
  createWebExploration,
  createWebPlan,
  decideWebDesignRevision,
  dispatchWebExploration,
  downloadWebExplorationEvidence,
  getWebDesignRevisions,
  getWebExplorations,
  getWebPlanExplorations,
  getWebPlans,
  stopWebExploration,
} from '@/api/web-design'
import type { ApiDefinition } from '@/types/api-definition'
import type { Environment } from '@/types/environment'
import type { PromptDefinition } from '@/types/prompt-center'
import type { RequirementDocumentVersion } from '@/types/requirement'
import type { Runner } from '@/types/runner'
import type { SessionProfileResponse } from '@/types/web'
import type { WebRecordingListItem } from '@/types/web-recording'
import type {
  WebDesignRevision,
  WebExploration,
  WebExplorationEvidence,
  WebExplorationPermission,
  WebPlan,
  WebPlanItem,
} from '@/types/web-design'
import { useClientPagination } from '@/composables/useClientPagination'
import { apiDateTimeMs, formatApiDateTime } from '@/utils/datetime'
import { promptOptionLabel } from '@/utils/prompt-display'

const props = defineProps<{
  projectId?: number
  canWrite: boolean
  environments: Environment[]
  sessionProfiles: SessionProfileResponse[]
}>()

const emit = defineEmits<{
  (event: 'manual-record', item: WebPlanItem): void
  (event: 'refresh-assets'): void
}>()

const loading = ref(false)
const plans = ref<WebPlan[]>([])
const requirementVersions = ref<RequirementDocumentVersion[]>([])
const apiDefinitions = ref<ApiDefinition[]>([])
const planPrompts = ref<PromptDefinition[]>([])
const decisionPrompts = ref<PromptDefinition[]>([])
const reconcilePrompts = ref<PromptDefinition[]>([])
const runners = ref<Runner[]>([])
const selectedPlanId = ref<number | null>(null)
const selectedItemId = ref<number | null>(null)
const explorations = ref<WebExploration[]>([])
const planExplorations = ref<WebExploration[]>([])
const revisions = ref<WebDesignRevision[]>([])
const recordings = ref<WebRecordingListItem[]>([])
const errorMessage = ref<string | null>(null)
const creatingPlan = ref(false)
const creatingExploration = ref(false)
const creatingRevision = ref(false)
const planForm = ref({
  requirement_document_version_id: undefined as number | undefined,
  prompt_id: undefined as number | undefined,
  api_definition_ids: [] as number[],
  additional_instructions: '',
})
const exploreDialog = ref(false)
const traceDialog = ref(false)
const tracedExplorationId = ref<string | null>(null)
const traceEvidenceUrls = ref<Record<string, string>>({})
const traceEvidenceLoading = ref(false)
const exploreAdvancedSections = ref<string[]>([])
const exploreCredentialState = ref<'not-needed' | 'loading' | 'ready' | 'missing' | 'error'>('not-needed')
const exploreForm = ref({
  runner_id: '',
  decision_prompt_id: undefined as number | undefined,
  environment_id: null as number | null,
  session_profile_id: null as number | null,
  use_login_credentials: false,
  headless: false,
  start_url: '',
  max_steps: 20,
  permissions: ['PAGE_READ', 'SCREENSHOT_CAPTURE'] as WebExplorationPermission[],
})
const reconcilePromptId = ref<number | undefined>()
let loadSequence = 0
let pollTimer: number | undefined

const selectedPlan = computed(() => plans.value.find((item) => item.id === selectedPlanId.value) ?? null)
const selectedItem = computed(() => selectedPlan.value?.items.find((item) => item.id === selectedItemId.value) ?? null)
const selectedPlanItems = computed(() => selectedPlan.value?.items ?? [])
const {
  items: pagedPlanItems,
  total: planItemTotal,
  page: planItemPage,
  pageSize: planItemPageSize,
  changePage: changePlanItemPage,
  changePageSize: changePlanItemPageSize,
} = useClientPagination(selectedPlanItems, 5)
const planItemPageSizes = [5, 10, 20, 50] as const
const selectableApiIds = computed(() => apiDefinitions.value.map((item) => item.id))
const allApisSelected = computed(() => (
  selectableApiIds.value.length > 0
  && selectableApiIds.value.every((id) => planForm.value.api_definition_ids.includes(id))
))
const someApisSelected = computed(() => (
  !allApisSelected.value
  && selectableApiIds.value.some((id) => planForm.value.api_definition_ids.includes(id))
))
const activeRunners = computed(() => runners.value.filter((runner) => (
  runner.status === 'ACTIVE'
  && runner.online_status === 'ONLINE'
  && runner.redis_available
  && runner.capabilities.some((capability) => capability.name === 'WEB' && capability.status === 'READY')
  && runner.slots.some((slot) => slot.type === 'WEB' && slot.available > 0)
)))
const selectableProfiles = computed(() => props.sessionProfiles.filter((profile) => (
  profile.status === 'ACTIVE'
  && (!profile.expires_at || apiDateTimeMs(profile.expires_at) > Date.now())
  && (profile.environment_id === null || profile.environment_id === exploreForm.value.environment_id)
)))
const latestExploration = computed(() => explorations.value[0] ?? null)
const latestRevision = computed(() => revisions.value[0] ?? null)
const selectedExploreRunner = computed(() => activeRunners.value.find(
  (runner) => runner.id === exploreForm.value.runner_id,
) ?? null)
const selectedExplorePrompt = computed(() => decisionPrompts.value.find(
  (prompt) => prompt.id === exploreForm.value.decision_prompt_id,
) ?? null)
const selectedExploreEnvironment = computed(() => props.environments.find(
  (environment) => environment.id === exploreForm.value.environment_id,
) ?? null)
const tracedExploration = computed(() => planExplorations.value.find(
  (item) => item.id === tracedExplorationId.value,
) ?? explorations.value.find((item) => item.id === tracedExplorationId.value) ?? null)
const permissionOptions: Array<{
  value: WebExplorationPermission; label: string; description: string
}> = [
  { value: 'PAGE_READ', label: '读取页面', description: '读取可访问性快照、URL 和网络摘要（必选）' },
  { value: 'MANAGED_LOGIN', label: '受控登录', description: '引用项目已保存的测试账号，由 Runner 本地注入' },
  { value: 'FORM_SUBMIT', label: '提交表单', description: '允许点击提交、保存或确认类操作' },
  { value: 'TEST_DATA_CREATE', label: '创建测试数据', description: '允许新增业务测试数据；不包含付款、权限修改和删除' },
  { value: 'DIRECT_API_NAVIGATION', label: '直接访问 API', description: '特殊诊断能力；UI 探索默认不开启' },
  { value: 'SCREENSHOT_CAPTURE', label: '保存关键截图', description: '由 Runner 在成功、失败和关键业务检查点截图；AI 不能任意拍摄' },
]
const hasPollableWork = computed(() => (
  plans.value.some((item) => ['QUEUED', 'RUNNING'].includes(item.generation_status))
  || planExplorations.value.some((item) => (
    ['QUEUED', 'RUNNING', 'STOP_REQUESTED'].includes(item.status)
  ))
  || revisions.value.some((item) => ['QUEUED', 'RUNNING'].includes(item.generation_status))
))

function safeError(error: unknown, fallback: string): string {
  return getApiErrorMessage(error, fallback).trim() || fallback
}

function enabledStructured(prompts: PromptDefinition[]): PromptDefinition[] {
  return prompts.filter((prompt) => (
    prompt.enabled && prompt.current_version !== null
    && prompt.current_version.output_schema_id !== null
  ))
}

function statusType(status: string): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'SUCCEEDED' || status === 'COMPLETED' || status === 'ACCEPTED') return 'success'
  if (status === 'FAILED') return 'danger'
  if (status === 'RUNNING' || status === 'DRAFT' || status === 'STOP_REQUESTED') return 'warning'
  return 'info'
}

function explorationReason(exploration: WebExploration): string {
  const reason = exploration.result_summary?.reason
  return typeof reason === 'string' && reason.trim()
    ? reason.trim()
    : exploration.error_message ?? '—'
}

function explorationIsLimited(exploration: WebExploration): boolean {
  return exploration.status === 'COMPLETED'
    && /安全约束|安全限制|无安全动作|凭据.*(?:禁止|无法)|无法.*登录/.test(
      explorationReason(exploration),
    )
}

function explorationStatusLabel(exploration: WebExploration): string {
  if (explorationIsLimited(exploration)) return '受限结束'
  return exploration.status
}

function permissionLabel(value: string): string {
  return permissionOptions.find((item) => item.value === value)?.label ?? value
}

function openTrace(exploration: WebExploration): void {
  tracedExplorationId.value = exploration.id
  traceDialog.value = true
  void loadTraceEvidence()
}

function stepEvidence(sequence: number): WebExplorationEvidence[] {
  return tracedExploration.value?.evidence?.filter((item) => item.sequence === sequence) ?? []
}

function clearTraceEvidenceUrls(): void {
  Object.values(traceEvidenceUrls.value).forEach((url) => URL.revokeObjectURL(url))
  traceEvidenceUrls.value = {}
}

async function loadTraceEvidence(): Promise<void> {
  const evidence = tracedExploration.value?.evidence ?? []
  if (!traceDialog.value || evidence.length === 0) return
  traceEvidenceLoading.value = true
  try {
    for (const item of evidence) {
      if (traceEvidenceUrls.value[item.id]) continue
      try {
        const blob = await downloadWebExplorationEvidence(item.id)
        if (!traceDialog.value || tracedExplorationId.value !== item.exploration_id) continue
        traceEvidenceUrls.value = {
          ...traceEvidenceUrls.value,
          [item.id]: URL.createObjectURL(blob),
        }
      } catch {
        // The decision trace remains useful when one optional screenshot is unavailable.
      }
    }
  } finally {
    traceEvidenceLoading.value = false
  }
}

function openTraceEvidence(evidence: WebExplorationEvidence): void {
  const url = traceEvidenceUrls.value[evidence.id]
  if (url) window.open(url, '_blank', 'noopener,noreferrer')
}

function candidateNeedsLoginCredentials(item: WebPlanItem): boolean {
  const text = [item.name, item.objective, ...item.preconditions,
    ...item.planned_steps, ...item.expected_outcomes].join(' ')
  return /登录|登入|账号|用户名|密码|凭据|身份认证|login|log\s*in|sign\s*in|password|credential/i.test(text)
}

async function refreshExploreCredentialState(): Promise<void> {
  const projectId = props.projectId
  if (!exploreForm.value.use_login_credentials) {
    exploreCredentialState.value = 'not-needed'
    return
  }
  if (!projectId) {
    exploreCredentialState.value = 'error'
    return
  }
  exploreCredentialState.value = 'loading'
  try {
    const secrets = await getSecrets(projectId)
    const environmentId = exploreForm.value.environment_id
    const usableNames = new Set(secrets.filter((secret) => (
      secret.enabled
      && secret.secret_type === 'PASSWORD'
      && (secret.environment_id === null || secret.environment_id === environmentId)
    )).map((secret) => secret.name))
    exploreCredentialState.value = (
      usableNames.has('AI_TEST_USERNAME') && usableNames.has('AI_TEST_PASSWORD')
        ? 'ready'
        : 'missing'
    )
  } catch {
    exploreCredentialState.value = 'error'
  }
}

function syncManagedLoginPermission(): void {
  const selected = new Set(exploreForm.value.permissions)
  if (exploreForm.value.use_login_credentials) selected.add('MANAGED_LOGIN')
  else selected.delete('MANAGED_LOGIN')
  exploreForm.value.permissions = [...selected]
  void refreshExploreCredentialState()
}

function normalizePermissionDependencies(): void {
  const selected = new Set(exploreForm.value.permissions)
  selected.add('PAGE_READ')
  if (selected.has('TEST_DATA_CREATE')) selected.add('FORM_SUBMIT')
  exploreForm.value.permissions = [...selected]
  const shouldUseCredentials = selected.has('MANAGED_LOGIN')
  if (exploreForm.value.use_login_credentials !== shouldUseCredentials) {
    exploreForm.value.use_login_credentials = shouldUseCredentials
    void refreshExploreCredentialState()
  }
}

function dateLabel(value: string | null): string {
  return formatApiDateTime(value)
}

function resetSelection(): void {
  selectedPlanId.value = null
  selectedItemId.value = null
  explorations.value = []
  planExplorations.value = []
  revisions.value = []
  recordings.value = []
}

function selectionStorageKey(projectId: number): string {
  return `web-design-selection:${projectId}`
}

function readStoredSelection(projectId: number): { planId: number | null; itemId: number | null } {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(selectionStorageKey(projectId)) ?? '{}')
    return {
      planId: Number.isInteger(parsed.planId) ? parsed.planId : null,
      itemId: Number.isInteger(parsed.itemId) ? parsed.itemId : null,
    }
  } catch {
    return { planId: null, itemId: null }
  }
}

function persistSelection(): void {
  if (!props.projectId || !selectedPlanId.value) return
  sessionStorage.setItem(selectionStorageKey(props.projectId), JSON.stringify({
    planId: selectedPlanId.value,
    itemId: selectedItemId.value,
  }))
}

function toggleAllApis(): void {
  planForm.value.api_definition_ids = allApisSelected.value ? [] : [...selectableApiIds.value]
}

async function loadAll(quiet = false): Promise<void> {
  const projectId = props.projectId
  if (!projectId) return
  const sequence = ++loadSequence
  if (!quiet) loading.value = true
  if (!quiet) errorMessage.value = null
  try {
    const results = await Promise.allSettled([
      getWebPlans(projectId),
      getRequirementDocumentVersions(projectId),
      getApiDefinitions(projectId),
      getPrompts(false, 'WEB_TEST_PLAN'),
      getPrompts(false, 'WEB_EXPLORATION_DECISION'),
      getPrompts(false, 'WEB_PLAN_RECONCILE'),
      getRunners(),
    ] as const)
    if (sequence !== loadSequence || props.projectId !== projectId) return
    const [planResult, versionResult, apiResult, planRuleResult, decisionRuleResult,
      reconcileRuleResult, runnerResult] = results
    const failedLabels = results.flatMap((result, index) => (
      result.status === 'rejected'
        ? [['方案历史', '需求版本', 'API 定义', '规划 Prompt', '探索 Prompt', '校准 Prompt', 'Runner 状态'][index]]
        : []
    ))
    if (planResult.status === 'fulfilled') plans.value = planResult.value
    if (versionResult.status === 'fulfilled') requirementVersions.value = versionResult.value
    if (apiResult.status === 'fulfilled') apiDefinitions.value = apiResult.value
    if (planRuleResult.status === 'fulfilled') {
      planPrompts.value = enabledStructured(planRuleResult.value)
    }
    if (decisionRuleResult.status === 'fulfilled') {
      decisionPrompts.value = enabledStructured(decisionRuleResult.value)
    }
    if (reconcileRuleResult.status === 'fulfilled') {
      reconcilePrompts.value = enabledStructured(reconcileRuleResult.value)
    }
    if (runnerResult.status === 'fulfilled') runners.value = runnerResult.value.items
    if (!planForm.value.requirement_document_version_id) {
      planForm.value.requirement_document_version_id = requirementVersions.value[0]?.id
    }
    planForm.value.prompt_id ||= planPrompts.value[0]?.id
    exploreForm.value.decision_prompt_id ||= decisionPrompts.value[0]?.id
    reconcilePromptId.value ||= reconcilePrompts.value[0]?.id
    const stored = readStoredSelection(projectId)
    const preferredPlanId = selectedPlanId.value ?? stored.planId
    const preferredItemId = selectedItemId.value ?? stored.itemId
    const retained = plans.value.find((item) => item.id === preferredPlanId)
      ?? plans.value.find((item) => item.generation_status === 'SUCCEEDED')
      ?? plans.value[0]
    selectedPlanId.value = retained?.id ?? null
    let detailError: string | null = null
    if (retained?.generation_status === 'SUCCEEDED') {
      selectedItemId.value = retained.items.some((item) => item.id === preferredItemId)
        ? preferredItemId
        : retained.items[0]?.id ?? null
      const [loadedDetailError, loadedPlanError] = await Promise.all([
        loadItemDetails(sequence, true),
        loadPlanExplorations(retained.id, sequence, true),
      ])
      detailError = loadedDetailError ?? loadedPlanError
      persistSelection()
    } else {
      selectedItemId.value = null
      explorations.value = []
      planExplorations.value = []
      revisions.value = []
    }
    if (sequence !== loadSequence || props.projectId !== projectId) return
    if (failedLabels.length || detailError) {
      if (!quiet) {
        const failed = [...failedLabels, ...(detailError ? ['候选详情'] : [])]
        errorMessage.value = `部分数据加载失败：${failed.join('、')}。其他内容已保留，请刷新重试。`
      }
    } else {
      errorMessage.value = null
    }
  } catch (error) {
    if (sequence === loadSequence && !quiet) {
      errorMessage.value = safeError(error, 'Web 测试设计加载失败')
    }
  } finally {
    if (sequence === loadSequence && !quiet) loading.value = false
    schedulePolling()
  }
}

async function loadPlanExplorations(
  planId: number,
  sequence = loadSequence,
  quiet = false,
): Promise<string | null> {
  try {
    const loaded = await getWebPlanExplorations(planId)
    if (sequence !== loadSequence || selectedPlanId.value !== planId) return null
    planExplorations.value = loaded
    return null
  } catch (error) {
    const message = safeError(error, '方案探索历史加载失败')
    if (sequence === loadSequence && !quiet) errorMessage.value = message
    return message
  }
}

async function loadItemDetails(sequence = loadSequence, quiet = false): Promise<string | null> {
  const itemId = selectedItemId.value
  if (!itemId) {
    explorations.value = []
    revisions.value = []
    return null
  }
  try {
    const [loadedExplorations, loadedRevisions, loadedRecordings] = await Promise.all([
      getWebExplorations(itemId),
      getWebDesignRevisions(itemId),
      getWebRecordings(props.projectId as number, 1, 100, itemId),
    ])
    if (sequence !== loadSequence || selectedItemId.value !== itemId) return null
    explorations.value = loadedExplorations
    revisions.value = loadedRevisions
    recordings.value = loadedRecordings.items
    return null
  } catch (error) {
    const message = safeError(error, '方案条目状态加载失败')
    if (sequence === loadSequence && !quiet) errorMessage.value = message
    return message
  }
}

function schedulePolling(): void {
  if (pollTimer !== undefined) window.clearTimeout(pollTimer)
  pollTimer = undefined
  if (!hasPollableWork.value || !props.projectId) return
  pollTimer = window.setTimeout(() => void loadAll(true), 2500)
}

async function submitPlan(): Promise<void> {
  const projectId = props.projectId
  const form = planForm.value
  if (!projectId || !form.requirement_document_version_id || !form.prompt_id) {
    ElMessage.warning('请选择固定需求版本和 WEB_TEST_PLAN Prompt')
    return
  }
  creatingPlan.value = true
  try {
    const importIds = new Set(
      apiDefinitions.value
        .filter((item) => form.api_definition_ids.includes(item.id) && item.import_id !== null)
        .map((item) => item.import_id as number),
    )
    const created = await createWebPlan({
      project_id: projectId,
      prompt_id: form.prompt_id,
      requirement_document_version_id: form.requirement_document_version_id,
      api_import_id: importIds.size === 1 ? [...importIds][0] : undefined,
      api_definition_ids: form.api_definition_ids,
      additional_instructions: form.additional_instructions.trim() || undefined,
    })
    selectedPlanId.value = created.id
    ElMessage.success(created.reused ? '已复用相同的生成任务' : 'Web 测试方案已开始生成')
    await loadAll(true)
  } catch (error) {
    ElMessage.error(safeError(error, 'Web 测试方案创建失败'))
  } finally {
    creatingPlan.value = false
  }
}

async function choosePlan(plan: WebPlan): Promise<void> {
  selectedPlanId.value = plan.id
  selectedItemId.value = plan.generation_status === 'SUCCEEDED' ? plan.items[0]?.id ?? null : null
  persistSelection()
  await Promise.all([loadItemDetails(), loadPlanExplorations(plan.id)])
}

async function chooseItem(item: WebPlanItem): Promise<void> {
  selectedItemId.value = item.id
  persistSelection()
  await loadItemDetails()
}

function openExplore(item: WebPlanItem): void {
  selectedItemId.value = item.id
  persistSelection()
  const defaultEnvironment = props.environments.find((environment) => (
    environment.enabled && environment.is_default
  )) ?? props.environments.find((environment) => environment.enabled)
  const environmentBaseUrl = defaultEnvironment?.base_url?.trim() ?? ''
  const hint = item.start_url_hint?.trim() ?? ''
  let resolvedStartUrl = hint || environmentBaseUrl
  if (hint.startsWith('/') && environmentBaseUrl) {
    try {
      resolvedStartUrl = new URL(hint, environmentBaseUrl).toString()
    } catch {
      resolvedStartUrl = environmentBaseUrl
    }
  }
  exploreForm.value = {
    runner_id: activeRunners.value[0]?.id ?? '',
    decision_prompt_id: decisionPrompts.value[0]?.id,
    environment_id: defaultEnvironment?.id ?? null,
    session_profile_id: null,
    use_login_credentials: candidateNeedsLoginCredentials(item),
    headless: false,
    start_url: resolvedStartUrl,
    max_steps: 20,
    permissions: [
      'PAGE_READ',
      'SCREENSHOT_CAPTURE',
      ...(candidateNeedsLoginCredentials(item) ? ['MANAGED_LOGIN' as const] : []),
    ],
  }
  exploreAdvancedSections.value = (
    exploreForm.value.runner_id && exploreForm.value.decision_prompt_id
      ? []
      : ['advanced']
  )
  exploreDialog.value = true
  void refreshExploreCredentialState()
}

async function submitExploration(): Promise<void> {
  const item = selectedItem.value
  const form = exploreForm.value
  if (!item || !form.runner_id || !form.decision_prompt_id) {
    exploreAdvancedSections.value = ['advanced']
    ElMessage.warning('当前没有可自动使用的 Web Runner 或探索决策规则，请在高级设置中检查')
    return
  }
  if (form.use_login_credentials && exploreCredentialState.value !== 'ready') {
    exploreAdvancedSections.value = ['advanced']
    ElMessage.warning('请先在项目设置 → 密钥管理中配置 AI 测试登录账号')
    return
  }
  normalizePermissionDependencies()
  let origin: string
  try {
    const url = new URL(form.start_url)
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.hash) throw new Error()
    origin = url.origin
  } catch {
    ElMessage.warning('起始 URL 必须是安全的 http(s) 地址')
    return
  }
  creatingExploration.value = true
  try {
    const created = await createWebExploration(item.id, {
      runner_id: form.runner_id,
      decision_prompt_id: form.decision_prompt_id,
      environment_id: form.environment_id,
      session_profile_id: form.session_profile_id,
      use_login_credentials: form.use_login_credentials,
      headless: form.headless,
      start_url: form.start_url.trim(),
      allowed_origins: [origin],
      permissions: form.permissions,
      max_steps: form.max_steps,
    })
    await dispatchWebExploration(created.id)
    exploreDialog.value = false
    tracedExplorationId.value = created.id
    traceDialog.value = true
    ElMessage.success('MCP 探索已创建并投递')
    await Promise.all([
      loadItemDetails(),
      selectedPlanId.value ? loadPlanExplorations(selectedPlanId.value) : Promise.resolve(null),
    ])
    schedulePolling()
  } catch (error) {
    ElMessage.error(safeError(error, 'MCP 探索创建或投递失败'))
  } finally {
    creatingExploration.value = false
  }
}

async function requestStop(exploration: WebExploration): Promise<void> {
  try {
    await stopWebExploration(exploration.id)
    await Promise.all([
      loadItemDetails(),
      selectedPlanId.value ? loadPlanExplorations(selectedPlanId.value) : Promise.resolve(null),
    ])
  } catch (error) {
    ElMessage.error(safeError(error, '停止探索失败'))
  }
}

async function reconcile(exploration: WebExploration): Promise<void> {
  const item = selectedPlan.value?.items.find(
    (candidate) => candidate.id === exploration.plan_item_id,
  ) ?? null
  if (!item || !reconcilePromptId.value) {
    ElMessage.warning('请选择 WEB_PLAN_RECONCILE Prompt')
    return
  }
  if (selectedItemId.value !== item.id) {
    selectedItemId.value = item.id
    persistSelection()
  }
  creatingRevision.value = true
  try {
    await createWebDesignRevision(item.id, {
      prompt_id: reconcilePromptId.value,
      exploration_id: exploration.id,
    })
    ElMessage.success('已开始用探索事实校准方案')
    await loadItemDetails()
    schedulePolling()
  } catch (error) {
    ElMessage.error(safeError(error, '校准任务创建失败'))
  } finally {
    creatingRevision.value = false
  }
}

async function focusExploration(exploration: WebExploration): Promise<void> {
  const item = selectedPlan.value?.items.find(
    (candidate) => candidate.id === exploration.plan_item_id,
  )
  if (!item) return
  selectedItemId.value = item.id
  persistSelection()
  const index = selectedPlanItems.value.findIndex((candidate) => candidate.id === item.id)
  if (index >= 0) changePlanItemPage(Math.floor(index / planItemPageSize.value) + 1)
  await loadItemDetails()
}

async function reconcileRecording(recording: WebRecordingListItem): Promise<void> {
  const item = selectedItem.value
  if (!item || !reconcilePromptId.value) {
    ElMessage.warning('请选择 WEB_PLAN_RECONCILE Prompt')
    return
  }
  creatingRevision.value = true
  try {
    await createWebDesignRevision(item.id, {
      prompt_id: reconcilePromptId.value,
      recording_id: recording.id,
    })
    ElMessage.success('已开始用人工录制事实校准方案')
    await loadItemDetails()
    schedulePolling()
  } catch (error) {
    ElMessage.error(safeError(error, '录制校准任务创建失败'))
  } finally {
    creatingRevision.value = false
  }
}

async function decideRevision(revision: WebDesignRevision, action: 'ACCEPT' | 'REJECT'): Promise<void> {
  if (action === 'REJECT') {
    try {
      await decideWebDesignRevision(revision.id, { action })
      ElMessage.success('已拒绝该校准草稿')
      await loadItemDetails()
    } catch (error) {
      ElMessage.error(safeError(error, '拒绝失败'))
    }
    return
  }
  const suggested = revision.structured_result?.suggested_name ?? selectedItem.value?.name ?? ''
  try {
    const name = await ElMessageBox.prompt(
      '确认后只会创建 DRAFT Web Case，不会自动批准。',
      '接受校准结果',
      { inputValue: suggested, inputPattern: /^.{2,255}$/, inputErrorMessage: '名称需为 2～255 字符' },
    )
    await decideWebDesignRevision(revision.id, { action: 'ACCEPT', name: name.value })
    ElMessage.success('已创建 Web Case 草稿，请检查后人工批准')
    await loadItemDetails()
    emit('refresh-assets')
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(safeError(error, '接受校准结果失败'))
  }
}

watch(() => props.projectId, () => {
  loadSequence += 1
  resetSelection()
  planForm.value.requirement_document_version_id = undefined
  planForm.value.api_definition_ids = []
  if (pollTimer !== undefined) window.clearTimeout(pollTimer)
  void loadAll()
}, { immediate: true })

watch(selectedPlanId, () => {
  changePlanItemPage(1)
})

watch(() => exploreForm.value.environment_id, () => {
  if (!selectableProfiles.value.some((item) => item.id === exploreForm.value.session_profile_id)) {
    exploreForm.value.session_profile_id = null
  }
  if (exploreDialog.value && exploreForm.value.use_login_credentials) {
    void refreshExploreCredentialState()
  }
})

watch(traceDialog, (open) => {
  if (open) void loadTraceEvidence()
  else clearTraceEvidenceUrls()
})

watch(
  () => (tracedExploration.value?.evidence ?? []).map((item) => item.id).join(','),
  () => void loadTraceEvidence(),
)

onBeforeUnmount(() => {
  loadSequence += 1
  if (pollTimer !== undefined) window.clearTimeout(pollTimer)
  clearTraceEvidenceUrls()
})
</script>

<template>
  <div class="design-workbench" v-loading="loading">
    <el-alert
      title="先规划意图，再用人工录制或 MCP 获取页面事实"
      description="AI 规划不会猜 locator；MCP 探索只调用白名单浏览工具。校准结果始终是待人工确认的草稿。"
      type="info" :closable="false" show-icon
    />
    <el-alert v-if="errorMessage" :title="errorMessage" type="error" :closable="false" show-icon />

    <section class="design-panel">
      <div class="panel-heading"><div><h3>1. 从需求与 API 生成方案</h3><p>需求文档版本和 API 合约会作为不可变来源快照保存。</p></div><el-button :icon="Refresh" @click="loadAll()">刷新</el-button></div>
      <el-form label-position="top" class="plan-form">
        <el-form-item label="固定需求版本"><el-select v-model="planForm.requirement_document_version_id" class="full"><el-option v-for="version in requirementVersions" :key="version.id" :value="version.id" :label="`V${version.version_no} · ${dateLabel(version.created_at)}`" /></el-select></el-form-item>
        <el-form-item label="规划 Prompt"><el-select v-model="planForm.prompt_id" class="full" placeholder="WEB_TEST_PLAN"><el-option v-for="prompt in planPrompts" :key="prompt.id" :value="prompt.id" :label="promptOptionLabel(prompt)" /></el-select></el-form-item>
        <el-form-item label="相关 API（可多选，可留空）">
          <div class="api-selection">
            <el-select v-model="planForm.api_definition_ids" multiple collapse-tags collapse-tags-tooltip filterable class="full" placeholder="请选择">
              <template #header>
                <el-checkbox
                  :model-value="allApisSelected"
                  :indeterminate="someApisSelected"
                  :disabled="apiDefinitions.length === 0"
                  @change="toggleAllApis"
                >
                  全选当前项目 API（{{ apiDefinitions.length }}）
                </el-checkbox>
              </template>
              <el-option v-for="api in apiDefinitions" :key="api.id" :value="api.id" :label="`${api.method} ${api.path}`" />
            </el-select>
            <small>已选 {{ planForm.api_definition_ids.length }} 个；接口较多时建议优先选页面实际依赖的登录、查询和提交接口。</small>
          </div>
        </el-form-item>
        <el-form-item label="补充说明"><el-input v-model="planForm.additional_instructions" type="textarea" :rows="2" maxlength="5000" show-word-limit /></el-form-item>
      </el-form>
      <el-button type="primary" :loading="creatingPlan" :disabled="!canWrite" @click="submitPlan">AI 生成抽象方案</el-button>
      <el-text v-if="!planPrompts.length" type="warning">请先创建带结构化输出 Schema 的 WEB_TEST_PLAN Prompt，并配置模型绑定。</el-text>
    </section>

    <div class="design-columns">
      <section class="design-panel plan-history">
        <h3>方案历史</h3>
        <button v-for="plan in plans" :key="plan.id" class="plan-choice" :class="{ active: plan.id === selectedPlanId }" @click="choosePlan(plan)">
          <span>#{{ plan.id }} · 需求 V{{ plan.requirement_document_version_no }}</span>
          <el-tag size="small" :type="statusType(plan.generation_status)">{{ plan.generation_status }}</el-tag>
          <small>{{ plan.summary ?? plan.error_message ?? dateLabel(plan.created_at) }}</small>
        </button>
        <el-empty v-if="!plans.length" description="尚未生成方案" :image-size="56" />
      </section>

      <section class="design-panel plan-items">
        <div class="panel-heading"><div><h3>2. 候选用例</h3><p>{{ selectedPlan?.summary ?? '选择生成成功的方案' }}</p></div></div>
        <el-alert
          v-if="selectedPlan?.generation_status === 'FAILED'"
          class="plan-failure"
          title="该方案未生成候选用例"
          :description="selectedPlan.error_message ?? '模型输出未通过校验，请重新生成'"
          type="error"
          :closable="false"
          show-icon
        />
        <article v-for="item in pagedPlanItems" :key="item.id" class="item-card" :class="{ active: item.id === selectedItemId }" @click="chooseItem(item)">
          <div class="item-title"><strong>{{ item.name }}</strong><span><el-tag size="small">{{ item.priority }}</el-tag><el-tag size="small" type="info">{{ item.category }}</el-tag></span></div>
          <p>{{ item.objective }}</p>
          <ol><li v-for="step in item.planned_steps" :key="step">{{ step }}</li></ol>
          <div class="item-actions">
            <el-button size="small" :disabled="!canWrite" @click.stop="emit('manual-record', item)">人工录制</el-button>
            <el-button size="small" type="primary" :disabled="!canWrite || !decisionPrompts.length" @click.stop="openExplore(item)">MCP 自动探索</el-button>
          </div>
        </article>
        <el-empty v-if="selectedPlan?.generation_status === 'SUCCEEDED' && !selectedPlan.items.length" description="方案没有候选用例" :image-size="56" />
        <el-pagination
          v-if="planItemTotal"
          class="records-pagination"
          background
          layout="total, sizes, prev, pager, next"
          :current-page="planItemPage"
          :page-size="planItemPageSize"
          :page-sizes="[...planItemPageSizes]"
          :total="planItemTotal"
          @current-change="changePlanItemPage"
          @size-change="changePlanItemPageSize"
        />
      </section>
    </div>

    <section v-if="selectedItem" class="design-panel">
      <div class="panel-heading"><div><h3>3. 探索与校准</h3><p>当前：{{ selectedItem.name }}</p></div><el-select v-model="reconcilePromptId" placeholder="WEB_PLAN_RECONCILE Prompt" style="width: 300px"><el-option v-for="prompt in reconcilePrompts" :key="prompt.id" :value="prompt.id" :label="promptOptionLabel(prompt)" /></el-select></div>
      <p class="section-hint">下方展示当前方案所有候选用例的探索记录；点击候选名称可定位到它的录制与校准草稿。</p>
      <el-table :data="planExplorations" size="small" table-layout="fixed">
        <el-table-column label="候选用例" min-width="220"><template #default="{ row }"><el-button link type="primary" @click="focusExploration(row)">{{ row.plan_item_name ?? `#${row.plan_item_id}` }}</el-button><el-tag v-if="row.plan_item_id === selectedItemId" size="small" type="success">当前</el-tag></template></el-table-column>
        <el-table-column label="探索" min-width="190"><template #default="{ row }"><span>{{ row.id.slice(0, 12) }}…</span><el-tag class="exploration-mode-tag" size="small" :type="row.headless ? 'info' : 'success'">{{ row.headless ? '后台' : '可视' }}</el-tag><small class="block">{{ dateLabel(row.created_at) }}</small></template></el-table-column>
        <el-table-column label="状态" width="130"><template #default="{ row }"><el-tag size="small" :type="explorationIsLimited(row) ? 'warning' : statusType(row.status)">{{ explorationStatusLabel(row) }}</el-tag></template></el-table-column>
        <el-table-column label="进度" width="110"><template #default="{ row }">{{ row.current_step }}/{{ row.max_steps }}</template></el-table-column>
        <el-table-column label="结果/错误" min-width="300"><template #default="{ row }">{{ explorationReason(row) }}</template></el-table-column>
        <el-table-column label="操作" width="270"><template #default="{ row }"><el-button link @click="openTrace(row)">查看过程</el-button><el-button v-if="['QUEUED','RUNNING'].includes(row.status)" link type="warning" @click="requestStop(row)">停止</el-button><el-button v-if="row.status === 'COMPLETED' && !explorationIsLimited(row)" link type="primary" :loading="creatingRevision" @click="reconcile(row)">生成校准草稿</el-button><span v-else-if="explorationIsLimited(row)" class="warning">请重新探索</span></template></el-table-column>
      </el-table>
      <el-empty v-if="!planExplorations.length" description="当前方案尚无 MCP 探索；也可进入录制工作台完成绑定录制" :image-size="56" />

      <h4>绑定的人工录制</h4>
      <el-table :data="recordings" size="small" table-layout="fixed">
        <el-table-column label="录制" min-width="170"><template #default="{ row }"><span>{{ row.id.slice(0, 12) }}…</span><small class="block">{{ dateLabel(row.created_at) }}</small></template></el-table-column>
        <el-table-column label="状态" width="130"><template #default="{ row }"><el-tag size="small" :type="statusType(row.status)">{{ row.status }}</el-tag></template></el-table-column>
        <el-table-column label="事件" prop="event_count" width="90" />
        <el-table-column label="操作" width="160"><template #default="{ row }"><el-button v-if="row.status === 'COMPLETED'" link type="primary" :loading="creatingRevision" @click="reconcileRecording(row)">生成校准草稿</el-button></template></el-table-column>
      </el-table>
      <el-empty v-if="!recordings.length" description="尚无绑定该方案条目的人工录制" :image-size="50" />

      <h4>校准草稿</h4>
      <el-table :data="revisions" size="small" table-layout="fixed">
        <el-table-column label="版本" width="90"><template #default="{ row }">#{{ row.id }}</template></el-table-column>
        <el-table-column label="生成" width="110"><template #default="{ row }"><el-tag size="small" :type="statusType(row.generation_status)">{{ row.generation_status }}</el-tag></template></el-table-column>
        <el-table-column label="审核" width="110"><template #default="{ row }"><el-tag size="small" :type="statusType(row.status)">{{ row.status }}</el-tag></template></el-table-column>
        <el-table-column label="摘要" min-width="320"><template #default="{ row }">
          {{ row.structured_result?.summary ?? row.error_message ?? '生成中' }}
          <template v-if="row.structured_result?.unresolved_gaps.length">
            <small class="block warning">仍有 {{ row.structured_result.unresolved_gaps.length }} 个未确认缺口：</small>
            <ol class="revision-gaps"><li v-for="gap in row.structured_result.unresolved_gaps" :key="gap">{{ gap }}</li></ol>
          </template>
        </template></el-table-column>
        <el-table-column label="操作" width="150"><template #default="{ row }"><template v-if="row.generation_status === 'SUCCEEDED' && row.status === 'DRAFT'"><el-button link type="primary" @click="decideRevision(row, 'ACCEPT')">接受为草稿</el-button><el-button link @click="decideRevision(row, 'REJECT')">拒绝</el-button></template><span v-else-if="row.created_web_case_id">Web Case #{{ row.created_web_case_id }}</span></template></el-table-column>
      </el-table>
      <el-empty v-if="!revisions.length" description="尚无校准草稿" :image-size="50" />
    </section>

    <el-dialog v-model="exploreDialog" title="创建 Playwright MCP 自动探索" width="650px">
      <el-alert title="安全边界" description="仅允许当前起始站点 origin；禁止任意脚本、文件上传、Cookie/Storage 枚举及高风险业务动作。" type="warning" :closable="false" show-icon />
      <el-form label-position="top" class="dialog-form">
        <el-form-item label="从哪个页面开始探索">
          <el-input v-model="exploreForm.start_url" maxlength="2048" placeholder="优先自动使用方案地址或项目默认环境 Base URL" />
          <small class="explore-field-hint">这是唯一通常需要确认的内容；页面 origin 会自动成为本次探索边界。</small>
        </el-form-item>
        <el-form-item label="探索显示方式">
          <el-switch
            v-model="exploreForm.headless"
            :active-value="false"
            :inactive-value="true"
            active-text="可视化探索"
            inactive-text="后台无界面"
          />
          <small class="explore-field-hint">可视化模式会在所选 Runner 所在电脑打开 Chrome 窗口；窗口仍由 MCP 自动操作，请勿手动点击或输入。</small>
        </el-form-item>
        <el-form-item label="本次 MCP 权限">
          <el-checkbox-group v-model="exploreForm.permissions" class="permission-list" @change="normalizePermissionDependencies">
            <el-checkbox
              v-for="permission in permissionOptions"
              :key="permission.value"
              :value="permission.value"
              :disabled="permission.value === 'PAGE_READ'"
            >
              <span class="permission-copy"><strong>{{ permission.label }}</strong><small>{{ permission.description }}</small></span>
            </el-checkbox>
          </el-checkbox-group>
          <small class="explore-field-hint">权限按本次任务固化；Runner 拒绝时会显示缺少的具体权限。高风险的付款、权限修改、文件上传和删除始终禁止。</small>
        </el-form-item>
        <div class="explore-auto-context">
          <div>
            <strong>其余配置已自动准备</strong>
            <span>
              {{ selectedExploreRunner?.name ?? '暂无可用 Web Runner' }} ·
              {{ selectedExplorePrompt ? promptOptionLabel(selectedExplorePrompt) : '暂无探索决策规则' }} ·
              {{ selectedExploreEnvironment?.name ?? '不绑定环境' }} · 最多 {{ exploreForm.max_steps }} 步 ·
              {{ exploreForm.headless ? '后台无界面运行' : '在 Runner 电脑打开可视 Chrome' }} ·
              {{ exploreForm.use_login_credentials
                ? (exploreCredentialState === 'ready' ? '登录凭据可安全注入' : '登录凭据待配置')
                : '不使用真实登录凭据' }}
            </span>
          </div>
          <el-tag v-if="selectedExploreRunner && selectedExplorePrompt" type="success">可以直接投递</el-tag>
          <el-tag v-else type="danger">需要补充配置</el-tag>
        </div>
        <el-collapse v-model="exploreAdvancedSections" class="explore-advanced">
          <el-collapse-item name="advanced" title="高级设置（通常无需修改）">
            <el-form-item label="执行 Runner"><el-select v-model="exploreForm.runner_id" class="full"><el-option v-for="runner in activeRunners" :key="runner.id" :value="runner.id" :label="runner.name" /></el-select></el-form-item>
            <el-form-item label="探索决策 Prompt"><el-select v-model="exploreForm.decision_prompt_id" class="full"><el-option v-for="prompt in decisionPrompts" :key="prompt.id" :value="prompt.id" :label="promptOptionLabel(prompt)" /></el-select></el-form-item>
            <el-form-item label="运行环境（可选）"><el-select v-model="exploreForm.environment_id" clearable class="full"><el-option v-for="environment in environments" :key="environment.id" :value="environment.id" :label="environment.name" /></el-select></el-form-item>
            <el-form-item label="登录会话（可选）"><el-select v-model="exploreForm.session_profile_id" clearable class="full"><el-option v-for="profile in selectableProfiles" :key="profile.id" :value="profile.id" :label="profile.name" /></el-select></el-form-item>
            <el-form-item label="项目测试登录凭据">
              <el-switch v-model="exploreForm.use_login_credentials" active-text="允许 Runner 安全注入" @change="syncManagedLoginPermission" />
              <small class="explore-field-hint">只下发 AI_TEST_USERNAME / AI_TEST_PASSWORD 给本次 Runner；AI、日志和校准结果只能看到脱敏引用。</small>
              <el-alert v-if="exploreForm.use_login_credentials && exploreCredentialState === 'missing'" title="尚未配置测试登录账号，请先前往项目设置 → 密钥管理配置" type="warning" :closable="false" show-icon />
              <el-alert v-else-if="exploreForm.use_login_credentials && exploreCredentialState === 'error'" title="暂时无法检查登录凭据，请刷新后重试" type="error" :closable="false" show-icon />
            </el-form-item>
            <el-form-item label="最大探索步骤"><el-input-number v-model="exploreForm.max_steps" :min="1" :max="50" /></el-form-item>
          </el-collapse-item>
        </el-collapse>
      </el-form>
      <template #footer><el-button @click="exploreDialog = false">取消</el-button><el-button type="primary" :icon="VideoPlay" :loading="creatingExploration" @click="submitExploration">创建并投递</el-button></template>
    </el-dialog>

    <el-dialog v-model="traceDialog" title="MCP 探索过程" width="820px" top="8vh" class="mcp-trace-dialog">
      <div class="trace-dialog-body" v-loading="traceEvidenceLoading">
        <template v-if="tracedExploration">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="状态">{{ explorationStatusLabel(tracedExploration) }}</el-descriptions-item>
          <el-descriptions-item label="进度">{{ tracedExploration.current_step }}/{{ tracedExploration.max_steps }}</el-descriptions-item>
          <el-descriptions-item label="用户授权" :span="2">
            <el-tag v-for="permission in (tracedExploration.permissions ?? ['PAGE_READ'])" :key="permission" size="small" class="trace-tag">{{ permissionLabel(permission) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item v-if="tracedExploration.error_message" label="结果/错误" :span="2">
            <span class="danger">{{ tracedExploration.error_message }}</span>
            <el-tag v-if="tracedExploration.result_summary?.required_capability" type="danger" size="small" class="trace-tag">
              需开通 {{ permissionLabel(String(tracedExploration.result_summary.required_capability)) }}
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>
        <el-timeline v-if="tracedExploration.steps?.length" class="trace-timeline">
          <el-timeline-item v-for="step in (tracedExploration.steps ?? [])" :key="step.sequence" :timestamp="`第 ${step.sequence} 步 · ${step.status}`" placement="top">
            <div class="trace-step">
              <strong>{{ step.action ?? '等待决策' }}<template v-if="step.element"> · {{ step.element }}</template></strong>
              <small>{{ step.page_title || step.page_url || '页面观察已记录' }}</small>
              <p><b>决策依据：</b>{{ step.reason || '—' }}</p>
              <p v-if="step.expected_observation"><b>期待反馈：</b>{{ step.expected_observation }}</p>
              <p v-if="step.result"><b>执行反馈：</b>{{ step.result }}</p>
              <div v-if="stepEvidence(step.sequence).length" class="trace-evidence-grid">
                <button
                  v-for="evidence in stepEvidence(step.sequence)"
                  :key="evidence.id"
                  type="button"
                  class="trace-evidence"
                  :disabled="!traceEvidenceUrls[evidence.id]"
                  @click="openTraceEvidence(evidence)"
                >
                  <img v-if="traceEvidenceUrls[evidence.id]" :src="traceEvidenceUrls[evidence.id]" :alt="evidence.label" />
                  <span v-else>截图加载中</span>
                  <small>{{ evidence.label }} · {{ evidence.kind }}</small>
                </button>
              </div>
            </div>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="Runner 尚未回传第一步页面观察" :image-size="50" />
        </template>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.design-workbench { display: grid; gap: 16px; }
.design-panel { padding: 18px; border: 1px solid var(--el-border-color-light); border-radius: 12px; background: var(--el-bg-color); }
.panel-heading, .item-title { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.panel-heading h3, .design-panel h3, .design-panel h4 { margin: 0 0 6px; }
.panel-heading p, .item-card p { margin: 0; color: var(--el-text-color-secondary); }
.plan-form { display: grid; grid-template-columns: 1fr 1fr; gap: 0 16px; margin-top: 14px; }
.plan-form .el-form-item:nth-child(3), .plan-form .el-form-item:nth-child(4) { grid-column: 1 / -1; }
.full { width: 100%; }
.api-selection { display: grid; width: 100%; gap: 5px; }
.api-selection small { color: var(--el-text-color-secondary); }
.design-columns { display: grid; grid-template-columns: minmax(260px, 0.7fr) minmax(420px, 1.5fr); gap: 16px; }
.plan-choice { width: 100%; display: grid; grid-template-columns: 1fr auto; gap: 6px; padding: 12px; margin-top: 8px; border: 1px solid var(--el-border-color); border-radius: 10px; background: transparent; color: inherit; text-align: left; cursor: pointer; }
.plan-choice.active, .item-card.active { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.plan-choice small { grid-column: 1 / -1; color: var(--el-text-color-secondary); }
.item-card { border: 1px solid var(--el-border-color-light); border-radius: 10px; padding: 14px; margin-top: 10px; cursor: pointer; }
.plan-failure { margin-top: 14px; }
.item-card ol { margin: 10px 0; padding-left: 22px; }
.item-actions { display: flex; justify-content: flex-end; gap: 8px; }
.block { display: block; color: var(--el-text-color-secondary); }
.section-hint { margin: 4px 0 10px; color: var(--el-text-color-secondary); font-size: 13px; }
.section-hint + :deep(.el-table) .el-tag { margin-left: 6px; }
.exploration-mode-tag { margin-left: 8px; vertical-align: 1px; }
.warning { color: var(--el-color-warning); }
.revision-gaps { margin: 5px 0 0; padding-left: 20px; color: var(--el-color-warning-dark-2); font-size: 12px; line-height: 1.5; }
.dialog-form { margin-top: 16px; }
.explore-field-hint { display: block; margin-top: 6px; color: var(--el-text-color-secondary); line-height: 1.5; }
.permission-list { display: grid; width: 100%; gap: 8px; }
.permission-list :deep(.el-checkbox) { height: auto; margin-right: 0; align-items: flex-start; }
.permission-copy { display: inline-grid; gap: 2px; white-space: normal; }
.permission-copy small { color: var(--el-text-color-secondary); }
.trace-tag { margin-right: 6px; }
.trace-timeline { margin-top: 20px; }
.trace-dialog-body { height: min(62vh, 610px); overflow-y: auto; padding-right: 8px; }
:deep(.mcp-trace-dialog) { height: min(78vh, 760px); display: flex; flex-direction: column; margin-bottom: 0; }
:deep(.mcp-trace-dialog .el-dialog__body) { flex: 1; min-height: 0; overflow: hidden; padding-top: 12px; }
.trace-step { padding: 10px 12px; border: 1px solid var(--el-border-color-light); border-radius: 8px; }
.trace-step small { display: block; margin-top: 3px; color: var(--el-text-color-secondary); }
.trace-step p { margin: 8px 0 0; line-height: 1.55; word-break: break-word; }
.trace-evidence-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px; margin-top: 12px; }
.trace-evidence { display: grid; gap: 6px; padding: 8px; border: 1px solid var(--el-border-color); border-radius: 8px; background: var(--el-fill-color-lighter); color: inherit; text-align: left; cursor: zoom-in; }
.trace-evidence:disabled { cursor: wait; opacity: 0.72; }
.trace-evidence img { width: 100%; max-height: 150px; object-fit: contain; border-radius: 5px; background: #fff; }
.trace-evidence small { color: var(--el-text-color-secondary); line-height: 1.4; }
.danger { color: var(--el-color-danger); }
.explore-auto-context { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin: 4px 0 12px; padding: 12px 14px; border: 1px solid var(--el-color-success-light-7); border-radius: 9px; background: var(--el-color-success-light-9); }
.explore-auto-context strong, .explore-auto-context span { display: block; }
.explore-auto-context span { margin-top: 4px; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.5; }
.explore-advanced { border-top: 0; }
.explore-advanced :deep(.el-collapse-item__header) { height: 42px; color: var(--el-color-primary); font-weight: 600; }
.explore-advanced :deep(.el-collapse-item__content) { padding: 12px 2px 0; }
@media (max-width: 1000px) { .design-columns, .plan-form { grid-template-columns: 1fr; } .plan-form .el-form-item { grid-column: 1; } }
</style>
