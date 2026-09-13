<script setup lang="ts">
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Edit, Plus, Refresh, SortDown, SortUp } from '@element-plus/icons-vue'

import { getApiErrorMessage } from '@/api/http'
import { getModelBindings } from '@/api/model-center'
import { getPrompts } from '@/api/prompt-center'
import { getRunners } from '@/api/runners'
import { RECORD_PAGE_SIZES, useClientPagination } from '@/composables/useClientPagination'
import { apiDateTimeMs, formatApiDateTime } from '@/utils/datetime'
import { promptOptionLabel } from '@/utils/prompt-display'
import {
  cancelWebRecording,
  confirmWebRecording,
  createWebRecording,
  dispatchWebRecording,
  generateWebRecordingAiSuggestion,
  getWebRecording,
  getWebRecordingAiSuggestions,
  getWebRecordings,
  rejectWebRecordingAiSuggestion,
  stopWebRecording,
} from '@/api/web-recordings'
import type { Environment } from '@/types/environment'
import type { Runner } from '@/types/runner'
import type {
  SessionProfileResponse,
  WebAction,
  WebAssertion,
  WebCaseContent,
  WebCaseResponse,
  WebLocator,
  LocatorStrategy,
} from '@/types/web'
import type {
  WebRecordingCreateRequest,
  WebRecordingAiSuggestionResponse,
  WebRecordingDetailResponse,
  WebRecordingEvent,
  WebRecordingEventType,
  WebRecordingListItem,
  WebRecordingStatus,
} from '@/types/web-recording'
import type { PromptDefinition } from '@/types/prompt-center'
import type { ProjectModelBinding } from '@/types/model-center'
import {
  identityIsCurrent,
  isIdentityStorageEvent,
  readRequestIdentity,
  type RequestIdentity,
} from '@/utils/request-context'
import {
  WEB_ACTION_TYPES,
  WEB_TAB_ALIAS_HELP,
  hasWebLocator,
  makeWebAction,
  validWebTabAlias,
  validWebUrlOrTemplate,
  webActionSummary,
} from '@/utils/web-actions'
import {
  WEB_ASSERTION_TYPES,
  hasWebAssertionExpected,
  makeWebAssertion,
  validWebAssertionExpected,
  webAssertionExpectedPlaceholder,
  webAssertionSummary,
  webAssertionTypeLabel,
} from '@/utils/web-assertions'

const props = defineProps<{
  projectId?: number
  canWrite: boolean
  webCases: WebCaseResponse[]
  sessionProfiles: SessionProfileResponse[]
  environments: Environment[]
  environmentsLoading: boolean
  environmentsError?: string | null
  refreshKey?: number
  initialPlanItemId?: number | null
  initialStartUrl?: string
}>()

const emit = defineEmits<{ (event: 'refresh-assets'): void }>()

const eventTypes: WebRecordingEventType[] = ['NAVIGATE', 'CLICK', 'FILL', 'SELECT', 'PRESS']
const locatorStrategies: LocatorStrategy[] = ['css', 'xpath', 'text', 'role', 'label', 'placeholder', 'test_id']

const runners = ref<Runner[]>([])
const runnersLoading = ref(false)
const runnersError = ref<string | null>(null)
const recordings = ref<WebRecordingListItem[]>([])
const recordingsTotal = ref(0)
const recordingsPage = ref(1)
const recordingsPageSize = ref(10)
const recordingsLoading = ref(false)
const recordingsError = ref<string | null>(null)

const detailVisible = ref(false)
const recordingDetail = ref<WebRecordingDetailResponse | null>(null)
const detailLoading = ref(false)
const detailError = ref<string | null>(null)
const editedEvents = ref<WebRecordingEvent[]>([])
const recordingForm = ref({
  runner_id: '',
  plan_item_id: (props.initialPlanItemId ?? null) as number | null,
  environment_id: null as number | null,
  start_url: props.initialStartUrl ?? '',
  session_profile_id: null as number | null,
  save_session: false,
  save_session_name: '',
  save_session_expires_at: '',
})
const createLoading = ref(false)
const operationRecordingId = ref<string | null>(null)
const confirmVisible = ref(false)
const confirmSaving = ref(false)
const confirmTarget = ref<'new' | 'existing'>('new')
const confirmName = ref('')
const confirmWebCaseId = ref<number | undefined>()
const lastConfirmMessage = ref<string | null>(null)
const sourceEvents = ref<WebRecordingEvent[]>([])
const deterministicBaseline = ref<WebCaseContent | null>(null)
const aiPrompts = ref<PromptDefinition[]>([])
const aiBindings = ref<ProjectModelBinding[]>([])
const aiPromptLoading = ref(false)
const aiPromptError = ref<string | null>(null)
const aiBindingLoading = ref(false)
const aiBindingError = ref<string | null>(null)
const selectedAiPromptId = ref<number | undefined>()
const aiAdditionalInstructions = ref('')
const aiSuggestions = ref<WebRecordingAiSuggestionResponse[]>([])
const aiSuggestionPages = useClientPagination(aiSuggestions)
const selectedAiSuggestionId = ref<number | null>(null)
const aiSuggestionsLoading = ref(false)
const aiSuggestionsError = ref<string | null>(null)
const aiGenerating = ref(false)
const aiContentDraft = ref<WebCaseContent | null>(null)
const aiContentEdited = ref(false)
const aiAcceptVisible = ref(false)
const aiAcceptSaving = ref(false)
const aiAcceptTarget = ref<'new' | 'existing'>('new')
const aiAcceptName = ref('')
const aiAcceptWebCaseId = ref<number | undefined>()
const aiDecisionNote = ref('')
const aiRejectVisible = ref(false)
const aiRejectSaving = ref(false)
const aiRejectSuggestionId = ref<number | null>(null)
const aiRejectDecisionNote = ref('')

let projectSequence = 0
let detailSequence = 0
let runnerSequence = 0
let aiDecisionGeneration = 0
let nextAiDecisionOperationId = 0
let activeAiDecisionOperationId: number | null = null
let pollTimer: number | undefined
let pollInFlight = false
let pageActive = true

const activeWebCases = computed(() => props.webCases.filter((item) => item.status !== 'ARCHIVED'))
const selectableRunners = computed(() => runners.value.filter((runner) => canUseRunner(runner)))
const selectableProfiles = computed(() => props.sessionProfiles.filter((profile) => {
  if (profile.status !== 'ACTIVE') return false
  if (profile.expires_at && apiDateTimeMs(profile.expires_at) <= Date.now()) return false
  return profile.environment_id === null || profile.environment_id === recordingForm.value.environment_id
}))
const recordingIsPollable = computed(() => recordingDetail.value !== null && isPollableStatus(recordingDetail.value.status))
const currentRecordingOperation = computed(() => operationRecordingId.value === recordingDetail.value?.id)
const totalPages = computed(() => Math.max(1, Math.ceil(recordingsTotal.value / recordingsPageSize.value)))
const availableAiPrompts = computed(() => aiPrompts.value.filter((prompt) => (
  prompt.enabled
  && prompt.current_version_id !== null
  && prompt.current_version !== null
  && prompt.current_version.output_schema_id !== null
)))
const aiBinding = computed(() => aiBindings.value.find((binding) => binding.task_type === 'WEB_CASE_GENERATE') ?? null)
const selectedAiSuggestion = computed(() => (
  aiSuggestions.value.find((suggestion) => suggestion.id === selectedAiSuggestionId.value)
  ?? aiSuggestions.value.find((suggestion) => suggestion.status === 'DRAFT')
  ?? null
))
const hasDraftAiSuggestion = computed(() => aiSuggestions.value.some((suggestion) => suggestion.status === 'DRAFT'))
const aiContextReady = computed(() => availableAiPrompts.value.length > 0 && aiBinding.value !== null)
const aiLoading = computed(() => aiPromptLoading.value || aiBindingLoading.value || aiSuggestionsLoading.value)

function safeError(error: unknown, fallback: string): string {
  return getApiErrorMessage(error, fallback).trim() || fallback
}

function isAiSuggestionRequestTimeout(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false
  const code = (error as { code?: unknown }).code
  if (code === 'ECONNABORTED' || code === 'ETIMEDOUT') return true
  return error instanceof Error && /timeout/i.test(error.message)
}

function dateLabel(value: string | null | undefined): string {
  return formatApiDateTime(value)
}

function statusLabel(status: WebRecordingStatus): string {
  const labels: Record<WebRecordingStatus, string> = {
    CREATED: '已创建',
    QUEUED: '排队中',
    RUNNING: '录制中',
    STOP_REQUESTED: '等待停止',
    COMPLETED: '已完成',
    FAILED: '失败',
    CANCELLED: '已取消',
  }
  return labels[status]
}

function statusType(status: WebRecordingStatus): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'COMPLETED') return 'success'
  if (status === 'FAILED') return 'danger'
  if (status === 'CANCELLED') return 'info'
  if (status === 'RUNNING') return 'warning'
  return 'info'
}

function dispatchStatusLabel(status: WebRecordingListItem['dispatch_status']): string {
  return status === 'PUBLISHED' ? '已投递' : status === 'FAILED' ? '投递失败' : '投递中'
}

function eventTypeLabel(type: WebRecordingEventType): string {
  const labels: Record<WebRecordingEventType, string> = {
    NAVIGATE: '打开页面', CLICK: '点击', FILL: '填写', SELECT: '选择', PRESS: '按键',
  }
  return labels[type]
}

function aiStatusLabel(status: WebRecordingAiSuggestionResponse['status']): string {
  return status === 'DRAFT' ? '待人工审核' : status === 'ACCEPTED' ? '已接受' : '已拒绝'
}

function aiStatusType(status: WebRecordingAiSuggestionResponse['status']): 'success' | 'warning' | 'info' {
  return status === 'ACCEPTED' ? 'success' : status === 'DRAFT' ? 'warning' : 'info'
}

function shortHash(value: string): string {
  return value ? `${value.slice(0, 12)}…` : '—'
}

function byteSizeLabel(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(2)} MB`
}

function containsSensitiveText(value: string): boolean {
  return /(?:bearer\s+|password|passwd|token|access[_-]?token|secret|credential|authorization|cookie|api[_-]?key)\s*[:=]?/i.test(value)
}

function aiDiffSummary(suggestion: WebRecordingAiSuggestionResponse): string {
  const result = suggestion.structured_result
  if (!result) return '当前建议没有可展示的结构化结果'
  const baselineSteps = deterministicBaseline.value?.actions.length ?? 0
  return `操作/步骤 ${baselineSteps} → ${result.steps.length}，断言 ${deterministicBaseline.value?.assertions.length ?? 0} → ${result.assertions.length}；AI 候选可能新增命名、定位器或断言说明。`
}

function cloneContent(content: WebCaseContent): WebCaseContent {
  return JSON.parse(JSON.stringify(content)) as WebCaseContent
}

function cloneRecordingEvents(events: WebRecordingEvent[]): WebRecordingEvent[] {
  return events
    .filter((event) => eventTypes.includes(event.event_type))
    .sort((left, right) => left.sequence - right.sequence)
    .map((event) => ({
      ...event,
      locator_candidates: event.locator_candidates.map((candidate) => ({ ...candidate })),
    }))
}

function clearAiState(): void {
  aiPromptLoading.value = false
  aiBindingLoading.value = false
  aiSuggestionsLoading.value = false
  aiGenerating.value = false
  aiAcceptSaving.value = false
  aiRejectSaving.value = false
  aiPrompts.value = []
  aiBindings.value = []
  aiPromptError.value = null
  aiBindingError.value = null
  selectedAiPromptId.value = undefined
  aiAdditionalInstructions.value = ''
  aiSuggestions.value = []
  aiSuggestionsError.value = null
  selectedAiSuggestionId.value = null
  aiContentDraft.value = null
  aiContentEdited.value = false
  aiAcceptVisible.value = false
  aiRejectVisible.value = false
  aiRejectSuggestionId.value = null
  aiRejectDecisionNote.value = ''
}

function invalidateAiDecisionContext(): void {
  aiDecisionGeneration += 1
  activeAiDecisionOperationId = null
  aiAcceptSaving.value = false
  aiRejectSaving.value = false
}

function aiDecisionCurrent(
  operationId: number,
  generation: number,
  identity: RequestIdentity,
  recordingId: string,
  sequence: number,
  projectAtStart: number,
): boolean {
  return activeAiDecisionOperationId === operationId
    && aiDecisionGeneration === generation
    && identityIsCurrent(identity)
    && props.projectId === projectAtStart
    && isCurrentDetail(recordingId, sequence)
}

function finishAiDecision(operationId: number): void {
  if (activeAiDecisionOperationId !== operationId) return
  activeAiDecisionOperationId = null
  aiAcceptSaving.value = false
  aiRejectSaving.value = false
}

function isActiveStatus(status: WebRecordingStatus): boolean {
  return status === 'CREATED' || status === 'QUEUED' || status === 'RUNNING' || status === 'STOP_REQUESTED'
}

function isPollableStatus(status: WebRecordingStatus): boolean {
  return status === 'QUEUED' || status === 'RUNNING' || status === 'STOP_REQUESTED'
}

function canUseRunner(runner: Runner): boolean {
  const capability = runner.capabilities.find((item) => item.name === 'WEB')
  const slot = runner.slots.find((item) => item.type === 'WEB')
  return runner.status === 'ACTIVE'
    && runner.online_status === 'ONLINE'
    && runner.redis_available
    && capability?.status === 'READY'
    && (slot?.available ?? 0) >= 1
}

function webSlot(runner: Runner): number {
  return runner.slots.find((item) => item.type === 'WEB')?.available ?? 0
}

function runnerLabel(runner: Runner): string {
  return `${runner.name}（${runner.hostname} · WEB 可用 ${webSlot(runner)}）`
}

function clearPollTimer(): void {
  if (pollTimer !== undefined) {
    window.clearTimeout(pollTimer)
    pollTimer = undefined
  }
}

function stopPolling(): void {
  clearPollTimer()
}

function schedulePolling(): void {
  clearPollTimer()
  if (!pageActive || !detailVisible.value || !recordingIsPollable.value) return
  pollTimer = window.setTimeout(() => { void pollRecordingDetail(true) }, 5000)
}

async function pollRecordingDetail(silent: boolean): Promise<void> {
  const detail = recordingDetail.value
  const sequence = detailSequence
  if (!pageActive || !detailVisible.value || !detail || !isPollableStatus(detail.status) || pollInFlight) return
  pollInFlight = true
  try {
    const latest = await getWebRecording(detail.id)
    if (!pageActive || !detailVisible.value || sequence !== detailSequence || recordingDetail.value?.id !== detail.id) return
    recordingDetail.value = latest
    syncEditedEvents(latest)
    detailError.value = null
    if (aiDetailIsEligible(latest)) prepareAiContext(latest, sequence)
    if (!isPollableStatus(latest.status)) stopPolling()
  } catch (error) {
    if (sequence === detailSequence && recordingDetail.value?.id === detail.id) {
      detailError.value = safeError(error, '录制状态刷新失败，已保留上次详情')
      if (!silent) ElMessage.error(detailError.value)
    }
  } finally {
    pollInFlight = false
    if (sequence === detailSequence && recordingDetail.value?.id === detail.id && isPollableStatus(recordingDetail.value.status)) schedulePolling()
  }
}

async function loadRunners(sequence: number): Promise<void> {
  if (!props.projectId) return
  const runnerRequestSequence = ++runnerSequence
  runnersLoading.value = true
  runnersError.value = null
  try {
    const response = await getRunners()
    if (sequence !== projectSequence || runnerRequestSequence !== runnerSequence || props.projectId === undefined) return
    runners.value = response.items
    if (recordingForm.value.runner_id && !selectableRunners.value.some((runner) => runner.id === recordingForm.value.runner_id)) {
      recordingForm.value.runner_id = ''
    }
  } catch (error) {
    if (sequence === projectSequence && runnerRequestSequence === runnerSequence) runnersError.value = safeError(error, 'Runner 资源加载失败，请稍后重试')
  } finally {
    if (sequence === projectSequence && runnerRequestSequence === runnerSequence) runnersLoading.value = false
  }
}

async function loadRecordings(sequence: number): Promise<void> {
  const projectAtStart = props.projectId
  if (!projectAtStart) return
  recordingsLoading.value = true
  recordingsError.value = null
  try {
    const response = await getWebRecordings(projectAtStart, recordingsPage.value, recordingsPageSize.value)
    if (sequence !== projectSequence || props.projectId !== projectAtStart) return
    recordings.value = response.items
    recordingsTotal.value = response.total
    if (recordingDetail.value && !recordings.value.some((item) => item.id === recordingDetail.value?.id) && !detailVisible.value) {
      recordingDetail.value = null
    }
  } catch (error) {
    if (sequence === projectSequence && props.projectId === projectAtStart) recordingsError.value = safeError(error, '录制列表加载失败，请稍后重试')
  } finally {
    if (sequence === projectSequence && props.projectId === projectAtStart) recordingsLoading.value = false
  }
}

function aiDetailIsEligible(detail: WebRecordingDetailResponse): boolean {
  return detail.status === 'COMPLETED' && detail.confirmed_web_case_id === null
}

function isCurrentDetail(recordingId: string, sequence: number): boolean {
  return sequence === detailSequence
    && props.projectId !== undefined
    && recordingDetail.value?.id === recordingId
    && detailVisible.value
}

async function loadAiSuggestions(recordingId: string, projectAtStart: number, sequence: number): Promise<void> {
  aiSuggestionsLoading.value = true
  aiSuggestionsError.value = null
  try {
    const response = await getWebRecordingAiSuggestions(recordingId)
    if (!isCurrentDetail(recordingId, sequence) || props.projectId !== projectAtStart) return
    aiSuggestions.value = response.items
    selectedAiSuggestionId.value = response.items.find((item) => item.status === 'DRAFT')?.id ?? null
    syncAiContentDraft(selectedAiSuggestion.value)
  } catch (error) {
    if (isCurrentDetail(recordingId, sequence)) aiSuggestionsError.value = safeError(error, 'AI 整理建议加载失败，请稍后重试')
  } finally {
    if (isCurrentDetail(recordingId, sequence)) aiSuggestionsLoading.value = false
  }
}

async function refreshAiSuggestions(): Promise<void> {
  const detail = recordingDetail.value
  if (!detail || !isCurrentDetail(detail.id, detailSequence)) return
  await loadAiSuggestions(detail.id, detail.project_id, detailSequence)
}

async function loadAiConfiguration(projectAtStart: number, sequence: number, recordingId: string): Promise<void> {
  aiPromptLoading.value = true
  aiBindingLoading.value = true
  aiPromptError.value = null
  aiBindingError.value = null
  const promptRequest = getPrompts(false, 'WEB_CASE_GENERATE')
    .then((items) => {
      if (!isCurrentDetail(recordingId, sequence) || props.projectId !== projectAtStart) return
      aiPrompts.value = items
      selectedAiPromptId.value = items.find((item) => (
        item.enabled
        && item.current_version_id !== null
        && item.current_version !== null
        && item.current_version.output_schema_id !== null
      ))?.id
      if (!selectedAiPromptId.value) aiPromptError.value = '当前项目没有启用且绑定输出 Schema 的 Web 用例生成 Prompt，请先在 Prompt 中心配置。'
    })
    .catch((error: unknown) => {
      if (isCurrentDetail(recordingId, sequence)) aiPromptError.value = safeError(error, 'WEB_CASE_GENERATE Prompt 加载失败，请稍后重试')
    })
    .finally(() => {
      if (isCurrentDetail(recordingId, sequence)) aiPromptLoading.value = false
    })
  const bindingRequest = getModelBindings(projectAtStart)
    .then((items) => {
      if (!isCurrentDetail(recordingId, sequence) || props.projectId !== projectAtStart) return
      aiBindings.value = items
      if (!items.some((item) => item.task_type === 'WEB_CASE_GENERATE')) aiBindingError.value = '当前项目未配置 WEB_CASE_GENERATE 模型绑定，请先在项目设置中配置。'
    })
    .catch((error: unknown) => {
      if (isCurrentDetail(recordingId, sequence)) aiBindingError.value = safeError(error, '项目模型绑定加载失败，请稍后重试')
    })
    .finally(() => {
      if (isCurrentDetail(recordingId, sequence)) aiBindingLoading.value = false
    })
  await Promise.all([promptRequest, bindingRequest])
}

function prepareAiContext(detail: WebRecordingDetailResponse, sequence: number): void {
  clearAiState()
  if (!aiDetailIsEligible(detail) || !props.projectId) return
  void Promise.all([
    loadAiSuggestions(detail.id, props.projectId, sequence),
    loadAiConfiguration(props.projectId, sequence, detail.id),
  ])
}

function resetRecordingSelection(): void {
  invalidateAiDecisionContext()
  detailSequence += 1
  stopPolling()
  detailVisible.value = false
  recordingDetail.value = null
  sourceEvents.value = []
  deterministicBaseline.value = null
  editedEvents.value = []
  detailError.value = null
  operationRecordingId.value = null
  clearAiState()
}

function resetRecordingForm(): void {
  recordingForm.value = {
    runner_id: selectableRunners.value[0]?.id ?? '',
    plan_item_id: props.initialPlanItemId ?? null,
    environment_id: null,
    start_url: props.initialStartUrl ?? '',
    session_profile_id: null,
    save_session: false,
    save_session_name: '',
    save_session_expires_at: '',
  }
}

function syncEditedEvents(detail: WebRecordingDetailResponse): void {
  const detailChanged = recordingDetail.value?.id !== detail.id
  if (detailChanged) {
    sourceEvents.value = cloneRecordingEvents(detail.events)
    editedEvents.value = cloneRecordingEvents(detail.events)
    deterministicBaseline.value = detail.status === 'COMPLETED'
      ? buildRecordedContent(false, sourceEvents.value)
      : null
  } else if (editedEvents.value.length === 0 || detail.status !== 'COMPLETED') {
    editedEvents.value = cloneRecordingEvents(detail.events)
    if (detail.status === 'COMPLETED') {
      sourceEvents.value = cloneRecordingEvents(detail.events)
      deterministicBaseline.value = buildRecordedContent(false, sourceEvents.value)
    }
  }
}

function syncAiContentDraft(suggestion: WebRecordingAiSuggestionResponse | null): void {
  const content = suggestion?.status === 'DRAFT'
    ? (suggestion.human_content ?? suggestion.canonical_suggested_content)
    : null
  aiContentDraft.value = content ? cloneContent(content) : null
  aiContentEdited.value = false
}

function selectAiSuggestion(suggestion: WebRecordingAiSuggestionResponse): void {
  if (selectedAiSuggestionId.value === suggestion.id && aiContentDraft.value) return
  selectedAiSuggestionId.value = suggestion.id
  syncAiContentDraft(suggestion)
}

function markAiContentEdited(): void {
  aiContentEdited.value = true
}

async function openRecording(item: WebRecordingListItem): Promise<void> {
  const sequence = ++detailSequence
  stopPolling()
  detailVisible.value = true
  detailLoading.value = true
  detailError.value = null
  recordingDetail.value = null
  editedEvents.value = []
  try {
    const detail = await getWebRecording(item.id)
    if (sequence !== detailSequence || props.projectId !== item.project_id) return
    recordingDetail.value = detail
    syncEditedEvents(detail)
    prepareAiContext(detail, sequence)
    if (isPollableStatus(detail.status)) schedulePolling()
  } catch (error) {
    if (sequence === detailSequence) detailError.value = safeError(error, '录制详情加载失败，请稍后重试')
  } finally {
    if (sequence === detailSequence) detailLoading.value = false
  }
}

function closeDetail(): void {
  resetRecordingSelection()
}

function validateStartUrl(value: string): boolean {
  try {
    const url = new URL(value.trim())
    return (url.protocol === 'http:' || url.protocol === 'https:') && !url.username && !url.password && !url.hash && !/\s/.test(value)
  } catch {
    return false
  }
}

function buildCreatePayload(): WebRecordingCreateRequest | null {
  const form = recordingForm.value
  if (!props.projectId || !form.runner_id || !selectableRunners.value.some((runner) => runner.id === form.runner_id)) {
    ElMessage.warning('请选择当前在线且有可用 WEB Slot 的 Runner')
    return null
  }
  if (!validateStartUrl(form.start_url)) {
    ElMessage.warning('起始 URL 必须是合法的 http(s) 地址，且不能携带凭据或 fragment')
    return null
  }
  if (form.session_profile_id && !selectableProfiles.value.some((profile) => profile.id === form.session_profile_id)) {
    ElMessage.warning('请选择当前环境可用且未过期的会话配置')
    return null
  }
  if (form.save_session && !/^[A-Za-z][A-Za-z0-9_.-]*$/.test(form.save_session_name.trim())) {
    ElMessage.warning('保存 Session 的名称需以字母开头，只能包含字母、数字、点、下划线和连字符')
    return null
  }
  let expiresAt: string | null = null
  if (form.save_session_expires_at) {
    const date = new Date(form.save_session_expires_at)
    if (Number.isNaN(date.getTime())) {
      ElMessage.warning('请选择有效的保存 Session 过期时间')
      return null
    }
    expiresAt = date.toISOString()
  }
  return {
    project_id: props.projectId,
    runner_id: form.runner_id,
    plan_item_id: form.plan_item_id,
    environment_id: form.environment_id,
    start_url: form.start_url.trim(),
    session_profile_id: form.session_profile_id,
    save_session: form.save_session,
    ...(form.save_session ? {
      save_session_name: form.save_session_name.trim(),
      save_session_expires_at: expiresAt,
    } : {}),
  }
}

async function refreshRecordingDetail(
  recordingId: string,
  sequence = detailSequence,
  identity = readRequestIdentity(),
): Promise<WebRecordingDetailResponse | null> {
  if (!identity) return null
  try {
    const detail = await getWebRecording(recordingId)
    if (sequence !== detailSequence || props.projectId !== detail.project_id || !identityIsCurrent(identity)) return null
    recordingDetail.value = detail
    syncEditedEvents(detail)
    prepareAiContext(detail, sequence)
    if (isPollableStatus(detail.status)) schedulePolling()
    else stopPolling()
    return detail
  } catch (error) {
    if (sequence === detailSequence && recordingDetail.value?.id === recordingId) detailError.value = safeError(error, '录制详情刷新失败，已保留现有状态')
    return null
  }
}

async function createAndDispatch(): Promise<void> {
  if (createLoading.value || !props.canWrite) return
  const payload = buildCreatePayload()
  if (!payload || !props.projectId) return
  const projectAtStart = props.projectId
  createLoading.value = true
  try {
    const created = await createWebRecording(payload)
    if (props.projectId !== projectAtStart) return
    const sequence = ++detailSequence
    detailVisible.value = true
    detailError.value = null
    recordingDetail.value = created
    syncEditedEvents(created)
    await loadRecordings(projectSequence)
    try {
      await dispatchWebRecording(created.id)
      await refreshRecordingDetail(created.id, sequence)
      ElMessage.success('录制已创建并投递，正在等待 Runner 开始')
      emit('refresh-assets')
    } catch (error) {
      await refreshRecordingDetail(created.id, sequence)
      ElMessage.error(safeError(error, '录制已创建，但投递失败，请稍后重试'))
    }
  } catch (error) {
    if (props.projectId === projectAtStart) ElMessage.error(safeError(error, '录制创建失败，请检查配置'))
  } finally {
    createLoading.value = false
  }
}

async function dispatchExisting(): Promise<void> {
  const detail = recordingDetail.value
  if (!detail || operationRecordingId.value || !props.canWrite || !['CREATED', 'QUEUED'].includes(detail.status)) return
  const recordingId = detail.id
  const sequence = detailSequence
  operationRecordingId.value = recordingId
  try {
    await dispatchWebRecording(recordingId)
    await refreshRecordingDetail(recordingId, sequence)
    await loadRecordings(projectSequence)
    ElMessage.success('录制任务已投递')
  } catch (error) {
    await refreshRecordingDetail(recordingId, sequence)
    ElMessage.error(safeError(error, '录制投递失败，请稍后重试'))
  } finally {
    if (operationRecordingId.value === recordingId) operationRecordingId.value = null
  }
}

async function requestControl(kind: 'stop' | 'cancel'): Promise<void> {
  const detail = recordingDetail.value
  if (!detail || operationRecordingId.value || !props.canWrite) return
  const allowed = kind === 'stop' ? detail.status === 'RUNNING' : isActiveStatus(detail.status)
  if (!allowed) return
  try {
    await ElMessageBox.confirm(
      kind === 'stop'
        ? '停止会让 Runner 尽快结束当前录制并保存已收到的事件。正在进行的页面操作可能等待返回或超时。'
        : '取消只阻止尚未完成的录制任务。若 Runner 已开始，会请求它在安全点停止。',
      kind === 'stop' ? '请求停止录制？' : '取消录制？',
      { type: 'warning', confirmButtonText: kind === 'stop' ? '请求停止' : '确认取消', cancelButtonText: '暂不操作' },
    )
  } catch {
    return
  }
  const recordingId = detail.id
  const sequence = detailSequence
  operationRecordingId.value = recordingId
  try {
    const updated = kind === 'stop' ? await stopWebRecording(recordingId) : await cancelWebRecording(recordingId)
    if (sequence === detailSequence && recordingDetail.value?.id === recordingId) {
      recordingDetail.value = updated
      syncEditedEvents(updated)
      if (aiDetailIsEligible(updated)) prepareAiContext(updated, sequence)
      if (!isPollableStatus(updated.status)) stopPolling()
    }
    await loadRecordings(projectSequence)
    ElMessage.success(kind === 'stop' ? '已发送停止请求' : '录制已取消')
  } catch (error) {
    await refreshRecordingDetail(recordingId, sequence)
    ElMessage.error(safeError(error, kind === 'stop' ? '停止请求失败，请稍后重试' : '取消录制失败，请稍后重试'))
  } finally {
    if (operationRecordingId.value === recordingId) operationRecordingId.value = null
  }
}

function removeEvent(index: number): void {
  editedEvents.value.splice(index, 1)
}

function moveEvent(index: number, offset: -1 | 1): void {
  const target = index + offset
  if (target < 0 || target >= editedEvents.value.length) return
  const events = editedEvents.value
  const [event] = events.splice(index, 1)
  events.splice(target, 0, event)
}

function addLocator(event: WebRecordingEvent): void {
  const priorities = event.locator_candidates.map((candidate) => candidate.priority)
  const priority = Array.from({ length: 20 }, (_, index) => index + 1).find((item) => !priorities.includes(item)) ?? priorities.length + 1
  if (priority > 20) {
    ElMessage.warning('每个事件最多配置 20 个 Locator 候选')
    return
  }
  event.locator_candidates.push({ strategy: 'css', value: '', priority })
}

function removeLocator(event: WebRecordingEvent, index: number): void {
  event.locator_candidates.splice(index, 1)
}

function candidateLocator(event: WebRecordingEvent): WebLocator | null {
  const candidate = [...event.locator_candidates].sort((left, right) => left.priority - right.priority)[0]
  if (!candidate || !candidate.value.trim()) return null
  return { strategy: candidate.strategy, value: candidate.value.trim(), element_version_id: null }
}

function safeEventUrl(value: string | null): boolean {
  return Boolean(value && validateStartUrl(value))
}

function buildRecordedContent(notify = true, events = editedEvents.value): WebCaseContent | null {
  const detail = recordingDetail.value
  if (!detail || detail.status !== 'COMPLETED') return null
  const actions: WebAction[] = []
  const naturalLanguageSteps: string[] = []
  for (const event of events) {
    if (!eventTypes.includes(event.event_type)) continue
    const base = { timeout_ms: 30000, failure_policy: 'STOP' as const }
    if (event.event_type === 'NAVIGATE') {
      if (!safeEventUrl(event.target_url)) {
        if (notify) ElMessage.warning(`第 ${actions.length + 1} 个导航事件的 URL 无效`)
        return null
      }
      actions.push({ ...base, type: 'GOTO', url: event.target_url!.trim() })
      naturalLanguageSteps.push('打开页面')
    } else if (event.event_type === 'CLICK') {
      const locator = candidateLocator(event)
      if (!locator) {
        if (notify) ElMessage.warning('点击事件至少需要一个完整 Locator 候选')
        return null
      }
      actions.push({ ...base, type: 'CLICK', locator })
      naturalLanguageSteps.push('点击页面元素')
    } else if (event.event_type === 'FILL') {
      const locator = candidateLocator(event)
      if (!locator || !event.value || !(event.value === 'REDACTED' || /^\{\{[A-Za-z_][A-Za-z0-9_.-]*\}\}$/.test(event.value))) {
        if (notify) ElMessage.warning('填写事件需要 Locator，且值只能是 {{recording.xxx}} 或 REDACTED')
        return null
      }
      actions.push({ ...base, type: 'FILL', locator, value: event.value })
      naturalLanguageSteps.push('填写页面输入')
    } else if (event.event_type === 'SELECT') {
      const locator = candidateLocator(event)
      if (!locator || !event.value?.trim()) {
        if (notify) ElMessage.warning('选择事件需要完整 Locator 和选项值')
        return null
      }
      actions.push({ ...base, type: 'SELECT', locator, value: event.value.trim() })
      naturalLanguageSteps.push('选择页面选项')
    } else {
      const locator = candidateLocator(event)
      if (!locator || !event.key?.trim()) {
        if (notify) ElMessage.warning('按键事件需要完整 Locator 和按键')
        return null
      }
      actions.push({ ...base, type: 'PRESS', locator, key: event.key.trim() })
      naturalLanguageSteps.push('在页面元素上按键')
    }
  }
  if (actions.length > 200) {
    if (notify) ElMessage.warning('生成的 Web 用例最多支持 200 个操作，请删除部分录制事件后再确认')
    return null
  }
  if (actions.length === 0) {
    if (!safeEventUrl(detail.start_url)) {
      if (notify) ElMessage.warning('录制没有可转换的事件，且起始 URL 无效')
      return null
    }
    actions.push({ timeout_ms: 30000, failure_policy: 'STOP', type: 'GOTO', url: detail.start_url })
    naturalLanguageSteps.push('打开起始页面')
  }
  return {
    start_url: detail.start_url,
    natural_language_steps: naturalLanguageSteps,
    actions,
    assertions: [],
    session_profile_id: detail.saved_session_profile_id ?? detail.session_profile_id ?? null,
    browser: 'CHROME',
    headless: false,
    browser_config: {
      window_width: 1280,
      window_height: 720,
      language: null,
      user_agent: null,
      proxy: null,
      download_path: null,
    },
    total_timeout_ms: 900000,
    parameters: {},
  }
}

const aiActionTypes = WEB_ACTION_TYPES
const webTabAliasHelp = WEB_TAB_ALIAS_HELP
const aiAssertionTypes = WEB_ASSERTION_TYPES

function makeAiAction(type: WebAction['type']): WebAction {
  return makeWebAction(type)
}

function makeAiAssertion(type: WebAssertion['type']): WebAssertion {
  return makeWebAssertion(type)
}

function aiHasLocator(item: WebAction | WebAssertion): item is (WebAction & { locator: WebLocator }) | (WebAssertion & { locator: WebLocator }) {
  return hasWebLocator(item)
}

function aiGetLocator(item: WebAction | WebAssertion): WebLocator | null {
  return aiHasLocator(item) ? item.locator : null
}

function aiActionSummary(action: WebAction): string {
  return webActionSummary(action)
}

function aiAssertionSummary(assertion: WebAssertion): string {
  return webAssertionSummary(assertion)
}

function aiReplaceAction(index: number, type: WebAction['type']): void {
  if (!aiContentDraft.value) return
  aiContentDraft.value.actions.splice(index, 1, makeAiAction(type))
  markAiContentEdited()
}

function aiReplaceAssertion(index: number, type: WebAssertion['type']): void {
  if (!aiContentDraft.value) return
  aiContentDraft.value.assertions.splice(index, 1, makeAiAssertion(type))
  markAiContentEdited()
}

function aiRemoveAction(index: number): void {
  if (!aiContentDraft.value || aiContentDraft.value.actions.length <= 1) {
    ElMessage.warning('Web 用例至少需要保留一个操作')
    return
  }
  aiContentDraft.value.actions.splice(index, 1)
  markAiContentEdited()
}

function aiRemoveAssertion(index: number): void {
  aiContentDraft.value?.assertions.splice(index, 1)
  markAiContentEdited()
}

function aiAddAction(): void {
  if (!aiContentDraft.value) return
  if (aiContentDraft.value.actions.length >= 200) {
    ElMessage.warning('Web 用例最多支持 200 个操作')
    return
  }
  aiContentDraft.value.actions.push(makeAiAction('CLICK'))
  markAiContentEdited()
}

function aiAddAssertion(): void {
  if (!aiContentDraft.value) return
  if (aiContentDraft.value.assertions.length >= 100) {
    ElMessage.warning('Web 用例最多支持 100 个断言')
    return
  }
  aiContentDraft.value.assertions.push(makeAiAssertion('ASSERT_VISIBLE'))
  markAiContentEdited()
}

function aiUpdateLocatorStrategy(item: WebAction | WebAssertion, value: LocatorStrategy): void {
  const locator = aiGetLocator(item)
  if (locator) {
    locator.strategy = value
    markAiContentEdited()
  }
}

function aiUpdateLocatorValue(item: WebAction | WebAssertion, value: string): void {
  const locator = aiGetLocator(item)
  if (locator) {
    locator.value = value
    markAiContentEdited()
  }
}

function validateAiContent(content: WebCaseContent): boolean {
  if (!content.start_url.trim() || !safeEventUrl(content.start_url)) {
    ElMessage.warning('AI Web 用例的起始 URL 无效')
    return false
  }
  if (content.actions.length < 1 || content.actions.length > 200) {
    ElMessage.warning('AI Web 用例的操作数量必须为 1～200')
    return false
  }
  for (const action of content.actions) {
    if (!Number.isInteger(action.timeout_ms) || action.timeout_ms < 100 || action.timeout_ms > 600000) {
      ElMessage.warning('AI Web 用例操作超时必须为 100～600,000 毫秒')
      return false
    }
    if (action.failure_policy !== 'STOP' && action.failure_policy !== 'CONTINUE') {
      ElMessage.warning('AI Web 用例操作的失败策略无效')
      return false
    }
    if (aiHasLocator(action)) {
      const locator = action.locator
      if (locator.element_version_id === null && (!locator.strategy || !locator.value?.trim())) {
        ElMessage.warning('AI Web 用例中存在未完成的定位器')
        return false
      }
      if (locator.value && containsSensitiveText(locator.value)) {
        ElMessage.warning('AI Web 用例定位器不能包含凭据或 Token')
        return false
      }
    }
    if ((action.type === 'GOTO' || action.type === 'WAIT_URL') && !action.url.trim()) {
      ElMessage.warning(`${action.type} 必须填写 URL`)
      return false
    }
    if (action.type === 'NEW_TAB' && !validWebUrlOrTemplate(action.url)) {
      ElMessage.warning('NEW_TAB 必须填写合法的 http(s) URL 或运行时模板')
      return false
    }
    if ((action.type === 'NEW_TAB' || action.type === 'SWITCH_TAB' || action.type === 'CLOSE_TAB')
      && !validWebTabAlias(action.value, action.type !== 'NEW_TAB')) {
      ElMessage.warning(action.type === 'NEW_TAB'
        ? 'NEW_TAB 标签页别名格式无效，且不能使用初始页别名 main'
        : `${action.type} 标签页别名格式无效`)
      return false
    }
    if ((action.type === 'FILL' || action.type === 'SELECT') && !action.value.trim()) {
      ElMessage.warning(`${action.type} 必须填写值`)
      return false
    }
    if (action.type === 'FILL' && !(action.value === 'REDACTED' || /^\{\{[A-Za-z_][A-Za-z0-9_.-]*\}\}$/.test(action.value))) {
      ElMessage.warning('FILL 只能使用 {{recording.xxx}} 或 REDACTED')
      return false
    }
    if (action.type === 'SELECT' && containsSensitiveText(action.value)) {
      ElMessage.warning('SELECT 值不能包含凭据或 Token')
      return false
    }
    if (action.type === 'PRESS' && !action.key.trim()) {
      ElMessage.warning('PRESS 必须填写按键')
      return false
    }
  }
  for (const assertion of content.assertions) {
    if (!Number.isInteger(assertion.timeout_ms) || assertion.timeout_ms < 100 || assertion.timeout_ms > 600000) {
      ElMessage.warning('AI Web 用例断言超时必须为 100～600,000 毫秒')
      return false
    }
    if (aiHasLocator(assertion) && assertion.locator.element_version_id === null && (!assertion.locator.strategy || !assertion.locator.value?.trim())) {
      ElMessage.warning('AI Web 用例断言中存在未完成的定位器')
      return false
    }
    if (aiHasLocator(assertion) && assertion.locator.value && containsSensitiveText(assertion.locator.value)) {
      ElMessage.warning('AI Web 用例断言定位器不能包含凭据或 Token')
      return false
    }
    if (!validWebAssertionExpected(assertion)) {
      ElMessage.warning(`${assertion.type} 的期望值缺失、超长或无效`)
      return false
    }
    if ('expected' in assertion && containsSensitiveText(assertion.expected)) {
      ElMessage.warning('AI Web 用例断言不能包含凭据或 Token')
      return false
    }
  }
  return true
}

function aiContentForSubmit(): WebCaseContent | null | undefined {
  if (!aiContentEdited.value) return undefined
  if (!aiContentDraft.value || !recordingDetail.value) return null
  const content = cloneContent(aiContentDraft.value)
  content.session_profile_id = recordingDetail.value.saved_session_profile_id
    ?? recordingDetail.value.session_profile_id
    ?? null
  return validateAiContent(content) ? content : null
}

async function generateAiSuggestion(): Promise<void> {
  const detail = recordingDetail.value
  if (!detail || !aiDetailIsEligible(detail) || aiGenerating.value || !props.canWrite) return
  if (hasDraftAiSuggestion.value) {
    ElMessage.warning('当前录制已有待审核 AI 建议，请先接受或拒绝后再重新生成')
    return
  }
  const prompt = availableAiPrompts.value.find((item) => item.id === selectedAiPromptId.value)
  if (!prompt || prompt.current_version_id === null || !aiBinding.value) {
    ElMessage.warning('当前项目缺少可用 Prompt 或 WEB_CASE_GENERATE 模型绑定，请先配置后再生成')
    return
  }
  const additionalInstructions = aiAdditionalInstructions.value.trim()
  if (additionalInstructions.includes('\n') || additionalInstructions.includes('\r')) {
    ElMessage.warning('补充说明不能包含换行')
    return
  }
  if (containsSensitiveText(additionalInstructions)) {
    ElMessage.warning('补充说明不能包含凭据、Cookie 或 Token')
    return
  }
  const recordingId = detail.id
  const sequence = detailSequence
  aiGenerating.value = true
  try {
    const suggestion = await generateWebRecordingAiSuggestion(recordingId, {
      prompt_id: prompt.id,
      additional_instructions: additionalInstructions || null,
    })
    if (!isCurrentDetail(recordingId, sequence)) return
    aiSuggestions.value = [
      suggestion,
      ...aiSuggestions.value.filter((item) => item.id !== suggestion.id),
    ]
    selectedAiSuggestionId.value = suggestion.id
    syncAiContentDraft(suggestion)
    await loadAiSuggestions(recordingId, detail.project_id, sequence)
    ElMessage.success('AI 整理建议已生成，请人工审核后接受或拒绝')
  } catch (error) {
    if (isCurrentDetail(recordingId, sequence)) {
      const message = isAiSuggestionRequestTimeout(error)
        ? 'AI 整理请求已等待 90 秒，后端可能仍在处理；请点击“刷新建议”查看结果，不要重复生成。'
        : safeError(error, 'AI 整理建议生成失败，请检查 Prompt、模型绑定和模型服务商')
      aiSuggestionsError.value = message
      ElMessage.error(message)
    }
  } finally {
    aiGenerating.value = false
  }
}

function openAiAcceptDialog(suggestion: WebRecordingAiSuggestionResponse): void {
  if (!props.canWrite || suggestion.status !== 'DRAFT' || !recordingDetail.value || !aiDetailIsEligible(recordingDetail.value)) return
  selectAiSuggestion(suggestion)
  aiAcceptTarget.value = 'new'
  aiAcceptName.value = suggestion.structured_result?.suggested_name?.trim() || 'recorded_web_case'
  aiAcceptWebCaseId.value = undefined
  aiDecisionNote.value = ''
  aiAcceptVisible.value = true
}

async function acceptAiSuggestion(): Promise<void> {
  const detail = recordingDetail.value
  const suggestion = selectedAiSuggestion.value
  if (!detail || !suggestion || suggestion.status !== 'DRAFT' || !aiDetailIsEligible(detail)
    || aiAcceptSaving.value || activeAiDecisionOperationId !== null || !props.canWrite) return
  const name = aiAcceptName.value.trim()
  const webCaseId = aiAcceptWebCaseId.value
  if (aiAcceptTarget.value === 'new' && (name.length < 2 || name.length > 255)) {
    ElMessage.warning('新 Web 用例名称长度需为 2～255 个字符')
    return
  }
  if (aiAcceptTarget.value === 'existing' && (!webCaseId || !activeWebCases.value.some((item) => item.id === webCaseId))) {
    ElMessage.warning('请选择当前项目中的非归档 Web 用例')
    return
  }
  const content = aiContentForSubmit()
  if (content === null) return
  const decisionNote = aiDecisionNote.value.trim()
  if (decisionNote.length > 500 || decisionNote.includes('\n') || decisionNote.includes('\r') || containsSensitiveText(decisionNote)) {
    ElMessage.warning('决策说明最多 500 个字符且不能换行')
    return
  }
  const recordingId = detail.id
  const sequence = detailSequence
  const projectAtStart = props.projectId
  const identity = readRequestIdentity()
  if (!projectAtStart || !identity) return
  const generation = aiDecisionGeneration
  const operationId = ++nextAiDecisionOperationId
  activeAiDecisionOperationId = operationId
  aiAcceptSaving.value = true
  try {
    const payload = {
      ai_suggestion_id: suggestion.id,
      decision_note: decisionNote || null,
      ...(aiAcceptTarget.value === 'new' ? { name } : { web_case_id: webCaseId }),
      ...(content !== undefined ? { content } : {}),
    }
    const response = await confirmWebRecording(recordingId, payload)
    if (!aiDecisionCurrent(operationId, generation, identity, recordingId, sequence, projectAtStart)) return
    aiAcceptVisible.value = false
    await refreshRecordingDetail(recordingId, sequence, identity)
    if (!aiDecisionCurrent(operationId, generation, identity, recordingId, sequence, projectAtStart)) return
    await loadRecordings(projectSequence)
    if (!aiDecisionCurrent(operationId, generation, identity, recordingId, sequence, projectAtStart)) return
    await loadAiSuggestions(recordingId, detail.project_id, sequence)
    if (!aiDecisionCurrent(operationId, generation, identity, recordingId, sequence, projectAtStart)) return
    emit('refresh-assets')
    ElMessage.success('AI 建议已接受，已生成 Web 用例草稿版本；仍需人工批准')
  } catch (error) {
    if (aiDecisionCurrent(operationId, generation, identity, recordingId, sequence, projectAtStart)) {
      ElMessage.error(safeError(error, '接受 AI 建议失败，请检查候选内容和 Web 用例状态'))
    }
  } finally {
    finishAiDecision(operationId)
  }
}

function openAiRejectDialog(suggestion: WebRecordingAiSuggestionResponse): void {
  if (!props.canWrite || suggestion.status !== 'DRAFT') return
  aiRejectSuggestionId.value = suggestion.id
  aiRejectDecisionNote.value = ''
  aiRejectVisible.value = true
}

async function rejectAiSuggestion(): Promise<void> {
  const detail = recordingDetail.value
  const suggestionId = aiRejectSuggestionId.value
  const suggestion = aiSuggestions.value.find((item) => item.id === suggestionId)
  if (!detail || suggestionId === null || !suggestion || suggestion.status !== 'DRAFT'
    || aiRejectSaving.value || activeAiDecisionOperationId !== null || !props.canWrite) return
  const note = aiRejectDecisionNote.value.trim()
  if (note.length > 500 || note.includes('\n') || note.includes('\r') || containsSensitiveText(note)) {
    ElMessage.warning('拒绝说明最多 500 个字符且不能换行')
    return
  }
  const recordingId = detail.id
  const sequence = detailSequence
  const projectAtStart = props.projectId
  const identity = readRequestIdentity()
  if (!projectAtStart || !identity) return
  const generation = aiDecisionGeneration
  const operationId = ++nextAiDecisionOperationId
  activeAiDecisionOperationId = operationId
  aiRejectSaving.value = true
  try {
    try {
      await ElMessageBox.confirm(
        '拒绝后该建议不能再接受，但不会修改任何 Web 用例；之后可以重新生成新的建议。',
        '确认拒绝 AI 建议？',
        { type: 'warning', confirmButtonText: '确认拒绝', cancelButtonText: '暂不拒绝' },
      )
    } catch {
      return
    }
    if (!aiDecisionCurrent(operationId, generation, identity, recordingId, sequence, projectAtStart)) return
    await rejectWebRecordingAiSuggestion(recordingId, suggestionId, { decision_note: note || null })
    if (!aiDecisionCurrent(operationId, generation, identity, recordingId, sequence, projectAtStart)) return
    aiRejectVisible.value = false
    await loadAiSuggestions(recordingId, detail.project_id, sequence)
    if (!aiDecisionCurrent(operationId, generation, identity, recordingId, sequence, projectAtStart)) return
    ElMessage.success('AI 建议已拒绝，可重新生成')
  } catch (error) {
    if (aiDecisionCurrent(operationId, generation, identity, recordingId, sequence, projectAtStart)) {
      ElMessage.error(safeError(error, '拒绝 AI 建议失败，请稍后重试'))
    }
  } finally {
    finishAiDecision(operationId)
  }
}

function onIdentityStorage(event: StorageEvent): void {
  if (!isIdentityStorageEvent(event)) return
  projectSequence += 1
  runnerSequence += 1
  resetRecordingSelection()
  recordings.value = []
  recordingsTotal.value = 0
  runners.value = []
  recordingsError.value = '登录身份已变化，旧身份的录制与 AI 建议已清除。'
}

function openConfirmDialog(): void {
  if (!props.canWrite || !recordingDetail.value || recordingDetail.value.status !== 'COMPLETED') return
  if (!buildRecordedContent()) return
  confirmTarget.value = 'new'
  confirmName.value = ''
  confirmWebCaseId.value = undefined
  lastConfirmMessage.value = null
  confirmVisible.value = true
}

async function confirmRecording(): Promise<void> {
  const content = buildRecordedContent()
  const detail = recordingDetail.value
  if (!content || !detail || confirmSaving.value || !props.canWrite) return
  const name = confirmName.value.trim()
  const caseId = confirmWebCaseId.value
  if (confirmTarget.value === 'new' && (name.length < 2 || name.length > 255)) {
    ElMessage.warning('新 Web 用例名称长度需为 2～255 个字符')
    return
  }
  if (confirmTarget.value === 'existing' && (!caseId || !activeWebCases.value.some((item) => item.id === caseId))) {
    ElMessage.warning('请选择当前项目中的 Web 用例')
    return
  }
  confirmSaving.value = true
  try {
    const response = await confirmWebRecording(detail.id, confirmTarget.value === 'new'
      ? { name, content }
      : { web_case_id: caseId, content })
    lastConfirmMessage.value = '已生成 Web 用例草稿版本。请到资产页检查并批准后再执行。'
    confirmVisible.value = false
    await refreshRecordingDetail(detail.id)
    emit('refresh-assets')
    ElMessage.success('录制事件已转换为 Web 用例草稿')
  } catch (error) {
    ElMessage.error(safeError(error, '确认生成 Web 用例失败，请检查事件和定位器'))
  } finally {
    confirmSaving.value = false
  }
}

function changePage(page: number): void {
  if (page < 1 || page > totalPages.value || page === recordingsPage.value || !props.projectId) return
  recordingsPage.value = page
  void loadRecordings(projectSequence)
}

watch(() => recordingForm.value.environment_id, () => {
  if (recordingForm.value.session_profile_id && !selectableProfiles.value.some((profile) => profile.id === recordingForm.value.session_profile_id)) {
    recordingForm.value.session_profile_id = null
  }
})

watch(() => [props.initialPlanItemId, props.initialStartUrl] as const, ([planItemId, startUrl]) => {
  recordingForm.value.plan_item_id = planItemId ?? null
  if (startUrl) recordingForm.value.start_url = startUrl
})

watch(() => [props.projectId, props.refreshKey], ([projectId], previousSelection) => {
  const sequence = ++projectSequence
  runnerSequence += 1
  const projectChanged = projectId !== previousSelection?.[0]
  if (projectChanged) resetRecordingSelection()
  recordings.value = []
  recordingsTotal.value = 0
  recordingsPage.value = 1
  runners.value = []
  runnersError.value = null
  if (!projectId) {
    if (projectChanged) resetRecordingForm()
    return
  }
  if (projectChanged) resetRecordingForm()
  void Promise.all([loadRunners(sequence), loadRecordings(sequence)])
}, { immediate: true })

onMounted(() => {
  pageActive = true
  window.addEventListener('storage', onIdentityStorage)
})
onActivated(() => {
  pageActive = true
  if (recordingDetail.value && detailVisible.value && recordingIsPollable.value) schedulePolling()
})
onDeactivated(() => {
  pageActive = false
  stopPolling()
})
onUnmounted(() => {
  pageActive = false
  stopPolling()
  detailSequence += 1
  projectSequence += 1
  invalidateAiDecisionContext()
  window.removeEventListener('storage', onIdentityStorage)
})
</script>

<template>
  <div class="recording-workbench">
    <el-alert title="录制安全边界" description="录制只保存经过后端脱敏的事件预览，不展示 DOM、Cookie、Token 或原始响应。确认后生成的是 Web 用例草稿，仍需在资产页检查并批准。" type="info" :closable="false" show-icon />
    <el-alert v-if="!props.canWrite" title="当前角色为只读：可查看录制、事件与 AI 建议，但不能创建录制、生成建议或提交接受/拒绝决策。" type="info" :closable="false" show-icon class="section-gap" />

    <div class="recording-create-grid section-gap">
      <el-card shadow="never" class="recording-config-card">
        <template #header><div class="section-heading"><div><strong>配置并开始录制</strong><p>使用当前项目的在线 Web Runner 打开一个页面</p></div></div></template>
        <el-alert v-if="recordingForm.plan_item_id" :title="`已绑定 AI 测试方案条目 #${recordingForm.plan_item_id}`" description="录制完成后可回到 AI 测试设计，用真实录制事实校准该条目。" type="success" show-icon class="section-gap" @close="recordingForm.plan_item_id = null" />
        <el-form label-position="top">
          <el-form-item label="Web Runner"><el-select v-model="recordingForm.runner_id" filterable :loading="runnersLoading" placeholder="选择可用 Runner" class="full-width"><el-option v-for="runner in selectableRunners" :key="runner.id" :label="runnerLabel(runner)" :value="runner.id" /></el-select><span v-if="runnersError" class="safe-note error-note">{{ runnersError }}</span><span v-else-if="!runnersLoading && !selectableRunners.length" class="safe-note">暂无满足在线、Redis 可用、Web 能力就绪且有空闲槽位的 Runner</span></el-form-item>
          <el-form-item label="起始 URL"><el-input v-model="recordingForm.start_url" maxlength="2048" placeholder="https://example.com/login" /></el-form-item>
          <el-form-item label="环境（可选）"><el-select v-model="recordingForm.environment_id" clearable filterable :loading="environmentsLoading" placeholder="不绑定" class="full-width"><el-option v-for="environment in environments" :key="environment.id" :label="`${environment.name}（${environment.code}）`" :value="environment.id" /></el-select><span v-if="environmentsError" class="safe-note error-note">{{ environmentsError }}</span></el-form-item>
          <el-form-item label="会话配置（可选）"><el-select v-model="recordingForm.session_profile_id" clearable filterable placeholder="不使用已保存会话" class="full-width"><el-option v-for="profile in selectableProfiles" :key="profile.id" :label="`${profile.name}${profile.expires_at ? ` · ${dateLabel(profile.expires_at)} 到期` : ''}`" :value="profile.id" /></el-select><span class="safe-note">仅显示有效且未过期、与当前环境匹配的会话配置。</span></el-form-item>
          <el-form-item><el-checkbox v-model="recordingForm.save_session">录制完成后保存新会话配置</el-checkbox><span class="safe-note">保存结果只返回安全元数据；不会在页面展示会话状态。</span></el-form-item>
          <template v-if="recordingForm.save_session"><el-form-item label="新会话配置名称"><el-input v-model="recordingForm.save_session_name" maxlength="128" placeholder="例如 member_session" /></el-form-item><el-form-item label="会话过期时间（可选）"><el-date-picker v-model="recordingForm.save_session_expires_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ" placeholder="未设置" class="full-width" /></el-form-item></template>
          <el-button type="primary" :icon="Plus" :loading="createLoading" :disabled="!props.canWrite || !selectableRunners.length" @click="createAndDispatch">创建并投递录制</el-button>
        </el-form>
      </el-card>

      <el-card shadow="never" class="recording-list-card">
        <template #header><div class="section-heading"><div><strong>录制任务</strong><p>列表按当前项目显示，详情对进行中的任务每 5 秒静默刷新。</p></div><el-button text :icon="Refresh" :loading="recordingsLoading" @click="props.projectId && loadRecordings(projectSequence)">刷新</el-button></div></template>
        <el-alert v-if="recordingsError" :title="recordingsError" type="error" :closable="false" show-icon />
        <el-table v-loading="recordingsLoading" :data="recordings" size="small" table-layout="fixed">
          <el-table-column label="录制" min-width="170"><template #default="{ row }"><strong>{{ row.id.slice(0, 12) }}</strong><small class="table-secondary">{{ row.start_url }}</small></template></el-table-column>
          <el-table-column label="状态" width="105"><template #default="{ row }"><el-tag size="small" :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag></template></el-table-column>
          <el-table-column label="投递" width="90"><template #default="{ row }">{{ dispatchStatusLabel(row.dispatch_status) }}</template></el-table-column>
          <el-table-column prop="event_count" label="事件" width="65" />
          <el-table-column label="更新时间" width="150"><template #default="{ row }">{{ dateLabel(row.updated_at) }}</template></el-table-column>
          <el-table-column label="操作" width="90" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="openRecording(row)">详情</el-button></template></el-table-column>
        </el-table>
        <el-empty v-if="!recordingsLoading && !recordingsError && !recordings.length" description="当前项目暂无录制任务" :image-size="60" />
        <el-pagination v-if="recordingsTotal" class="records-pagination" small layout="total, sizes, prev, pager, next" :current-page="recordingsPage" :page-size="recordingsPageSize" :page-sizes="[10, 20, 50, 100]" :total="recordingsTotal" @current-change="changePage" @size-change="(value: number) => { recordingsPageSize = value; recordingsPage = 1; loadRecordings(projectSequence) }" />
      </el-card>
    </div>

    <el-alert v-if="lastConfirmMessage" :title="lastConfirmMessage" type="success" :closable="true" show-icon class="section-gap" @close="lastConfirmMessage = null" />

    <el-drawer v-model="detailVisible" title="录制详情" size="min(920px, 96vw)" @closed="closeDetail">
      <div v-loading="detailLoading" class="recording-detail">
        <el-alert v-if="detailError" :title="detailError" type="error" :closable="false" show-icon />
        <template v-if="recordingDetail">
          <div class="detail-heading"><div><h2>{{ recordingDetail.id }}</h2><p>{{ recordingDetail.start_url }}</p></div><div class="detail-actions"><el-tag :type="statusType(recordingDetail.status)">{{ statusLabel(recordingDetail.status) }}</el-tag><el-button v-if="recordingDetail.status === 'CREATED' || recordingDetail.status === 'QUEUED'" type="primary" :loading="currentRecordingOperation" :disabled="!props.canWrite" @click="dispatchExisting">重新投递</el-button><el-button v-if="recordingDetail.status === 'RUNNING'" type="warning" :loading="currentRecordingOperation" :disabled="!props.canWrite" @click="requestControl('stop')">请求停止</el-button><el-button v-if="isActiveStatus(recordingDetail.status)" type="danger" plain :loading="currentRecordingOperation" :disabled="!props.canWrite" @click="requestControl('cancel')">取消录制</el-button></div></div>
          <el-descriptions :column="2" border size="small"><el-descriptions-item label="Runner">{{ recordingDetail.runner_id }}</el-descriptions-item><el-descriptions-item label="环境">{{ recordingDetail.environment_id ? `#${recordingDetail.environment_id}` : '未绑定' }}</el-descriptions-item><el-descriptions-item label="会话配置">{{ recordingDetail.session_profile_id ? `#${recordingDetail.session_profile_id}` : '未使用' }}</el-descriptions-item><el-descriptions-item label="投递状态">{{ dispatchStatusLabel(recordingDetail.dispatch_status) }}</el-descriptions-item><el-descriptions-item label="事件数量">{{ recordingDetail.event_count }}</el-descriptions-item><el-descriptions-item label="创建时间">{{ dateLabel(recordingDetail.created_at) }}</el-descriptions-item><el-descriptions-item label="开始时间">{{ dateLabel(recordingDetail.started_at) }}</el-descriptions-item><el-descriptions-item label="完成时间">{{ dateLabel(recordingDetail.completed_at) }}</el-descriptions-item></el-descriptions>
          <el-alert v-if="recordingDetail.error_message" :title="recordingDetail.error_type || '录制失败'" :description="recordingDetail.error_message" type="error" :closable="false" show-icon class="section-gap" />
          <el-alert v-if="recordingDetail.status === 'RUNNING' || recordingDetail.status === 'STOP_REQUESTED'" title="正在观察录制状态" description="详情会自动静默刷新；离开页面或关闭详情后会停止刷新。" type="info" :closable="false" show-icon class="section-gap" />

          <div v-if="recordingDetail.status === 'COMPLETED'" class="event-editor section-gap">
            <div class="section-heading"><div><h3>事件草稿</h3><p>仅保留安全的页面操作事件。可删除噪声、调整顺序和定位器；确认后生成不能自动批准的 Web 用例草稿。</p></div><el-button type="primary" :icon="Edit" :disabled="!props.canWrite" @click="openConfirmDialog">确认生成 Web 用例</el-button></div>
            <el-empty v-if="!editedEvents.length" description="录制未返回可转换的安全事件" :image-size="60" />
            <div v-for="(event, index) in editedEvents" :key="`${event.sequence}-${index}`" class="event-card">
              <div class="event-card-heading"><span class="step-number">{{ index + 1 }}</span><el-tag size="small" type="info">{{ eventTypeLabel(event.event_type) }}</el-tag><span class="event-time">+{{ event.relative_time_ms }} ms</span><el-button link :icon="SortUp" :disabled="index === 0" @click="moveEvent(index, -1)">上移</el-button><el-button link :icon="SortDown" :disabled="index === editedEvents.length - 1" @click="moveEvent(index, 1)">下移</el-button><el-button link type="danger" :icon="Delete" @click="removeEvent(index)">删除</el-button></div>
              <div class="event-fields"><el-input v-if="event.event_type === 'NAVIGATE'" v-model="event.target_url" label="目标 URL" placeholder="安全的 http(s) URL" /><el-input v-if="event.event_type === 'FILL'" v-model="event.value" label="填写值" placeholder="{{recording.username}} 或 REDACTED" /><el-input v-if="event.event_type === 'SELECT'" v-model="event.value" label="选择值" placeholder="SELECT value" /><el-input v-if="event.event_type === 'PRESS'" v-model="event.key" label="按键" placeholder="例如 Enter" /></div>
              <div v-if="event.event_type !== 'NAVIGATE'" class="locator-editor"><div class="section-heading"><div><strong>定位器候选</strong><p>只保存策略、值和优先级，不展示 DOM 上下文。</p></div><el-button text :icon="Plus" @click="addLocator(event)">添加</el-button></div><div v-for="(candidate, candidateIndex) in event.locator_candidates" :key="candidateIndex" class="locator-row"><el-select v-model="candidate.strategy" style="width: 130px"><el-option v-for="strategy in locatorStrategies" :key="strategy" :label="strategy" :value="strategy" /></el-select><el-input v-model="candidate.value" placeholder="定位器值" /><el-input-number v-model="candidate.priority" :min="1" :max="20" controls-position="right" /><el-button link type="danger" @click="removeLocator(event, candidateIndex)">删除</el-button></div><el-empty v-if="!event.locator_candidates.length" description="暂无定位器候选" :image-size="40" /></div>
            </div>
          </div>

          <section v-if="recordingDetail.status === 'COMPLETED' && recordingDetail.confirmed_web_case_id === null" v-loading="aiLoading" class="ai-suggestion-section section-gap">
            <div class="section-heading"><div><h3>AI 整理建议</h3><p>AI 只接收后端生成的安全事件快照。建议永远需要人工接受或拒绝，不会自动创建、批准或覆盖 Web 用例。</p></div><div class="detail-actions"><el-button link type="primary" :loading="aiSuggestionsLoading" :disabled="aiGenerating" @click="refreshAiSuggestions">刷新建议</el-button><el-tag v-if="hasDraftAiSuggestion" type="warning">有待审核建议</el-tag></div></div>
            <el-alert v-if="aiPromptError" :title="aiPromptError" type="warning" :closable="false" show-icon class="section-gap" />
            <el-alert v-if="aiBindingError" :title="aiBindingError" type="warning" :closable="false" show-icon class="section-gap" />
            <div class="ai-generate-form section-gap">
              <el-select v-model="selectedAiPromptId" filterable :disabled="!availableAiPrompts.length || hasDraftAiSuggestion" placeholder="选择启用的 WEB_CASE_GENERATE Prompt" class="ai-prompt-select"><el-option v-for="prompt in availableAiPrompts" :key="prompt.id" :label="promptOptionLabel(prompt)" :value="prompt.id" /></el-select>
              <el-input v-model="aiAdditionalInstructions" type="textarea" :rows="2" maxlength="5000" show-word-limit :disabled="hasDraftAiSuggestion" placeholder="补充整理要求（可选，不要填写凭据、Cookie、Token 或原始响应）" />
              <el-button type="primary" :loading="aiGenerating" :disabled="!props.canWrite || !aiContextReady || hasDraftAiSuggestion" @click="generateAiSuggestion">生成 AI 建议</el-button>
            </div>
            <span v-if="!aiLoading && !aiPromptError && !availableAiPrompts.length" class="safe-note">没有可用 Prompt：请在 Prompt 中启用 Web 用例生成任务，并为当前版本绑定启用的输出 Schema。</span>
            <span v-if="!aiLoading && !aiBindingError && !aiBinding" class="safe-note">没有模型绑定：请在项目设置中配置 Web 用例生成任务，页面不会自动创建模型配置。</span>
            <el-alert v-if="aiSuggestionsError" :title="aiSuggestionsError" type="error" :closable="false" show-icon class="section-gap" />
            <div v-if="!aiSuggestionsLoading && !aiSuggestions.length && !aiSuggestionsError" class="section-gap"><el-empty description="当前录制还没有 AI 整理建议" :image-size="50" /></div>
            <div v-if="aiSuggestions.length" class="ai-suggestion-history section-gap"><div class="section-heading"><div><strong>建议历史</strong><p>只展示状态与审计元数据；不会展示原始响应或来源快照。</p></div></div><div class="suggestion-history-list"><button v-for="(suggestion, suggestionIndex) in aiSuggestionPages.items.value" :key="suggestion.id" class="suggestion-history-item" :class="{ active: suggestion.id === selectedAiSuggestion?.id }" @click="selectAiSuggestion(suggestion)"><span><strong>第 {{ aiSuggestions.length - ((aiSuggestionPages.page.value - 1) * aiSuggestionPages.pageSize.value + suggestionIndex) }} 次建议</strong><small>{{ dateLabel(suggestion.created_at) }}</small></span><el-tag size="small" :type="aiStatusType(suggestion.status)">{{ aiStatusLabel(suggestion.status) }}</el-tag></button></div><el-pagination v-if="aiSuggestionPages.total.value" class="records-pagination compact" small layout="total, sizes, prev, pager, next" :current-page="aiSuggestionPages.page.value" :page-size="aiSuggestionPages.pageSize.value" :page-sizes="[...RECORD_PAGE_SIZES]" :total="aiSuggestionPages.total.value" @current-change="aiSuggestionPages.changePage" @size-change="aiSuggestionPages.changePageSize" /></div>

            <template v-if="selectedAiSuggestion">
              <div class="ai-audit-grid section-gap"><div><span>实际模型</span><strong>{{ selectedAiSuggestion.actual_model || '—' }}</strong></div><div><span>Prompt</span><strong>已锁定生成版本</strong></div><div><span>输出结构</span><strong>{{ selectedAiSuggestion.output_schema_id ? '已校验' : '未绑定' }}</strong></div><div><span>备用模型 / 输出修复</span><strong>{{ selectedAiSuggestion.fallback_used ? '已切换' : '未切换' }} / {{ selectedAiSuggestion.repair_used ? '已修复' : '未修复' }}</strong></div><div><span>来源快照</span><strong>{{ shortHash(selectedAiSuggestion.source_snapshot_sha256) }} · {{ byteSizeLabel(selectedAiSuggestion.source_snapshot_size) }}</strong></div><div><span>状态</span><strong><el-tag size="small" :type="aiStatusType(selectedAiSuggestion.status)">{{ aiStatusLabel(selectedAiSuggestion.status) }}</el-tag></strong></div></div>
              <div v-if="selectedAiSuggestion.structured_result" class="ai-preview-grid section-gap"><el-card shadow="never"><template #header><strong>确定性整理基线</strong></template><div class="preview-block"><strong>步骤 {{ deterministicBaseline?.natural_language_steps.length ?? 0 }} 项</strong><span v-for="(step, index) in deterministicBaseline?.natural_language_steps ?? []" :key="`baseline-step-${index}`">{{ index + 1 }}. {{ step }}</span></div><div class="preview-block"><strong>操作 {{ deterministicBaseline?.actions.length ?? 0 }} 项</strong><span v-for="(action, index) in deterministicBaseline?.actions ?? []" :key="`baseline-action-${index}`">{{ index + 1 }}. {{ aiActionSummary(action) }}</span></div><div class="preview-block"><strong>断言 {{ deterministicBaseline?.assertions.length ?? 0 }} 项</strong><span v-for="(assertion, index) in deterministicBaseline?.assertions ?? []" :key="`baseline-assertion-${index}`">{{ index + 1 }}. {{ aiAssertionSummary(assertion) }}</span></div></el-card><el-card shadow="never"><template #header><strong>AI 候选摘要</strong></template><p class="preview-summary">{{ selectedAiSuggestion.structured_result.summary }}</p><p class="preview-diff">差异摘要：{{ aiDiffSummary(selectedAiSuggestion) }}</p><div class="preview-block"><strong>建议步骤 {{ selectedAiSuggestion.structured_result.steps.length }} 项</strong><span v-for="step in selectedAiSuggestion.structured_result.steps" :key="`ai-step-${step.source_event_sequence}`">事件 #{{ step.source_event_sequence }} · {{ step.action }} · {{ step.natural_language_step }}{{ step.locator ? ` · 定位器：${step.locator.strategy}=${step.locator.value}` : '' }}{{ step.element_name ? ` · 元素：${step.element_name}` : '' }}{{ step.reason ? `（${step.reason}）` : '' }}</span></div><div class="preview-block"><strong>建议断言 {{ selectedAiSuggestion.structured_result.assertions.length }} 项</strong><span v-for="assertion in selectedAiSuggestion.structured_result.assertions" :key="`ai-assertion-${assertion.source_event_sequence}`">事件 #{{ assertion.source_event_sequence }} · {{ aiAssertionSummary(assertion.assertion) }}{{ assertion.reason ? `（${assertion.reason}）` : '' }}</span></div><div v-if="selectedAiSuggestion.structured_result.warnings.length" class="preview-block warning-block"><strong>警告</strong><span v-for="warning in selectedAiSuggestion.structured_result.warnings" :key="warning">{{ warning }}</span></div></el-card></div>
              <div v-if="selectedAiSuggestion.status === 'DRAFT' && aiContentDraft" class="ai-content-editor section-gap">
                <div class="section-heading"><div><h4>人工编辑 AI 候选 WebCaseContent</h4><p>未修改时接受请求省略 content，使用后端 canonical；修改后会发送当前安全内容，并恢复录制的 Session Profile 引用。</p></div><div class="detail-actions"><el-button type="success" data-testid="open-ai-accept" :disabled="!props.canWrite" @click="openAiAcceptDialog(selectedAiSuggestion)">接受并生成 DRAFT</el-button><el-button type="danger" data-testid="open-ai-reject" plain :disabled="!props.canWrite" @click="openAiRejectDialog(selectedAiSuggestion)">拒绝建议</el-button></div></div>
                <el-form label-position="top" class="ai-content-basic"><el-form-item label="起始 URL"><el-input v-model="aiContentDraft.start_url" data-testid="ai-content-start-url" @update:model-value="markAiContentEdited" /></el-form-item><el-form-item label="内容状态"><span class="safe-note">{{ aiContentEdited ? '已编辑：接受时会携带 content' : '未编辑：接受时使用后端 canonical' }}</span></el-form-item></el-form>
                <div class="ai-draft-section"><div class="section-heading"><div><strong>自然语言步骤</strong></div><el-button text :icon="Plus" @click="aiContentDraft.natural_language_steps.push(''); markAiContentEdited()">添加</el-button></div><div v-for="(step, index) in aiContentDraft.natural_language_steps" :key="`ai-nl-${index}`" class="natural-step-row"><span class="step-number">{{ index + 1 }}</span><el-input v-model="aiContentDraft.natural_language_steps[index]" maxlength="2000" @update:model-value="markAiContentEdited" /><el-button link type="danger" :icon="Delete" @click="aiContentDraft.natural_language_steps.splice(index, 1); markAiContentEdited()">删除</el-button></div><el-empty v-if="!aiContentDraft.natural_language_steps.length" description="暂无自然语言步骤" :image-size="40" /></div>
                <div class="ai-draft-section">
                  <div class="section-heading"><div><strong>结构化操作</strong></div><el-button text :icon="Plus" @click="aiAddAction">添加</el-button></div>
                  <div v-for="(action, index) in aiContentDraft.actions" :key="`ai-action-${index}`" class="dsl-row" :data-testid="`ai-action-row-${index}`">
                    <div class="dsl-row-heading"><span class="step-number">{{ index + 1 }}</span><el-select :model-value="action.type" :data-testid="`ai-action-type-${index}`" style="width: 205px" @update:model-value="aiReplaceAction(index, $event)"><el-option v-for="type in aiActionTypes" :key="type" :label="type" :value="type" /></el-select><span class="readable-step">{{ aiActionSummary(action) }}</span><el-button link type="danger" :icon="Delete" @click="aiRemoveAction(index)">删除</el-button></div>
                    <div class="dsl-fields">
                      <el-input v-if="action.type === 'GOTO' || action.type === 'WAIT_URL' || action.type === 'NEW_TAB'" v-model="action.url" placeholder="URL 或运行时模板" @update:model-value="markAiContentEdited" />
                      <template v-if="action.type === 'NEW_TAB' || action.type === 'SWITCH_TAB' || action.type === 'CLOSE_TAB'">
                        <el-input v-model="action.value" :data-testid="`ai-action-tab-alias-${index}`" placeholder="标签页别名" :title="webTabAliasHelp" @update:model-value="markAiContentEdited"><template #prepend>标签页别名</template></el-input>
                        <el-text type="info" size="small">{{ webTabAliasHelp }}</el-text>
                      </template>
                      <el-input v-if="action.type === 'FILL' || action.type === 'SELECT'" v-model="action.value" placeholder="值" @update:model-value="markAiContentEdited" />
                      <el-input v-if="action.type === 'PRESS'" v-model="action.key" placeholder="按键" @update:model-value="markAiContentEdited" />
                      <template v-if="aiHasLocator(action)">
                        <el-tag v-if="aiGetLocator(action)?.element_version_id" type="info">引用 Element Version #{{ aiGetLocator(action)?.element_version_id }}</el-tag>
                        <template v-else><el-select :model-value="aiGetLocator(action)?.strategy ?? undefined" :data-testid="`ai-action-locator-strategy-${index}`" placeholder="策略" style="width: 135px" @update:model-value="aiUpdateLocatorStrategy(action, $event)"><el-option v-for="strategy in locatorStrategies" :key="strategy" :label="strategy" :value="strategy" /></el-select><el-input :model-value="aiGetLocator(action)?.value ?? ''" :data-testid="`ai-action-locator-value-${index}`" placeholder="Locator 值" @update:model-value="aiUpdateLocatorValue(action, $event)" /></template>
                      </template>
                      <el-input-number v-model="action.timeout_ms" :data-testid="`ai-action-timeout-${index}`" :min="100" :max="600000" controls-position="right" @change="markAiContentEdited" />
                      <el-select v-model="action.failure_policy" :data-testid="`ai-action-policy-${index}`" style="width: 120px" @change="markAiContentEdited"><el-option label="失败停止" value="STOP" /><el-option label="继续" value="CONTINUE" /></el-select>
                    </div>
                  </div>
                </div>
                <div class="ai-draft-section"><div class="section-heading"><div><strong>结构化断言</strong></div><el-button text :icon="Plus" @click="aiAddAssertion">添加</el-button></div><div v-for="(assertion, index) in aiContentDraft.assertions" :key="`ai-assertion-${index}`" class="dsl-row" :data-testid="`ai-assertion-row-${index}`"><div class="dsl-row-heading"><span class="step-number">{{ index + 1 }}</span><el-select :model-value="assertion.type" :data-testid="`ai-assertion-type-${index}`" style="width: 290px" @update:model-value="aiReplaceAssertion(index, $event)"><el-option v-for="type in aiAssertionTypes" :key="type" :label="webAssertionTypeLabel(type)" :value="type" /></el-select><span class="readable-step">{{ aiAssertionSummary(assertion) }}</span><el-button link type="danger" :icon="Delete" @click="aiRemoveAssertion(index)">删除</el-button></div><div class="dsl-fields"><el-input v-if="hasWebAssertionExpected(assertion)" v-model="assertion.expected" :data-testid="`ai-assertion-expected-${index}`" :placeholder="webAssertionExpectedPlaceholder(assertion)" @update:model-value="markAiContentEdited" /><template v-if="aiHasLocator(assertion)"><el-tag v-if="aiGetLocator(assertion)?.element_version_id" type="info">引用元素版本 #{{ aiGetLocator(assertion)?.element_version_id }}</el-tag><template v-else><el-select :model-value="aiGetLocator(assertion)?.strategy ?? undefined" :data-testid="`ai-assertion-locator-strategy-${index}`" placeholder="策略" style="width: 135px" @update:model-value="aiUpdateLocatorStrategy(assertion, $event)"><el-option v-for="strategy in locatorStrategies" :key="strategy" :label="strategy" :value="strategy" /></el-select><el-input :model-value="aiGetLocator(assertion)?.value ?? ''" :data-testid="`ai-assertion-locator-value-${index}`" placeholder="定位器值" @update:model-value="aiUpdateLocatorValue(assertion, $event)" /></template></template><el-input-number v-model="assertion.timeout_ms" :data-testid="`ai-assertion-timeout-${index}`" :min="100" :max="600000" controls-position="right" @change="markAiContentEdited" /></div></div><el-empty v-if="!aiContentDraft.assertions.length" description="暂无断言" :image-size="40" /></div>
              </div>
            </template>
          </section>
        </template>
        <el-empty v-else-if="!detailLoading" description="请选择一条录制查看详情" :image-size="60" />
      </div>
    </el-drawer>

    <el-dialog v-model="confirmVisible" title="确认生成 Web 用例" width="620px">
      <el-alert title="生成结果为草稿" description="系统只会创建或追加一个草稿版本，不会自动批准或直接进入运行中心。请在资产页检查事件和定位器后再批准。" type="warning" :closable="false" show-icon />
      <el-radio-group v-model="confirmTarget" class="confirm-target section-gap"><el-radio value="new">创建新 Web 用例</el-radio><el-radio value="existing">追加到已有 Web 用例</el-radio></el-radio-group>
      <el-form label-position="top"><el-form-item v-if="confirmTarget === 'new'" label="新 Web 用例名称"><el-input v-model="confirmName" maxlength="255" placeholder="例如 checkout_recorded，也支持中文和空格" /></el-form-item><el-form-item v-else label="已有 Web 用例"><el-select v-model="confirmWebCaseId" filterable placeholder="选择当前项目的 Web 用例" class="full-width"><el-option v-for="item in activeWebCases" :key="item.id" :label="`${item.name}（${item.status === 'APPROVED' ? '已批准' : '草稿'}）`" :value="item.id" /></el-select></el-form-item></el-form>
      <template #footer><el-button @click="confirmVisible = false">取消</el-button><el-button type="primary" :loading="confirmSaving" :disabled="!props.canWrite" @click="confirmRecording">确认生成草稿</el-button></template>
    </el-dialog>

    <el-dialog v-model="aiAcceptVisible" title="接受 AI 整理建议" width="620px">
      <el-alert title="只生成草稿" description="接受建议只会创建或追加 Web 用例草稿，不会自动批准或进入运行中心；请在资产页检查后再批准。" type="warning" :closable="false" show-icon />
      <el-radio-group v-model="aiAcceptTarget" class="confirm-target section-gap"><el-radio value="new">创建新 Web 用例</el-radio><el-radio value="existing">追加到已有 Web 用例</el-radio></el-radio-group>
      <el-form label-position="top">
        <el-form-item v-if="aiAcceptTarget === 'new'" label="新 Web 用例名称"><el-input v-model="aiAcceptName" maxlength="255" placeholder="例如 checkout_ai_draft" /></el-form-item>
        <el-form-item v-else label="已有 Web 用例"><el-select v-model="aiAcceptWebCaseId" filterable placeholder="选择当前项目的 Web 用例" class="full-width"><el-option v-for="item in activeWebCases" :key="item.id" :label="`${item.name}（${item.status === 'APPROVED' ? '已批准' : '草稿'}）`" :value="item.id" /></el-select></el-form-item>
        <el-form-item label="决策说明（可选）"><el-input v-model="aiDecisionNote" type="textarea" :rows="3" maxlength="500" show-word-limit placeholder="记录接受建议的原因，不要填写凭据或原始响应" /></el-form-item>
      </el-form>
      <p class="safe-note">未编辑候选内容时，接受请求会使用后端 canonical 建议；编辑过则提交当前安全内容。</p>
      <template #footer><el-button @click="aiAcceptVisible = false">取消</el-button><el-button type="success" data-testid="confirm-ai-accept" :loading="aiAcceptSaving" :disabled="!props.canWrite" @click="acceptAiSuggestion">接受并生成草稿</el-button></template>
    </el-dialog>

    <el-dialog v-model="aiRejectVisible" title="拒绝 AI 整理建议" width="560px">
      <el-alert title="拒绝后不可接受该建议" description="拒绝只记录人工决定，不会修改现有 Web 用例；之后可以重新生成新的建议。" type="warning" :closable="false" show-icon />
      <el-form label-position="top" class="section-gap"><el-form-item label="拒绝说明（可选）"><el-input v-model="aiRejectDecisionNote" type="textarea" :rows="4" maxlength="500" show-word-limit placeholder="说明建议不适用的原因，不要填写凭据或原始响应" /></el-form-item></el-form>
      <template #footer><el-button @click="aiRejectVisible = false">取消</el-button><el-button type="danger" data-testid="confirm-ai-reject" :loading="aiRejectSaving" :disabled="!props.canWrite" @click="rejectAiSuggestion">确认拒绝</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.recording-workbench{min-width:0}.recording-create-grid{display:grid;grid-template-columns:minmax(300px,.9fr) minmax(480px,1.5fr);gap:18px}.recording-config-card,.recording-list-card{min-width:0}.full-width{width:100%}.section-gap{margin-top:14px}.section-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.section-heading strong,.section-heading h3,.section-heading h4{color:var(--text-primary)}.section-heading h3{margin:0;font-size:16px}.section-heading h4{margin:0;font-size:15px}.section-heading p{margin:4px 0 0;color:var(--text-secondary);font-size:12px}.safe-note{display:block;margin-top:5px;color:var(--text-secondary);font-size:12px}.error-note{color:var(--el-color-danger)}.table-secondary{display:block;overflow:hidden;color:var(--text-secondary);font-size:11px;text-overflow:ellipsis;white-space:nowrap}.pagination-row{display:flex;align-items:center;justify-content:center;gap:12px;margin-top:14px;color:var(--text-secondary);font-size:12px}.detail-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:18px}.detail-heading h2{margin:0;color:var(--text-primary);font-size:20px;word-break:break-all}.detail-heading p{margin:6px 0 0;color:var(--text-secondary);word-break:break-all}.detail-actions{display:flex;align-items:center;justify-content:flex-end;gap:8px;flex-wrap:wrap}.event-editor{padding-top:8px}.event-card{margin-top:12px;padding:14px;border:1px solid var(--border-color);border-radius:10px}.event-card-heading{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.step-number{display:grid;place-items:center;width:24px;height:24px;border-radius:50%;background:var(--surface-subtle);color:var(--text-secondary);font-size:12px}.event-time{margin-right:auto;color:var(--text-secondary);font-size:12px}.event-fields{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px}.locator-editor{margin-top:14px}.locator-row{display:flex;align-items:center;gap:8px;margin-top:10px}.locator-row>.el-input{min-width:180px;flex:1}.confirm-target{display:flex}.profile-form{display:grid;grid-template-columns:1fr 1fr;column-gap:14px}.expiry-warning{color:var(--el-color-danger)}.ai-suggestion-section{padding:16px;border:1px solid var(--border-color);border-radius:10px}.ai-generate-form{display:grid;grid-template-columns:minmax(220px,1fr) minmax(280px,2fr) auto;gap:10px;align-items:start}.ai-prompt-select{width:100%}.suggestion-history-list{display:flex;gap:8px;flex-wrap:wrap}.suggestion-history-item{display:flex;align-items:center;gap:12px;padding:8px 12px;border:1px solid var(--border-color);border-radius:8px;background:transparent;color:inherit;cursor:pointer}.suggestion-history-item:hover,.suggestion-history-item.active{border-color:var(--el-color-primary);background:var(--surface-subtle)}.suggestion-history-item span{display:flex;gap:8px;align-items:baseline}.suggestion-history-item small{color:var(--text-secondary);font-size:11px}.ai-audit-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.ai-audit-grid>div{display:flex;flex-direction:column;gap:4px;padding:10px;border-radius:8px;background:var(--surface-subtle)}.ai-audit-grid span{color:var(--text-secondary);font-size:12px}.ai-audit-grid strong{color:var(--text-primary);font-size:13px;word-break:break-all}.ai-preview-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.preview-block{display:flex;flex-direction:column;gap:6px;margin-top:12px;color:var(--text-secondary);font-size:12px;white-space:pre-wrap}.preview-block strong{color:var(--text-primary)}.preview-summary{margin:0;color:var(--text-secondary);white-space:pre-wrap}.warning-block{padding:10px;border-radius:8px;background:var(--el-color-warning-light-9)}.ai-content-editor{padding-top:14px;border-top:1px dashed var(--border-color)}.ai-content-basic{display:grid;grid-template-columns:2fr 1fr;gap:12px}.ai-content-basic .el-form-item{margin-bottom:0}.ai-draft-section{margin-top:18px}.dsl-row{margin-top:10px;padding:12px;border:1px solid var(--border-color);border-radius:9px}.dsl-row-heading{display:flex;align-items:center;gap:8px;margin-bottom:10px}.readable-step{flex:1;overflow:hidden;color:var(--text-secondary);font-size:13px;text-overflow:ellipsis;white-space:nowrap}.dsl-fields{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.dsl-fields>.el-input{min-width:180px;flex:1}.confirm-target{display:flex}.profile-form{display:grid;grid-template-columns:1fr 1fr;column-gap:14px}.expiry-warning{color:var(--el-color-danger)}
.preview-diff{margin:8px 0 0;color:var(--text-secondary);font-size:12px}@media(max-width:1050px){.recording-create-grid{grid-template-columns:1fr}}@media(max-width:680px){.detail-heading,.section-heading{align-items:flex-start;flex-direction:column}.detail-actions{justify-content:flex-start}.event-fields{grid-template-columns:1fr}.locator-row{align-items:stretch;flex-wrap:wrap}.locator-row>.el-input{min-width:100%}}
</style>
