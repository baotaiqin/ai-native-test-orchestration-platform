<script setup lang="ts">
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CircleClose, Close, Refresh, VideoPlay, View } from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'

import { getModelBindings } from '@/api/model-center'
import { getOutputSchemas } from '@/api/ai-infrastructure'
import { downloadEvidence as downloadEvidenceBlob, getEvidence } from '@/api/evidence'
import { getEnvironments } from '@/api/environments'
import {
  cancelRun,
  dispatchRun,
  dispatchRunsBatch,
  createRun,
  forceStopRun,
  getRun,
  getRuns,
  validateRun,
} from '@/api/runs'
import {
  consumeRunEventStream,
  RunEventStreamHttpError,
} from '@/api/run-events'
import { getProjects } from '@/api/projects'
import { getPrompts } from '@/api/prompt-center'
import { apiDateTimeMs, formatApiDateTime } from '@/utils/datetime'
import { promptOptionLabel } from '@/utils/prompt-display'
import { getScenarios, getScenarioVersions } from '@/api/scenarios'
import { getTestCase, getTestCases } from '@/api/test-cases'
import { getRunners } from '@/api/runners'
import { getSessionProfiles, getWebCase, getWebCaseVersions, getWebCases } from '@/api/web-assets'
import {
  acceptWebHealingProposal,
  generateWebHealingProposal,
  getWebHealingProposals,
  rejectWebHealingProposal,
  validateWebHealingProposal,
  WEB_HEALING_GENERATION_TIMEOUT_MS,
} from '@/api/web-healing'
import {
  generateWebFailureAnalysis,
  getWebFailureAnalyses,
  WEB_FAILURE_ANALYSIS_GENERATION_TIMEOUT_MS,
} from '@/api/web-failure-analysis'
import { useAuthStore } from '@/stores/auth'
import type { OutputSchema } from '@/types/ai-infrastructure'
import type { Environment } from '@/types/environment'
import type { ProjectModelBinding } from '@/types/model-center'
import type { PromptDefinition } from '@/types/prompt-center'
import type { EvidenceArtifact, EvidenceArtifactType } from '@/types/evidence'
import type { Project } from '@/types/project'
import type { Runner, RunnerCapabilityName, RunnerSlotType } from '@/types/runner'
import type { Scenario, ScenarioVersion } from '@/types/scenario'
import type { TestCaseAsset } from '@/types/test-case'
import type {
  SessionProfileResponse,
  WebCaseDetailResponse,
  WebCaseResponse,
  WebCaseVersionResponse,
  WebExecutionTrace,
  WebLocatorAttempt,
} from '@/types/web'
import type { RunEventResponse } from '@/types/run-event'
import type {
  CaseRun,
  Run,
  RunCreateRequest,
  RunDetail,
  RunNodeStatus,
  RunStatus,
  RunType,
  RunValidationResponse,
  StepRun,
} from '@/types/run'
import type {
  HealingLocator,
  HealingAnalysisStage,
  HealingCandidateLocator,
  HealingOldLocator,
  HealingSourceLocator,
  WebHealingProposalResponse,
} from '@/types/web-healing'
import type {
  WebFailureAnalysisCategory,
  WebFailureAnalysisResponse,
  WebFailureAnalysisResult,
  WebFailureAnalysisSeverity,
} from '@/types/web-failure-analysis'
import {
  apiStepMaxRetries,
  retryAwareApiErrorMessage,
  v1RetryPolicyError,
} from '@/utils/v1-retry-policy'

const POLL_INTERVAL_MS = 8_000
const runPageSize = ref(10)
const STREAM_MAX_RECONNECT_ATTEMPTS = 5
const STREAM_MAX_RETRY_DELAY_MS = 15_000
const REALTIME_REFRESH_DEBOUNCE_MS = 300

interface HealingTraceSelection {
  key: string
  run: RunDetail
  caseRun: CaseRun
  trace: WebExecutionTrace
}

interface RunDeepLink {
  projectId: number
  runId: string
}

interface BatchCreateFailure {
  caseId: number
  caseLabel: string
  message: string
}

interface BatchCreateSummary {
  requested: number
  created: number
  failures: BatchCreateFailure[]
}

const projects = ref<Project[]>([])
const projectsLoading = ref(false)
const projectError = ref<string | null>(null)
const projectId = ref<number | undefined>()

const environments = ref<Environment[]>([])
const apiCases = ref<TestCaseAsset[]>([])
const selectedApiCaseDetail = ref<TestCaseAsset | null>(null)
const selectedApiCaseDetailLoading = ref(false)
const selectedApiCaseDetailError = ref<string | null>(null)
const scenarios = ref<Scenario[]>([])
const webCases = ref<WebCaseResponse[]>([])
const webCaseDetail = ref<WebCaseDetailResponse | null>(null)
const webCaseVersions = ref<WebCaseVersionResponse[]>([])
const webCaseVersionsLoading = ref(false)
const webCaseVersionsError = ref<string | null>(null)
const sessionProfiles = ref<SessionProfileResponse[]>([])
const runners = ref<Runner[]>([])
const resourcesLoading = ref(false)
const resourcesError = ref<string | null>(null)

const selectedEnvironmentId = ref<number | undefined>()
const selectedCaseId = ref<number | undefined>()
const selectedApiCaseIds = ref<number[]>([])
const selectedScenarioId = ref<number | undefined>()
const selectedScenarioVersionId = ref<number | undefined>()
const scenarioVersions = ref<ScenarioVersion[]>([])
const scenarioVersionsLoading = ref(false)
const scenarioVersionsError = ref<string | null>(null)
const selectedWebCaseId = ref<number | undefined>()
const selectedWebCaseVersionId = ref<number | undefined>()
const runType = ref<RunType>('API_CASE')
const selectedRunnerId = ref<string | undefined>()
const validationLoading = ref(false)
const createLoading = ref(false)
const validationResult = ref<RunValidationResponse | null>(null)
const createdRun = ref<RunDetail | null>(null)
const batchCreateSummary = ref<BatchCreateSummary | null>(null)

const runs = ref<Run[]>([])
const runPage = ref(1)
const runTotal = ref(0)
const runListLoading = ref(false)
const runListInFlight = ref(false)
const runListError = ref<string | null>(null)

const detailVisible = ref(false)
const detailRun = ref<RunDetail | null>(null)
const detailLoading = ref(false)
const detailInFlight = ref(false)
const detailError = ref<string | null>(null)
const detailTargetRunId = ref<string | null>(null)
const dispatchingRunId = ref<string | null>(null)
const batchDispatching = ref(false)
const cancellingRunId = ref<string | null>(null)
const forceStoppingRunId = ref<string | null>(null)
const totalTimeoutMsInput = ref<number | null>(null)
const dispatchMessage = ref<string | null>(null)
const evidenceItems = ref<EvidenceArtifact[]>([])
const evidenceLoading = ref(false)
const evidenceInFlight = ref(false)
const evidenceError = ref<string | null>(null)
const evidenceDownloadId = ref<string | null>(null)

const selectedHealingTraceKey = ref<string | null>(null)
const healingPrompts = ref<PromptDefinition[]>([])
const healingOutputSchemas = ref<OutputSchema[]>([])
const healingBindings = ref<ProjectModelBinding[]>([])
const healingPromptLoading = ref(false)
const healingSchemaLoading = ref(false)
const healingBindingLoading = ref(false)
const healingPromptError = ref<string | null>(null)
const healingSchemaError = ref<string | null>(null)
const healingBindingError = ref<string | null>(null)
const selectedHealingPromptId = ref<number | undefined>()
const healingAdditionalInstructions = ref('')
const selectedHealingAnalysisStage = ref<HealingAnalysisStage>('LLM_DOM')
const selectedHealingScreenshotId = ref<string | undefined>()
const healingProposals = ref<WebHealingProposalResponse[]>([])
const healingProposalsLoading = ref(false)
const healingProposalsError = ref<string | null>(null)
const healingGenerationTimeoutNotice = ref<string | null>(null)
const selectedHealingProposalId = ref<number | null>(null)
const selectedHealingCandidateKey = ref<string | undefined>()
const healingGenerating = ref(false)
const healingValidating = ref(false)
const healingDecisionVisible = ref(false)
const healingDecisionMode = ref<'accept' | 'reject'>('accept')
const healingDecisionSaving = ref(false)
const healingDecisionNote = ref('')
const healingVersionLoading = ref(false)

const failureAnalysisCaseRunId = ref<number | null>(null)
const failureAnalysisPrompts = ref<PromptDefinition[]>([])
const failureAnalysisOutputSchemas = ref<OutputSchema[]>([])
const failureAnalysisBindings = ref<ProjectModelBinding[]>([])
const failureAnalysisPromptLoading = ref(false)
const failureAnalysisSchemaLoading = ref(false)
const failureAnalysisBindingLoading = ref(false)
const failureAnalysisPromptError = ref<string | null>(null)
const failureAnalysisSchemaError = ref<string | null>(null)
const failureAnalysisBindingError = ref<string | null>(null)
const selectedFailureAnalysisPromptId = ref<number | undefined>()
const failureAnalysisAdditionalInstructions = ref('')
const failureAnalysisItems = ref<WebFailureAnalysisResponse[]>([])
const failureAnalysisSelectedId = ref<number | null>(null)
const failureAnalysisHistoryLoading = ref(false)
const failureAnalysisGenerating = ref(false)
const failureAnalysisHistoryError = ref<string | null>(null)
const failureAnalysisGenerationTimeoutNotice = ref<string | null>(null)

const runDeepLinkMessage = ref<string | null>(null)

type RunEventConnectionState = 'DISCONNECTED' | 'CONNECTING' | 'CONNECTED' | 'RECONNECTING' | 'POLLING_FALLBACK'

const streamState = ref<RunEventConnectionState>('DISCONNECTED')
const streamErrorMessage = ref<string | null>(null)
const streamLastEventAt = ref<string | null>(null)
const streamLastEventId = ref<string | null>(null)
const streamController = ref<AbortController | null>(null)

let projectLoadSequence = 0
let loadedProjectId: number | null = null
let projectDataActiveRequests = 0
let scenarioVersionLoadSequence = 0
let webCaseVersionLoadSequence = 0
let apiCaseDetailLoadSequence = 0
let apiCaseDetailInFlightKey: string | null = null
let runCreationSequence = 0
let runCreationInFlightSequence: number | null = null
let detailRequestSequence = 0
let detailInFlightSequence: number | null = null
let evidenceRequestSequence = 0
let evidenceInFlightSequence: number | null = null
let streamSequence = 0
let streamTargetRunId: string | null = null
let streamTargetProjectId: number | null = null
let streamReconnectAttempt = 0
let streamRetryTimer: number | null = null
let realtimeRefreshTimer: number | null = null
let pollTimer: number | null = null
let healingProposalsInFlightKey: string | null = null
let healingRequestSequence = 0
let healingVersionRequestSequence = 0
let failureAnalysisRequestSequence = 0
let failureAnalysisConfigInFlightKey: string | null = null
let failureAnalysisHistoryInFlightKey: string | null = null
let failureAnalysisConfigLoadedKey: string | null = null
let failureAnalysisHistoryLoadedKey: string | null = null
let pendingRunDeepLink: RunDeepLink | null = null
const pageActive = ref(true)
const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()

const currentProject = computed(() => projects.value.find((project) => project.id === projectId.value) ?? null)
const enabledEnvironments = computed(() => environments.value.filter((environment) => environment.enabled))
const activeApiCases = computed(() => apiCases.value.filter((testCase) => (
  testCase.status === 'ACTIVE' && testCase.case_type === 'API'
)))
const executableScenarios = computed(() => scenarios.value.filter((scenario) => scenario.status === 'APPROVED'))
const executableWebCases = computed(() => webCases.value.filter((webCase) => (
  webCase.status === 'APPROVED' && webCase.current_version_id !== null
)))
const selectedWebCase = computed(() => executableWebCases.value.find((webCase) => webCase.id === selectedWebCaseId.value) ?? null)
const approvedWebCaseVersions = computed(() => webCaseVersions.value.filter((version) => version.status === 'APPROVED'))
const selectedWebCaseVersion = computed(() => approvedWebCaseVersions.value.find((version) => version.id === selectedWebCaseVersionId.value) ?? null)
const selectedRunnerCapability = computed<RunnerCapabilityName>(() => runType.value === 'WEB_CASE' ? 'WEB' : 'API')
const selectedRunnerSlotType = computed<RunnerSlotType>(() => runType.value === 'WEB_CASE' ? 'WEB' : 'API')
const availableRunners = computed(() => runners.value.filter((runner) => isRunnerSelectable(
  runner,
  selectedRunnerCapability.value,
  selectedRunnerSlotType.value,
)))
const selectedCase = computed(() => activeApiCases.value.find((testCase) => testCase.id === selectedCaseId.value) ?? null)
const selectedApiCases = computed(() => {
  const selectedIds = new Set(selectedApiCaseIds.value)
  return activeApiCases.value.filter((testCase) => selectedIds.has(testCase.id))
})
const apiCaseRunBlockMessage = computed(() => {
  if (runType.value !== 'API_CASE' || !selectedCaseId.value) return null
  if (selectedApiCaseDetailLoading.value) return '正在读取当前 API Case Version，请稍候'
  if (selectedApiCaseDetailError.value) return selectedApiCaseDetailError.value
  const detail = selectedApiCaseDetail.value
  if (!detail || detail.id !== selectedCaseId.value) return '尚未读取当前 API 用例版本，暂不能创建新运行'
  if (!detail.current_version) return '当前 API 用例没有可读取的当前版本，暂不能创建新运行'
  const retryError = v1RetryPolicyError(detail.current_version.content)
  if (!retryError) return null
  const value = apiStepMaxRetries(detail.current_version.content)
  return `${retryError}。当前 V${detail.current_version.version_no} 的值为 ${String(value)}，不能用于创建新运行；请前往测试用例显式选择 0/1 并创建新版本。`
})
const selectedScenario = computed(() => executableScenarios.value.find((scenario) => scenario.id === selectedScenarioId.value) ?? null)
const selectedRunner = computed(() => availableRunners.value.find((runner) => runner.id === selectedRunnerId.value) ?? null)
const streamStatusLabel = computed(() => {
  const labels: Record<RunEventConnectionState, string> = {
    DISCONNECTED: '实时连接未建立',
    CONNECTING: '实时连接中',
    CONNECTED: '实时已连接',
    RECONNECTING: '实时连接中断，正在重连',
    POLLING_FALLBACK: '实时不可用，轮询降级',
  }
  return labels[streamState.value]
})
const healingSelection = computed<HealingTraceSelection | null>(() => {
  const run = detailRun.value
  if (!run || run.run_type !== 'WEB_CASE' || run.status !== 'FAILED' || !selectedHealingTraceKey.value) return null
  for (const trace of webTracesOf(run)) {
    if (!isHealingTraceEligible(trace)) continue
    const caseRun = caseRunForTrace(run, trace)
    if (!caseRun) continue
    const key = healingTraceSelectionKey(run, caseRun, trace)
    if (key === selectedHealingTraceKey.value) return { key, run, caseRun, trace }
  }
  return null
})
const availableHealingPrompts = computed(() => {
  return healingPrompts.value.filter((prompt) => healingPromptIsUsable(prompt, healingOutputSchemas.value))
})
const healingBinding = computed(() => healingBindings.value.find((binding) => binding.task_type === 'LOCATOR_HEALING') ?? null)
const healingConfigLoading = computed(() => healingPromptLoading.value || healingSchemaLoading.value || healingBindingLoading.value)
const healingConfigReady = computed(() => availableHealingPrompts.value.length > 0 && healingBinding.value !== null)
const selectedHealingProposal = computed(() => (
  healingProposals.value.find((proposal) => proposal.id === selectedHealingProposalId.value)
  ?? healingProposals.value.find((proposal) => proposal.status === 'DRAFT')
  ?? healingProposals.value[0]
  ?? null
))
function healingCandidateKey(candidate: HealingCandidateLocator): string {
  return `${candidate.candidate_index}:${candidate.locator.strategy}:${candidate.locator.value}`
}
const selectedHealingCandidate = computed(() => {
  const proposal = selectedHealingProposal.value
  if (!proposal) return null
  return proposal.candidate_locators.find((candidate) => healingCandidateKey(candidate) === selectedHealingCandidateKey.value)
    ?? proposal.candidate_locators[0]
    ?? null
})
const selectedHealingCandidateValidated = computed(() => {
  const proposal = selectedHealingProposal.value
  const candidate = selectedHealingCandidate.value
  if (!proposal || !candidate) return false
  return proposal.validated_locators.some((locator) => (
    locator.strategy === candidate.locator.strategy && locator.value === candidate.locator.value
  ))
})
const healingValidationActive = computed(() => {
  const status = selectedHealingProposal.value?.latest_validation?.run_status
  return Boolean(status && ['CREATED', 'QUEUED', 'ASSIGNED', 'RUNNING', 'CANCELLING'].includes(status))
})
const healingScreenshotEvidence = computed(() => {
  const selection = healingSelection.value
  if (!selection) return []
  return evidenceItems.value.filter((item) => (
    item.run_id === selection.run.id
    && item.case_run_id === selection.caseRun.id
    && item.artifact_type === 'SCREENSHOT'
  ))
})
const healingUsesScreenshot = computed(() => selectedHealingAnalysisStage.value !== 'LLM_DOM')
const failureAnalysisVisible = computed(() => {
  const run = detailRun.value
  return Boolean(run && run.run_type === 'WEB_CASE' && (run.status === 'FAILED' || run.status === 'TIMEOUT'))
})
const failureAnalysisCaseRuns = computed(() => {
  if (!failureAnalysisVisible.value || !detailRun.value) return []
  return detailRun.value.case_runs.filter((caseRun) => caseRun.status === 'FAILED' || caseRun.status === 'TIMEOUT')
})
const failureAnalysisTargetCaseRun = computed(() => {
  const selected = failureAnalysisCaseRuns.value.find((caseRun) => caseRun.id === failureAnalysisCaseRunId.value)
  if (selected) return selected
  return failureAnalysisCaseRuns.value.length === 1 ? failureAnalysisCaseRuns.value[0] : null
})
const availableFailureAnalysisPrompts = computed(() => {
  return failureAnalysisPrompts.value.filter((prompt) => failureAnalysisPromptIsUsable(prompt, failureAnalysisOutputSchemas.value))
})
const failureAnalysisBinding = computed(() => failureAnalysisBindings.value.find((binding) => binding.task_type === 'WEB_FAILURE_ANALYSIS') ?? null)
const failureAnalysisConfigLoading = computed(() => (
  failureAnalysisPromptLoading.value || failureAnalysisSchemaLoading.value || failureAnalysisBindingLoading.value
))
const failureAnalysisConfigReady = computed(() => availableFailureAnalysisPrompts.value.length > 0 && failureAnalysisBinding.value !== null)
const selectedFailureAnalysis = computed(() => (
  failureAnalysisItems.value.find((item) => item.id === failureAnalysisSelectedId.value)
  ?? failureAnalysisItems.value[0]
  ?? null
))
const selectedFailureAnalysisResult = computed<WebFailureAnalysisResult | null>(() => {
  const analysis = selectedFailureAnalysis.value
  return analysis?.status === 'COMPLETED' ? analysis.structured_result : null
})

function commonRunPayload(): Omit<RunCreateRequest, 'run_type' | 'case_id' | 'case_version_id' | 'scenario_id' | 'scenario_version_id' | 'web_case_id' | 'web_case_version_id'> | null {
  if (!projectId.value || !selectedEnvironmentId.value || !selectedRunnerId.value) {
    return null
  }

  return {
    project_id: projectId.value,
    environment_id: selectedEnvironmentId.value,
    runner_id: selectedRunnerId.value,
    trigger_type: 'MANUAL',
    required_capabilities: [selectedRunnerCapability.value],
    required_tags: [],
    required_slot_type: selectedRunnerSlotType.value,
    required_slot_count: 1,
    total_timeout_ms: totalTimeoutMsInput.value ?? undefined,
  }
}

function apiCaseRunPayload(caseId: number): RunCreateRequest | null {
  const commonPayload = commonRunPayload()
  if (!commonPayload) return null
  return {
    ...commonPayload,
    run_type: 'API_CASE',
    case_id: caseId,
  }
}

const runPayload = computed<RunCreateRequest | null>(() => {
  const commonPayload = commonRunPayload()
  if (!commonPayload) return null

  if (runType.value === 'API_CASE') {
    const caseId = selectedApiCaseIds.value[0]
    if (!caseId || selectedApiCaseIds.value.length !== 1 || apiCaseRunBlockMessage.value) return null
    return apiCaseRunPayload(caseId)
  }

  if (runType.value === 'WEB_CASE') {
    if (!selectedWebCaseId.value || !selectedWebCaseVersionId.value) return null
    return {
      ...commonPayload,
      run_type: 'WEB_CASE',
      web_case_id: selectedWebCaseId.value,
      web_case_version_id: selectedWebCaseVersionId.value,
    }
  }

  if (!selectedScenarioId.value) return null
  return {
    ...commonPayload,
    run_type: 'SCENARIO',
    scenario_id: selectedScenarioId.value,
    ...(selectedScenarioVersionId.value ? { scenario_version_id: selectedScenarioVersionId.value } : {}),
  }
})

const canValidateAndCreate = computed(() => {
  if (runType.value !== 'API_CASE') return runPayload.value !== null
  if (selectedApiCaseIds.value.length === 0) return false
  if (selectedApiCaseIds.value.length === 1) return runPayload.value !== null
  return selectedApiCaseIds.value.every((caseId) => apiCaseRunPayload(caseId) !== null)
})

const runStatusLabels: Record<RunStatus, string> = {
  CREATED: '已创建',
  QUEUED: '排队中',
  ASSIGNED: '已分配',
  RUNNING: '运行中',
  CANCELLING: '取消中',
  SUCCESS: '成功',
  FAILED: '失败',
  CANCELLED: '已取消',
  TIMEOUT: '超时',
}

const nodeStatusLabels: Record<RunNodeStatus, string> = {
  CREATED: '已创建',
  ASSIGNED: '已分配',
  RUNNING: '运行中',
  CANCELLING: '取消中',
  SUCCESS: '成功',
  FAILED: '失败',
  REVIEW: '待复核',
  CANCELLED: '已取消',
  TIMEOUT: '超时',
  SKIPPED: '已跳过',
}

function safeErrorMessage(error: unknown, fallback: string): string {
  const message = retryAwareApiErrorMessage(error, fallback).trim()
  if (/credential|rabbitmq|amqp|routing[_ ]?key|message[_ ]?id|password|secret/i.test(message)) {
    return fallback
  }
  return message || fallback
}

function isAiGenerationRequestTimeout(error: unknown): boolean {
  const candidate = error as { code?: unknown; message?: unknown; response?: { status?: unknown } }
  if (candidate.code === 'ECONNABORTED' || candidate.code === 'ETIMEDOUT') return true
  if (candidate.response?.status === 408 || candidate.response?.status === 504) return true
  return typeof candidate.message === 'string' && /(?:timeout|timed out)/i.test(candidate.message)
}

function queryText(value: unknown): string | null {
  const candidate = Array.isArray(value) ? value[0] : value
  if (typeof candidate !== 'string') return null
  const text = candidate.trim()
  return text || null
}

function positiveQueryId(value: unknown): number | null {
  const text = queryText(value)
  if (!text || !/^[1-9]\d*$/.test(text)) return null
  const parsed = Number(text)
  return Number.isSafeInteger(parsed) ? parsed : null
}

function safeRunIdQuery(value: unknown): string | null {
  const text = queryText(value)
  if (!text || text.length > 128 || /[\r\n]/.test(text)) return null
  return text
}

function initializeRunDeepLink(): void {
  const hasProjectQuery = route.query.project_id !== undefined
  const hasRunQuery = route.query.run_id !== undefined
  const requestedProjectId = positiveQueryId(route.query.project_id)
  const requestedRunId = safeRunIdQuery(route.query.run_id)
  const messages: string[] = []

  if (hasProjectQuery && requestedProjectId === null) messages.push('返回原运行的项目参数无效，已使用默认项目。')
  if (hasRunQuery && requestedRunId === null) messages.push('返回原运行的标识无效，未自动打开运行详情。')
  if (requestedRunId && requestedProjectId === null) {
    messages.push('返回原运行缺少有效项目参数，未自动打开运行详情。')
  }

  pendingRunDeepLink = requestedRunId && requestedProjectId !== null
    ? { projectId: requestedProjectId, runId: requestedRunId }
    : null
  runDeepLinkMessage.value = messages.length ? messages.join(' ') : null
}

function apiErrorStatus(error: unknown): number | null {
  if (!error || typeof error !== 'object') return null
  const response = (error as { response?: unknown }).response
  if (!response || typeof response !== 'object') return null
  const status = (response as { status?: unknown }).status
  return typeof status === 'number' ? status : null
}

function safeRuntimeError(value: string | null | undefined, fallback: string): string | null {
  if (!value) return null
  if (/credential|rabbitmq|amqp|routing[_ ]?key|message[_ ]?id|password|secret/i.test(value)) {
    return fallback
  }
  return value
}

function invalidateDetailRequest(): void {
  detailRequestSequence += 1
  detailInFlightSequence = null
  detailInFlight.value = false
  detailLoading.value = false
}

function invalidateEvidenceRequest(): void {
  evidenceRequestSequence += 1
  evidenceInFlightSequence = null
  evidenceInFlight.value = false
  evidenceLoading.value = false
}

function invalidateScenarioVersionRequest(): void {
  scenarioVersionLoadSequence += 1
  scenarioVersionsLoading.value = false
  scenarioVersionsError.value = null
  scenarioVersions.value = []
  selectedScenarioVersionId.value = undefined
}

function invalidateWebCaseVersionRequest(): void {
  webCaseVersionLoadSequence += 1
  webCaseVersionsLoading.value = false
  webCaseVersionsError.value = null
  webCaseDetail.value = null
  webCaseVersions.value = []
  selectedWebCaseVersionId.value = undefined
}

function invalidateApiCaseDetailRequest(): void {
  apiCaseDetailLoadSequence += 1
  apiCaseDetailInFlightKey = null
  selectedApiCaseDetailLoading.value = false
  selectedApiCaseDetailError.value = null
  selectedApiCaseDetail.value = null
}

function invalidateRunCreationRequest(): void {
  runCreationSequence += 1
}

function healingTraceSelectionKey(run: RunDetail, caseRun: CaseRun, trace: WebExecutionTrace): string {
  return `${run.id}:${caseRun.id}:${trace.node_id}`
}

function caseRunForTrace(run: RunDetail, trace: WebExecutionTrace): CaseRun | null {
  return run.case_runs.find((caseRun) => caseRun.step_runs.some((stepRun) => stepRun.node_id === trace.node_id)) ?? null
}

function isHealingTraceEligible(trace: WebExecutionTrace): boolean {
  return trace.status === 'FAILED' && trace.healing_context !== undefined && trace.healing_context !== null
}

function invalidateHealingContext(): void {
  healingRequestSequence += 1
  healingVersionRequestSequence += 1
  healingProposalsInFlightKey = null
  selectedHealingTraceKey.value = null
  healingPrompts.value = []
  healingOutputSchemas.value = []
  healingBindings.value = []
  healingPromptLoading.value = false
  healingSchemaLoading.value = false
  healingBindingLoading.value = false
  healingPromptError.value = null
  healingSchemaError.value = null
  healingBindingError.value = null
  selectedHealingPromptId.value = undefined
  healingAdditionalInstructions.value = ''
  selectedHealingAnalysisStage.value = 'LLM_DOM'
  selectedHealingScreenshotId.value = undefined
  healingProposals.value = []
  healingProposalsLoading.value = false
  healingProposalsError.value = null
  healingGenerationTimeoutNotice.value = null
  selectedHealingProposalId.value = null
  selectedHealingCandidateKey.value = undefined
  healingGenerating.value = false
  healingValidating.value = false
  healingDecisionVisible.value = false
  healingDecisionSaving.value = false
  healingDecisionNote.value = ''
  healingVersionLoading.value = false
}

function failureAnalysisConfigKey(run: RunDetail): string {
  return `${run.project_id}:${run.id}`
}

function failureAnalysisHistoryKey(run: RunDetail, caseRun: CaseRun): string {
  return `${run.id}:${caseRun.id}`
}

function invalidateFailureAnalysisHistory(): void {
  failureAnalysisRequestSequence += 1
  failureAnalysisConfigInFlightKey = null
  failureAnalysisHistoryInFlightKey = null
  failureAnalysisHistoryLoadedKey = null
  failureAnalysisPromptLoading.value = false
  failureAnalysisSchemaLoading.value = false
  failureAnalysisBindingLoading.value = false
  failureAnalysisHistoryLoading.value = false
  failureAnalysisHistoryError.value = null
  failureAnalysisGenerationTimeoutNotice.value = null
  failureAnalysisItems.value = []
  failureAnalysisSelectedId.value = null
}

function invalidateFailureAnalysisContext(): void {
  failureAnalysisRequestSequence += 1
  failureAnalysisConfigInFlightKey = null
  failureAnalysisHistoryInFlightKey = null
  failureAnalysisConfigLoadedKey = null
  failureAnalysisHistoryLoadedKey = null
  failureAnalysisCaseRunId.value = null
  failureAnalysisPrompts.value = []
  failureAnalysisOutputSchemas.value = []
  failureAnalysisBindings.value = []
  failureAnalysisPromptLoading.value = false
  failureAnalysisSchemaLoading.value = false
  failureAnalysisBindingLoading.value = false
  failureAnalysisPromptError.value = null
  failureAnalysisSchemaError.value = null
  failureAnalysisBindingError.value = null
  selectedFailureAnalysisPromptId.value = undefined
  failureAnalysisAdditionalInstructions.value = ''
  failureAnalysisItems.value = []
  failureAnalysisSelectedId.value = null
  failureAnalysisHistoryLoading.value = false
  failureAnalysisGenerating.value = false
  failureAnalysisHistoryError.value = null
  failureAnalysisGenerationTimeoutNotice.value = null
}

function isCurrentHealingRequest(requestSequence: number, selectionKey: string): boolean {
  const selection = healingSelection.value
  return requestSequence === healingRequestSequence
    && pageActive.value
    && detailVisible.value
    && selection !== null
    && selection.key === selectionKey
    && projectId.value === selection.run.project_id
}

function healingPromptIsUsable(prompt: PromptDefinition, schemas: OutputSchema[]): boolean {
  const schemaId = prompt.current_version?.output_schema_id
  return prompt.enabled
    && prompt.current_version_id !== null
    && prompt.current_version !== null
    && schemaId !== null
    && schemas.some((schema) => schema.enabled && schema.id === schemaId)
}

function failureAnalysisPromptIsUsable(prompt: PromptDefinition, schemas: OutputSchema[]): boolean {
  const schemaId = prompt.current_version?.output_schema_id
  return prompt.enabled
    && prompt.current_version_id !== null
    && prompt.current_version !== null
    && schemaId !== null
    && schemas.some((schema) => schema.enabled && schema.id === schemaId)
}

function isCurrentFailureAnalysisRequest(
  requestSequence: number,
  runId: string,
  requestedProjectId: number,
  caseRunId?: number,
): boolean {
  return requestSequence === failureAnalysisRequestSequence
    && pageActive.value
    && detailVisible.value
    && detailTargetRunId.value === runId
    && detailRun.value?.id === runId
    && projectId.value === requestedProjectId
    && (caseRunId === undefined || failureAnalysisTargetCaseRun.value?.id === caseRunId)
}

function syncHealingCandidateSelection(proposal: WebHealingProposalResponse | null): void {
  if (!proposal) {
    selectedHealingCandidateKey.value = undefined
    return
  }
  const proposed = proposal.proposed_locator
  const matching = proposed
    ? proposal.candidate_locators.find((candidate) => (
      candidate.locator.strategy === proposed.strategy && candidate.locator.value === proposed.value
    ))
    : undefined
  const selected = matching ?? proposal.candidate_locators[0]
  selectedHealingCandidateKey.value = selected ? healingCandidateKey(selected) : undefined
}

function selectHealingProposal(proposal: WebHealingProposalResponse): void {
  selectedHealingProposalId.value = proposal.id
  syncHealingCandidateSelection(proposal)
}

function healingStatusLabel(status: WebHealingProposalResponse['status']): string {
  return status === 'DRAFT' ? '待人工审核' : status === 'ACCEPTED' ? '已接受' : '已拒绝'
}

function healingStatusType(status: WebHealingProposalResponse['status']): 'success' | 'warning' | 'info' {
  return status === 'ACCEPTED' ? 'success' : status === 'DRAFT' ? 'warning' : 'info'
}

function healingValueLabel(value: string): string {
  if (/(?:authorization|bearer|cookie|token|password|passwd|secret|credential|api[-_]?key)/i.test(value)) return '已隐藏'
  return value
}

function healingLocatorLabel(locator: HealingLocator | HealingSourceLocator | null | undefined): string {
  return locator ? `${locator.strategy} = ${healingValueLabel(locator.value)}` : '—'
}

function healingOldLocatorLabel(locator: HealingOldLocator): string {
  if ('element_version_id' in locator) {
    const locators = locator.locators.map((item) => (
      `${item.strategy} = ${healingValueLabel(item.value)}${item.priority ? ` · P${item.priority}` : ''}`
    )).join('；')
    return `元素版本 #${locator.element_version_id}${locators ? ` · 现有候选：${locators}` : ''}`
  }
  return healingLocatorLabel(locator)
}

function healingSchemaLabel(schemaId: number | null): string {
  if (schemaId === null) return '—'
  const schema = healingOutputSchemas.value.find((item) => item.id === schemaId)
  return schema ? `${schema.name}（#${schema.id} · V${schema.version_no}）` : `#${schemaId}`
}

function healingConfidenceLabel(confidence: number): string {
  return Number.isFinite(confidence) ? `${Math.round(confidence * 100)}%` : '—'
}

function healingShortHash(value: string): string {
  if (value.length <= 20) return value
  return `${value.slice(0, 12)}…${value.slice(-8)}`
}

function canOfferHealing(trace: WebExecutionTrace): boolean {
  const run = detailRun.value
  return Boolean(run && run.run_type === 'WEB_CASE' && run.status === 'FAILED'
    && isHealingTraceEligible(trace) && caseRunForTrace(run, trace))
}

function runCreationSelectionKey(): string {
  const singleApiCaseSelected = selectedApiCaseIds.value.length === 1
  return [
    projectId.value ?? '',
    runType.value,
    selectedEnvironmentId.value ?? '',
    selectedApiCaseIds.value.join(','),
    selectedCaseId.value ?? '',
    singleApiCaseSelected ? selectedApiCaseDetail.value?.current_version_id ?? '' : '',
    singleApiCaseSelected && selectedApiCaseDetail.value?.current_version
      ? String(apiStepMaxRetries(selectedApiCaseDetail.value.current_version.content))
      : '',
    selectedScenarioId.value ?? '',
    selectedScenarioVersionId.value ?? '',
    selectedWebCaseId.value ?? '',
    selectedWebCaseVersionId.value ?? '',
    selectedRunnerId.value ?? '',
    totalTimeoutMsInput.value ?? '',
  ].join('|')
}

async function loadSelectedApiCaseDetail(caseId: number, force = false): Promise<void> {
  const requestedProjectId = projectId.value
  const requestKey = `${requestedProjectId ?? ''}:${caseId}`
  if (!force && apiCaseDetailInFlightKey === requestKey) return
  const requestSequence = ++apiCaseDetailLoadSequence
  apiCaseDetailInFlightKey = requestKey
  selectedApiCaseDetail.value = null
  selectedApiCaseDetailError.value = null
  selectedApiCaseDetailLoading.value = true
  try {
    const detail = await getTestCase(caseId)
    if (requestSequence !== apiCaseDetailLoadSequence
      || projectId.value !== requestedProjectId
      || selectedCaseId.value !== caseId
      || runType.value !== 'API_CASE') return
    if (detail.project_id !== requestedProjectId || detail.case_type !== 'API') {
      selectedApiCaseDetailError.value = '所选用例不属于当前项目或不是 API 用例，暂不能创建新运行'
      return
    }
    selectedApiCaseDetail.value = detail
    validationResult.value = null
    invalidateRunCreationRequest()
  } catch (error) {
    if (requestSequence === apiCaseDetailLoadSequence
      && projectId.value === requestedProjectId
      && selectedCaseId.value === caseId
      && runType.value === 'API_CASE') {
      selectedApiCaseDetailError.value = safeErrorMessage(
        error,
        '当前 API 用例版本读取失败，暂不能创建新运行',
      )
    }
  } finally {
    if (requestSequence === apiCaseDetailLoadSequence) {
      apiCaseDetailInFlightKey = null
      selectedApiCaseDetailLoading.value = false
    }
  }
}

function isCurrentRunCreationRequest(requestSequence: number, selectionKey: string): boolean {
  return requestSequence === runCreationSequence
    && pageActive.value
    && runCreationSelectionKey() === selectionKey
}

async function loadScenarioVersions(scenarioId: number): Promise<void> {
  const requestedProjectId = projectId.value
  const requestSequence = ++scenarioVersionLoadSequence
  scenarioVersions.value = []
  selectedScenarioVersionId.value = undefined
  scenarioVersionsLoading.value = true
  scenarioVersionsError.value = null

  try {
    const versions = await getScenarioVersions(scenarioId)
    if (requestSequence !== scenarioVersionLoadSequence
      || projectId.value !== requestedProjectId
      || selectedScenarioId.value !== scenarioId
      || runType.value !== 'SCENARIO') return

    scenarioVersions.value = versions
    const scenario = scenarios.value.find((item) => item.id === scenarioId)
    const currentVersionId = scenario?.current_version_id
    selectedScenarioVersionId.value = versions.some((version) => version.id === currentVersionId)
      ? currentVersionId ?? undefined
      : versions[0]?.id
  } catch (error) {
    if (requestSequence === scenarioVersionLoadSequence
      && projectId.value === requestedProjectId
      && selectedScenarioId.value === scenarioId
      && runType.value === 'SCENARIO') {
      scenarioVersionsError.value = safeErrorMessage(error, 'Scenario 版本加载失败，请稍后重试')
    }
  } finally {
    if (requestSequence === scenarioVersionLoadSequence) {
      scenarioVersionsLoading.value = false
    }
  }
}

async function loadWebCaseVersions(webCaseId: number, preferredVersionId?: number): Promise<void> {
  const requestedProjectId = projectId.value
  const requestSequence = ++webCaseVersionLoadSequence
  webCaseDetail.value = null
  webCaseVersions.value = []
  selectedWebCaseVersionId.value = undefined
  webCaseVersionsLoading.value = true
  webCaseVersionsError.value = null

  try {
    const [detail, versions] = await Promise.all([
      getWebCase(webCaseId),
      getWebCaseVersions(webCaseId),
    ])
    if (requestSequence !== webCaseVersionLoadSequence
      || projectId.value !== requestedProjectId
      || selectedWebCaseId.value !== webCaseId
      || runType.value !== 'WEB_CASE') return

    webCaseDetail.value = detail
    webCaseVersions.value = versions
    const approvedVersions = versions.filter((version) => version.status === 'APPROVED')
    const preferredVersion = preferredVersionId !== undefined
      ? approvedVersions.find((version) => version.id === preferredVersionId)
      : undefined
    selectedWebCaseVersionId.value = preferredVersion?.id
      ?? (approvedVersions.some((version) => version.id === detail.current_version_id)
        ? detail.current_version_id ?? undefined
        : approvedVersions[0]?.id)
  } catch (error) {
    if (requestSequence === webCaseVersionLoadSequence
      && projectId.value === requestedProjectId
      && selectedWebCaseId.value === webCaseId
      && runType.value === 'WEB_CASE') {
      webCaseVersionsError.value = safeErrorMessage(error, 'Web 用例版本加载失败，请稍后重试')
    }
  } finally {
    if (requestSequence === webCaseVersionLoadSequence) webCaseVersionsLoading.value = false
  }
}

function clearStreamRetryTimer(): void {
  if (streamRetryTimer !== null) {
    window.clearTimeout(streamRetryTimer)
    streamRetryTimer = null
  }
}

function clearRealtimeRefreshTimer(): void {
  if (realtimeRefreshTimer !== null) {
    window.clearTimeout(realtimeRefreshTimer)
    realtimeRefreshTimer = null
  }
}

function isCurrentStream(sequence: number, projectIdForStream: number, runId: string): boolean {
  return sequence === streamSequence
    && streamTargetProjectId === projectIdForStream
    && streamTargetRunId === runId
    && projectId.value === projectIdForStream
    && detailVisible.value
    && detailTargetRunId.value === runId
}

function stopRunEventStream(
  state: RunEventConnectionState = 'DISCONNECTED',
  message: string | null = null,
): void {
  streamSequence += 1
  clearStreamRetryTimer()
  clearRealtimeRefreshTimer()
  streamController.value?.abort()
  streamController.value = null
  streamTargetRunId = null
  streamTargetProjectId = null
  streamReconnectAttempt = 0
  streamLastEventId.value = null
  streamLastEventAt.value = null
  streamState.value = state
  streamErrorMessage.value = message
}

function queueRealtimeRunRefresh(projectIdForStream: number, runId: string, sequence: number): void {
  clearRealtimeRefreshTimer()
  realtimeRefreshTimer = window.setTimeout(() => {
    realtimeRefreshTimer = null
    if (!isCurrentStream(sequence, projectIdForStream, runId)) return
    void refreshRunAndEvidenceFromEvent(projectIdForStream, runId, sequence)
  }, REALTIME_REFRESH_DEBOUNCE_MS)
}

async function refreshRunAndEvidenceFromEvent(
  projectIdForStream: number,
  runId: string,
  sequence: number,
): Promise<void> {
  if (!isCurrentStream(sequence, projectIdForStream, runId)) return
  await fetchRunDetail(runId, { showLoading: false })
  if (!isCurrentStream(sequence, projectIdForStream, runId)) return
  await fetchEvidence(projectIdForStream, runId)
}

function handleRunStatusEvent(
  event: RunEventResponse,
  frameId: string | null,
  sequence: number,
  projectIdForStream: number,
  runId: string,
): void {
  if (!isCurrentStream(sequence, projectIdForStream, runId)) return
  if (event.project_id !== projectIdForStream || event.run_id !== runId) return
  streamLastEventId.value = frameId || event.id
  streamLastEventAt.value = event.occurred_at
  queueRealtimeRunRefresh(projectIdForStream, runId, sequence)
}

function scheduleRunEventReconnect(
  projectIdForStream: number,
  runId: string,
  sequence: number,
): void {
  if (!isCurrentStream(sequence, projectIdForStream, runId)) return
  if (streamReconnectAttempt >= STREAM_MAX_RECONNECT_ATTEMPTS) {
    streamState.value = 'POLLING_FALLBACK'
    streamErrorMessage.value = '实时连接暂时不可用，已降级为 8 秒轮询。'
    return
  }

  streamReconnectAttempt += 1
  const delay = Math.min(
    1_000 * (2 ** (streamReconnectAttempt - 1)),
    STREAM_MAX_RETRY_DELAY_MS,
  )
  streamState.value = 'RECONNECTING'
  streamErrorMessage.value = `实时连接中断，将在 ${Math.ceil(delay / 1_000)} 秒后重连（${streamReconnectAttempt}/${STREAM_MAX_RECONNECT_ATTEMPTS}）。`
  clearStreamRetryTimer()
  streamRetryTimer = window.setTimeout(() => {
    streamRetryTimer = null
    if (isCurrentStream(sequence, projectIdForStream, runId)) {
      void connectRunEventStream(projectIdForStream, runId, sequence)
    }
  }, delay)
}

async function connectRunEventStream(
  projectIdForStream: number,
  runId: string,
  sequence: number,
): Promise<void> {
  if (!isCurrentStream(sequence, projectIdForStream, runId)) return
  const controller = new AbortController()
  streamController.value = controller
  streamState.value = streamReconnectAttempt > 0 ? 'RECONNECTING' : 'CONNECTING'

  try {
    await consumeRunEventStream({
      runId,
      afterId: streamLastEventId.value,
      signal: controller.signal,
      onOpen: () => {
        if (!isCurrentStream(sequence, projectIdForStream, runId)) return
        streamState.value = 'CONNECTED'
        streamErrorMessage.value = null
      },
      onRunStatus: (event, frameId) => handleRunStatusEvent(event, frameId, sequence, projectIdForStream, runId),
    })
    scheduleRunEventReconnect(projectIdForStream, runId, sequence)
  } catch (error) {
    if (controller.signal.aborted || !isCurrentStream(sequence, projectIdForStream, runId)) return
    if (error instanceof RunEventStreamHttpError && (error.status === 401 || error.status === 403)) {
      const message = error.status === 401
        ? '实时连接鉴权已失效，请重新登录；当前保留 8 秒轮询。'
        : '当前用户无权访问实时事件流；当前保留 8 秒轮询。'
      streamState.value = 'POLLING_FALLBACK'
      streamErrorMessage.value = message
      ElMessage.warning(message)
    } else {
      scheduleRunEventReconnect(projectIdForStream, runId, sequence)
    }
  } finally {
    if (streamController.value === controller) streamController.value = null
  }
}

function startRunEventStream(run: Run): void {
  stopRunEventStream()
  streamTargetProjectId = run.project_id
  streamTargetRunId = run.id
  streamState.value = 'CONNECTING'
  if (!authStore.isAuthenticated) {
    streamState.value = 'POLLING_FALLBACK'
    streamErrorMessage.value = '登录状态已失效，实时连接未启动；当前保留 8 秒轮询。'
    return
  }
  const sequence = streamSequence
  void connectRunEventStream(run.project_id, run.id, sequence)
}

function isRunnerSelectable(
  runner: Runner,
  capabilityName: RunnerCapabilityName = 'API',
  slotType: RunnerSlotType = 'API',
): boolean {
  const capabilityReady = runner.capabilities.some((capability) => (
    capability.name === capabilityName && capability.status === 'READY'
  ))
  const slotAvailable = runner.slots.some((slot) => slot.type === slotType && slot.available >= 1)
  return runner.status === 'ACTIVE'
    && runner.online_status === 'ONLINE'
    && runner.redis_available
    && capabilityReady
    && slotAvailable
}

function projectLabel(project: Project): string {
  return `${project.name}（${project.code}）`
}

function caseLabel(testCase: TestCaseAsset | null | undefined): string {
  if (!testCase) return '未知用例'
  return `${testCase.name}（${testCase.code}）`
}

function scenarioLabel(scenario: Scenario | null | undefined): string {
  if (!scenario) return '未知 Scenario'
  return `${scenario.name}（${scenario.code}）`
}

function webCaseLabel(webCase: WebCaseResponse | null | undefined, webCaseId?: number | null): string {
  if (!webCase) return webCaseId ? 'Web 用例（详情未加载）' : '未知 Web 用例'
  return `${webCase.name}（${webCase.code}）`
}

function sessionProfileLabel(profileId: number | null | undefined): string {
  if (!profileId) return '未配置'
  const profile = sessionProfiles.value.find((item) => item.id === profileId)
  if (!profile) return '已配置（详情未加载）'
  return `${profile.name}（${profile.status === 'ACTIVE' ? '启用' : '已归档'}${profile.expires_at ? `，有效至 ${formatDate(profile.expires_at)}` : ''}）`
}

function runnerLabel(runnerId: string | null | undefined): string {
  if (!runnerId) return '未指定'
  const runner = runners.value.find((item) => item.id === runnerId)
  return runner ? `${runner.name}（${runner.hostname}）` : runnerId
}

function environmentLabel(environmentId: number | null | undefined): string {
  if (!environmentId) return '未指定'
  const environment = environments.value.find((item) => item.id === environmentId)
  return environment ? `${environment.name}（${environment.code}）` : '环境详情未加载'
}

function runCaseLabel(run: Run): string {
  if (run.case_id) {
    return caseLabel(apiCases.value.find((testCase) => testCase.id === run.case_id))
  }
  return run.run_type === 'API_CASE' ? 'API 用例缺失' : 'Scenario 缺失'
}

function runTypeLabel(type: RunType): string {
  if (type === 'API_CASE') return 'API_CASE'
  if (type === 'SCENARIO') return 'SCENARIO'
  return 'WEB_CASE'
}

function runTargetLabel(run: Run): string {
  if (run.run_type === 'API_CASE') return runCaseLabel(run)
  if (run.run_type === 'SCENARIO') return scenarioLabel(scenarios.value.find((scenario) => scenario.id === run.scenario_id))
  return webCaseLabel(webCases.value.find((webCase) => webCase.id === run.web_case_id), run.web_case_id)
}

function runTargetVersionLabel(run: Run): string {
  const versionId = run.run_type === 'API_CASE'
    ? run.case_version_id
    : run.run_type === 'SCENARIO' ? run.scenario_version_id : run.web_case_version_id
  return versionId ? '已锁定' : '后端解析当前版本'
}

function caseRunLabel(caseRun: CaseRun): string {
  if (caseRun.case_id) {
    return caseLabel(apiCases.value.find((testCase) => testCase.id === caseRun.case_id))
  }
  if (caseRun.scenario_id) {
    return scenarioLabel(scenarios.value.find((scenario) => scenario.id === caseRun.scenario_id))
  }
  if (caseRun.web_case_id) {
    return webCaseLabel(webCases.value.find((webCase) => webCase.id === caseRun.web_case_id), caseRun.web_case_id)
  }
  if (detailRun.value?.run_type === 'SCENARIO') return `场景节点 #${caseRun.sequence_no}`
  if (detailRun.value?.run_type === 'WEB_CASE') return `Web 节点 #${caseRun.sequence_no}`
  return 'API 用例'
}

function stepRunLabel(stepRun: StepRun): string {
  return `${stepRun.step_name} · ${stepRun.step_type}`
}

function formatDate(value: string | null | undefined): string {
  return formatApiDateTime(value)
}

function formatDurationMilliseconds(milliseconds: number | null | undefined): string {
  if (milliseconds === null || milliseconds === undefined || milliseconds <= 0) return '—'
  if (milliseconds < 1_000) return `${milliseconds} ms`
  const seconds = milliseconds / 1_000
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`
  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = Math.round(seconds % 60)
  return `${minutes} 分 ${remainingSeconds} 秒`
}

function formatFileSize(bytes: number): string {
  if (bytes < 1_024) return `${bytes} B`
  if (bytes < 1_024 * 1_024) return `${(bytes / 1_024).toFixed(1)} KB`
  return `${(bytes / (1_024 * 1_024)).toFixed(1)} MB`
}

function evidenceTypeLabel(type: EvidenceArtifactType): string {
  const labels: Record<EvidenceArtifactType, string> = {
    RESPONSE: '响应',
    ASSERTION_RESULT: '断言结果',
    SCREENSHOT: '截图',
    PLAYWRIGHT_TRACE: 'Playwright Trace',
    CONSOLE_ERROR: '控制台错误',
    NETWORK_ERROR: '网络错误',
    WEB_SUMMARY: 'Web 摘要',
  }
  return labels[type]
}

function webTraceStatusLabel(status: WebExecutionTrace['status']): string {
  const labels: Record<WebExecutionTrace['status'], string> = {
    SUCCESS: '成功',
    FAILED: '失败',
    SKIPPED: '已跳过',
    TIMEOUT: '超时',
    CANCELLED: '已取消',
  }
  return labels[status]
}

function locatorAttemptStatusLabel(status: WebLocatorAttempt['status']): string {
  const labels: Record<WebLocatorAttempt['status'], string> = {
    NOT_FOUND: '未找到',
    ACTION_FAILED: '执行失败',
    SUCCESS: '成功',
  }
  return labels[status]
}

function locatorAttemptStatusType(status: WebLocatorAttempt['status']): 'success' | 'danger' | 'info' {
  if (status === 'SUCCESS') return 'success'
  if (status === 'ACTION_FAILED') return 'danger'
  return 'info'
}

function webTracesOf(run: RunDetail): WebExecutionTrace[] {
  return Array.isArray(run.web_traces) ? run.web_traces : []
}

function evidenceMetadataLabel(artifact: EvidenceArtifact): string {
  if (!artifact.metadata) return '—'

  const labels: Record<string, string> = {
    title: '标题',
    status: '状态',
    duration_ms: '耗时',
    error_count: '错误数',
    browser: '浏览器',
    page_count: '页面数',
    width: '宽度',
    height: '高度',
    level: '级别',
    count: '数量',
    method: '方法',
    category: '类别',
    page_url: '页面 URL',
    final_url: '最终 URL',
  }
  const urlKeys = new Set(['page_url', 'final_url'])
  const entries = Object.entries(labels).flatMap(([key, label]) => {
    const value = artifact.metadata?.[key]
    if (typeof value !== 'string' && typeof value !== 'number' && typeof value !== 'boolean') return []
    if (typeof value === 'number' && !Number.isFinite(value)) return []
    const normalized = String(value).replace(/[\r\n\t]/g, ' ').trim()
    if (!normalized) return []
    const limit = urlKeys.has(key) ? 120 : 80
    const shortened = normalized.length > limit ? `${normalized.slice(0, limit - 1)}…` : normalized
    return [`${label}：${shortened}${key === 'duration_ms' ? ' ms' : ''}`]
  })
  return entries.join(' · ') || '—'
}

function safeDownloadFileName(fileName: string): string {
  const safeName = fileName.replace(/[\\/:*?"<>|\r\n]/g, '_').trim().slice(0, 255)
  return safeName || 'evidence.json'
}

function formatRunDuration(run: Pick<Run, 'started_at' | 'ended_at'>): string {
  if (!run.started_at) return '—'
  const startedAt = apiDateTimeMs(run.started_at)
  const endedAt = run.ended_at ? apiDateTimeMs(run.ended_at) : Date.now()
  if (Number.isNaN(startedAt) || Number.isNaN(endedAt) || endedAt < startedAt) return '—'
  return formatDurationMilliseconds(endedAt - startedAt)
}

function retryCountLabel(retryCount: number | null | undefined): string {
  return retryCount && retryCount > 0 ? `${retryCount} 次` : '未重试'
}

function runStatusType(status: RunStatus): 'success' | 'danger' | 'warning' | 'info' {
  if (status === 'SUCCESS') return 'success'
  if (status === 'FAILED' || status === 'TIMEOUT') return 'danger'
  if (status === 'RUNNING' || status === 'ASSIGNED' || status === 'QUEUED' || status === 'CANCELLING') return 'warning'
  return 'info'
}

function runStatusLabel(status: RunStatus): string {
  return runStatusLabels[status]
}

function isTerminalRunStatus(status: RunStatus): boolean {
  return status === 'SUCCESS' || status === 'FAILED' || status === 'CANCELLED' || status === 'TIMEOUT'
}

function isCancellableRun(status: RunStatus): boolean {
  return status === 'CREATED'
    || status === 'QUEUED'
    || status === 'ASSIGNED'
    || status === 'RUNNING'
}

function isCancelInProgressRun(status: RunStatus): boolean {
  return status === 'CANCELLING'
}

function cancelConfirmationMessage(status: RunStatus): string {
  if (status === 'ASSIGNED' || status === 'RUNNING') {
    return '将发送协作取消请求，将在 Runner 下一个安全检查点停止。正在进行的 HTTP 可能等待返回或超时；不会立即终止外部请求。'
  }
  return '只取消尚未开始的任务。已进入执行阶段的任务不能通过此操作停止。'
}

function isForceStoppableRun(status: RunStatus): boolean {
  return status === 'ASSIGNED' || status === 'RUNNING' || status === 'CANCELLING'
}

function forceStopConfirmationMessage(run: Run): string {
  if (run.status === 'CANCELLING') {
    return '将重复请求强制停止；Runner 会立即终止执行子进程。清理可能未完成，请在停止后人工核对测试数据。'
  }
  return '将立即终止 Runner 上的执行子进程，阻塞中的 HTTP 请求会被中断。清理可能未完成，请在停止后人工核对测试数据；此操作不等同于协作取消。'
}

function runDisplayLabel(run: Run): string {
  if (run.status === 'CANCELLED' && run.force_stopped) return '已强制停止'
  return runStatusLabel(run.status)
}

function timeoutErrorLabel(run: Run): string {
  if (run.error_type === 'TOTAL_TIMEOUT') return '运行总超时已终止执行'
  if (run.error_type === 'TARGET_TIMEOUT') return '目标请求超时'
  if (run.error_type === 'AUTH_REQUEST_REJECTED') return '登录前置请求被目标系统拒绝'
  if (run.error_type === 'SETUP_REQUEST_REJECTED') return '数据准备请求被目标系统拒绝'
  if (run.error_type === 'TOKEN_VALUE_MISSING') return '登录响应中未找到 Token'
  return run.error_type || '运行错误'
}

function apiActionErrorLabel(errorType: string | null): string {
  if (!errorType) return '—'
  const labels: Record<string, string> = {
    AUTH_REQUEST_REJECTED: '登录前置请求被拒绝',
    SETUP_REQUEST_REJECTED: '数据准备请求被拒绝',
    TOKEN_VALUE_MISSING: '响应中未找到 Token',
    TARGET_NETWORK_ERROR: '目标 API 网络连接失败',
    TARGET_TIMEOUT: '目标请求超时',
    RUNTIME_TEMPLATE: '运行变量无法解析',
  }
  return labels[errorType] ?? errorType
}

function formatTotalTimeout(totalTimeoutMs: unknown): string {
  if (totalTimeoutMs === null || totalTimeoutMs === undefined) return '系统默认'
  if (typeof totalTimeoutMs !== 'number'
    || !Number.isFinite(totalTimeoutMs)
    || totalTimeoutMs < 1_000
    || totalTimeoutMs > 86_400_000) return '—'

  const totalSeconds = Math.floor(totalTimeoutMs / 1_000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  if (minutes > 0 && seconds > 0) return `${minutes} 分 ${seconds} 秒`
  if (minutes > 0) return `${minutes} 分钟`
  return `${totalSeconds} 秒`
}

function isRunOperationInFlight(runId: string): boolean {
  return batchDispatching.value
    || dispatchingRunId.value === runId
    || cancellingRunId.value === runId
    || forceStoppingRunId.value === runId
}

async function forceStopSelectedRun(run: Run): Promise<void> {
  if (!isForceStoppableRun(run.status)
    || dispatchingRunId.value !== null
    || cancellingRunId.value !== null
    || forceStoppingRunId.value !== null) return

  try {
    await ElMessageBox.confirm(
      forceStopConfirmationMessage(run),
      `强制停止运行 ${run.run_code}？`,
      {
        type: 'error',
        confirmButtonText: '确认强制停止',
        cancelButtonText: '暂不停止',
      },
    )
  } catch {
    return
  }

  if (projectId.value !== run.project_id
    || !pageActive.value
    || dispatchingRunId.value !== null
    || cancellingRunId.value !== null
    || forceStoppingRunId.value !== null) return
  const requestedProjectId = run.project_id
  const requestedProjectSequence = projectLoadSequence
  forceStoppingRunId.value = run.id
  try {
    const detail = await forceStopRun(run.id)
    if (!isCurrentRunRequestContext(requestedProjectId, requestedProjectSequence)) return
    await syncRunDetailAfterCancel(detail, requestedProjectId, requestedProjectSequence)
    if (detail.status === 'CANCELLED') {
      ElMessage.success(detail.force_stopped ? '运行已强制停止' : '运行已取消')
    } else {
      ElMessage.success(`强制停止请求已发送，运行状态：${runStatusLabel(detail.status)}`)
    }
  } catch (error) {
    if (!isCurrentRunRequestContext(requestedProjectId, requestedProjectSequence)) return
    if (apiErrorStatus(error) === 409) {
      const message = '运行状态已变化，强制停止未生效；正在刷新最新状态。'
      ElMessage.warning(message)
      await refreshRunAfterCancelConflict(run, requestedProjectId, requestedProjectSequence)
    } else {
      const message = safeErrorMessage(error, '强制停止失败，请稍后重试')
      ElMessage.error(message)
    }
  } finally {
    forceStoppingRunId.value = null
  }
}

function isCurrentRunRequestContext(requestedProjectId: number, requestedProjectSequence: number): boolean {
  return pageActive.value
    && projectId.value === requestedProjectId
    && projectLoadSequence === requestedProjectSequence
}

function syncDispatchMessageForCreatedRun(run: Run): void {
  if (createdRun.value?.id !== run.id || !dispatchMessage.value) return
  if (isTerminalRunStatus(run.status)) {
    dispatchMessage.value = `运行已完成：${runStatusLabel(run.status)}`
  } else if (dispatchMessage.value.startsWith('运行已投递')) {
    dispatchMessage.value = `运行已投递，当前状态：${runStatusLabel(run.status)}`
  }
}

function syncCreatedRun(detail: RunDetail): void {
  if (createdRun.value?.id !== detail.id) return
  createdRun.value = detail
  syncDispatchMessageForCreatedRun(detail)
}

function nodeStatusType(status: RunNodeStatus): 'success' | 'danger' | 'warning' | 'info' {
  if (status === 'SUCCESS') return 'success'
  if (status === 'FAILED' || status === 'TIMEOUT') return 'danger'
  if (status === 'RUNNING' || status === 'ASSIGNED' || status === 'CANCELLING' || status === 'REVIEW') return 'warning'
  return 'info'
}

function nodeStatusLabel(status: RunNodeStatus): string {
  return nodeStatusLabels[status]
}

function resultCountSummary(run: Run): string {
  return `总 ${run.total} · 成功 ${run.pass} · 失败 ${run.fail} · 复核 ${run.review} · 超时 ${run.timeout}`
}

async function openRunFromDeepLink(id: number, sequence: number): Promise<void> {
  const deepLink = pendingRunDeepLink
  if (!deepLink || deepLink.projectId !== id || sequence !== projectLoadSequence || projectId.value !== id || !pageActive.value) return

  const listItem = runs.value.find((run) => run.id === deepLink.runId)
  if (listItem) {
    pendingRunDeepLink = null
    await openRunDetail(listItem)
    return
  }

  try {
    const detail = await getRun(deepLink.runId)
    if (sequence !== projectLoadSequence || projectId.value !== id || !pageActive.value) return
    if (detail.project_id !== id) {
      runDeepLinkMessage.value = '原运行不属于当前项目，未打开运行详情。'
      pendingRunDeepLink = null
      return
    }
    pendingRunDeepLink = null
    await openRunDetail(detail)
  } catch (error) {
    if (sequence === projectLoadSequence && projectId.value === id) {
      runDeepLinkMessage.value = `原运行详情加载失败：${safeErrorMessage(error, '请稍后从运行列表打开')}`
      pendingRunDeepLink = null
    }
  }
}

async function loadProjects(): Promise<void> {
  projectsLoading.value = true
  projectError.value = null
  try {
    const response = await getProjects(false)
    projects.value = response.items.filter((project) => project.status === 'ACTIVE')
    const requestedProjectId = positiveQueryId(route.query.project_id)
    if (requestedProjectId !== null && projects.value.some((project) => project.id === requestedProjectId)) {
      projectId.value = requestedProjectId
    } else if (requestedProjectId !== null && route.query.project_id !== undefined) {
      runDeepLinkMessage.value = [runDeepLinkMessage.value, '返回原运行的项目当前不可用，已使用默认项目。'].filter(Boolean).join(' ')
      pendingRunDeepLink = null
      if (!projectId.value || !projects.value.some((project) => project.id === projectId.value)) projectId.value = projects.value[0]?.id
    } else if (!projectId.value || !projects.value.some((project) => project.id === projectId.value)) {
      projectId.value = projects.value[0]?.id
    }
    if (!projectId.value) {
      loadedProjectId = null
      resourcesError.value = null
      scenarios.value = []
      webCases.value = []
      sessionProfiles.value = []
      invalidateScenarioVersionRequest()
      invalidateWebCaseVersionRequest()
      runs.value = []
      runTotal.value = 0
      invalidateDetailRequest()
      invalidateEvidenceRequest()
      stopRunEventStream()
      detailVisible.value = false
      detailTargetRunId.value = null
      detailRun.value = null
      detailError.value = null
      evidenceItems.value = []
      evidenceError.value = null
    }
  } catch (error) {
    projectError.value = safeErrorMessage(error, '项目列表加载失败，请稍后重试')
  } finally {
    projectsLoading.value = false
  }
}

async function loadProjectData(id: number): Promise<void> {
  const sequence = ++projectLoadSequence
  invalidateScenarioVersionRequest()
  invalidateWebCaseVersionRequest()
  invalidateApiCaseDetailRequest()
  const isNewProject = loadedProjectId !== id
  if (isNewProject) {
    environments.value = []
    apiCases.value = []
    scenarios.value = []
    webCases.value = []
    sessionProfiles.value = []
    runners.value = []
    runs.value = []
    runTotal.value = 0
    runPage.value = 1
    selectedEnvironmentId.value = undefined
    selectedWebCaseId.value = undefined
    selectedWebCaseVersionId.value = undefined
    selectedCaseId.value = undefined
    selectedApiCaseIds.value = []
    selectedScenarioId.value = undefined
    selectedRunnerId.value = undefined
    validationResult.value = null
    batchCreateSummary.value = null
    createdRun.value = null
    invalidateDetailRequest()
    invalidateEvidenceRequest()
    stopRunEventStream()
    detailVisible.value = false
    detailTargetRunId.value = null
    detailRun.value = null
    detailError.value = null
    evidenceItems.value = []
    evidenceError.value = null
    dispatchMessage.value = null
    loadedProjectId = id
  }

  resourcesLoading.value = true
  runListLoading.value = true
  resourcesError.value = null
  runListError.value = null
  projectDataActiveRequests += 1
  try {
    const [environmentResult, caseResult, scenarioResult, webCaseResult, sessionProfileResult, runnerResult, runResult] = await Promise.allSettled([
      getEnvironments(id),
      getTestCases(id),
      getScenarios(id),
      getWebCases(id),
      getSessionProfiles(id),
      getRunners(),
      getRuns(id, runPage.value, runPageSize.value),
    ])

    if (sequence !== projectLoadSequence || projectId.value !== id) return

    const resourceErrors: string[] = []
    if (environmentResult.status === 'fulfilled') {
      environments.value = environmentResult.value
    } else {
      resourceErrors.push(safeErrorMessage(environmentResult.reason, '环境加载失败'))
    }
    if (caseResult.status === 'fulfilled') {
      apiCases.value = caseResult.value
    } else {
      resourceErrors.push(safeErrorMessage(caseResult.reason, 'API 用例加载失败'))
    }
    if (scenarioResult.status === 'fulfilled') {
      scenarios.value = scenarioResult.value
    } else {
      resourceErrors.push(safeErrorMessage(scenarioResult.reason, 'Scenario 加载失败'))
    }
    if (webCaseResult.status === 'fulfilled') {
      webCases.value = webCaseResult.value.items
    } else {
      resourceErrors.push(safeErrorMessage(webCaseResult.reason, 'Web 用例加载失败'))
    }
    if (sessionProfileResult.status === 'fulfilled') {
      sessionProfiles.value = sessionProfileResult.value
    } else {
      resourceErrors.push(safeErrorMessage(sessionProfileResult.reason, '会话配置加载失败'))
    }
    if (runnerResult.status === 'fulfilled') {
      runners.value = runnerResult.value.items
    } else {
      resourceErrors.push(safeErrorMessage(runnerResult.reason, 'Runner 加载失败'))
    }
    resourcesError.value = resourceErrors.length > 0 ? resourceErrors.join('；') : null

    if (runResult.status === 'fulfilled') {
      runs.value = runResult.value.items
      runTotal.value = runResult.value.total
      runPage.value = runResult.value.page
    } else {
      runListError.value = safeErrorMessage(runResult.reason, '运行列表加载失败')
    }

    if (!enabledEnvironments.value.some((environment) => environment.id === selectedEnvironmentId.value)) {
      selectedEnvironmentId.value = enabledEnvironments.value[0]?.id
    }
    const activeApiCaseIds = new Set(activeApiCases.value.map((testCase) => testCase.id))
    selectedApiCaseIds.value = selectedApiCaseIds.value.filter((caseId) => activeApiCaseIds.has(caseId))
    if (selectedApiCaseIds.value.length === 0 && activeApiCases.value[0]) {
      selectedApiCaseIds.value = [activeApiCases.value[0].id]
    }
    selectedCaseId.value = runType.value === 'API_CASE' ? selectedApiCaseIds.value[0] : undefined
    if (!executableScenarios.value.some((scenario) => scenario.id === selectedScenarioId.value)) {
      selectedScenarioId.value = executableScenarios.value[0]?.id
    }
    if (!executableWebCases.value.some((webCase) => webCase.id === selectedWebCaseId.value)) {
      selectedWebCaseId.value = executableWebCases.value[0]?.id
    }
    if (!availableRunners.value.some((runner) => runner.id === selectedRunnerId.value)) {
      selectedRunnerId.value = availableRunners.value[0]?.id
    }

    if (runType.value === 'SCENARIO' && selectedScenarioId.value) {
      await loadScenarioVersions(selectedScenarioId.value)
    }
    if (runType.value === 'WEB_CASE' && selectedWebCaseId.value) {
      await loadWebCaseVersions(selectedWebCaseId.value)
    }
    if (runType.value === 'API_CASE' && selectedCaseId.value) {
      await loadSelectedApiCaseDetail(selectedCaseId.value, true)
    }
    await openRunFromDeepLink(id, sequence)
  } finally {
    projectDataActiveRequests -= 1
    if (sequence === projectLoadSequence) {
      resourcesLoading.value = false
      runListLoading.value = false
    }
  }
}

async function loadRuns(options: { silent?: boolean } = {}): Promise<void> {
  const silent = options.silent ?? false
  const currentProjectId = projectId.value
  if (!currentProjectId || runListInFlight.value || projectDataActiveRequests > 0) return

  runListInFlight.value = true
  if (!silent) runListLoading.value = true
  if (!silent) runListError.value = null

  try {
    const response = await getRuns(currentProjectId, runPage.value, runPageSize.value)
    if (projectId.value !== currentProjectId) return
    runs.value = response.items
    runTotal.value = response.total
    runPage.value = response.page
    runListError.value = null
  } catch (error) {
    if (projectId.value !== currentProjectId) return
    const message = safeErrorMessage(error, '运行列表刷新失败，已保留已有数据')
    runListError.value = message
    if (!silent) ElMessage.error(message)
  } finally {
    runListInFlight.value = false
    if (!silent) runListLoading.value = false
  }
}

async function loadHealingConfiguration(
  selection: HealingTraceSelection,
  requestSequence: number,
): Promise<void> {
  const { key, run } = selection
  healingPromptLoading.value = true
  healingSchemaLoading.value = true
  healingBindingLoading.value = true
  healingPromptError.value = null
  healingSchemaError.value = null
  healingBindingError.value = null

  const promptRequest = getPrompts(false, 'LOCATOR_HEALING')
    .then((items) => {
      if (isCurrentHealingRequest(requestSequence, key)) healingPrompts.value = items
    })
    .catch((error: unknown) => {
      if (isCurrentHealingRequest(requestSequence, key)) {
        healingPromptError.value = safeErrorMessage(error, 'LOCATOR_HEALING Prompt 加载失败，请稍后重试')
      }
    })
    .finally(() => {
      if (isCurrentHealingRequest(requestSequence, key)) healingPromptLoading.value = false
    })
  const schemaRequest = getOutputSchemas(false)
    .then((items) => {
      if (isCurrentHealingRequest(requestSequence, key)) healingOutputSchemas.value = items
    })
    .catch((error: unknown) => {
      if (isCurrentHealingRequest(requestSequence, key)) {
        healingSchemaError.value = safeErrorMessage(error, '输出 Schema 加载失败，请稍后重试')
      }
    })
    .finally(() => {
      if (isCurrentHealingRequest(requestSequence, key)) healingSchemaLoading.value = false
    })
  const bindingRequest = getModelBindings(run.project_id)
    .then((items) => {
      if (isCurrentHealingRequest(requestSequence, key)) healingBindings.value = items
    })
    .catch((error: unknown) => {
      if (isCurrentHealingRequest(requestSequence, key)) {
        healingBindingError.value = safeErrorMessage(error, '项目 LOCATOR_HEALING 模型绑定加载失败，请稍后重试')
      }
    })
    .finally(() => {
      if (isCurrentHealingRequest(requestSequence, key)) healingBindingLoading.value = false
    })

  await Promise.all([promptRequest, schemaRequest, bindingRequest])
  if (!isCurrentHealingRequest(requestSequence, key)) return
  const firstPrompt = availableHealingPrompts.value[0]
  selectedHealingPromptId.value = firstPrompt?.id
  if (!firstPrompt && !healingPromptError.value && !healingSchemaError.value) {
    healingPromptError.value = '当前没有启用且绑定输出 Schema 的定位器自愈 Prompt，请前往 Prompt 中心配置。'
  }
  if (!healingBinding.value && !healingBindingError.value) {
    healingBindingError.value = '当前项目缺少 LOCATOR_HEALING 模型绑定，请前往项目设置配置。'
  }
}

async function loadHealingProposalHistory(
  selection: HealingTraceSelection,
  requestSequence: number,
  preferredProposalId?: number,
): Promise<void> {
  const { key, run, caseRun } = selection
  if (healingProposalsInFlightKey === key) return
  healingProposalsInFlightKey = key
  healingProposalsLoading.value = true
  healingProposalsError.value = null
  try {
    const response = await getWebHealingProposals(run.id, caseRun.id)
    if (!isCurrentHealingRequest(requestSequence, key)) return
    const items = response.items.filter((proposal) => proposal.node_id === selection.trace.node_id)
    healingProposals.value = items
    selectedHealingProposalId.value = preferredProposalId
      ?? items.find((proposal) => proposal.status === 'DRAFT')?.id
      ?? items[0]?.id
      ?? null
    syncHealingCandidateSelection(selectedHealingProposal.value)
    healingGenerationTimeoutNotice.value = null
  } catch (error) {
    if (isCurrentHealingRequest(requestSequence, key)) {
      healingProposalsError.value = safeErrorMessage(error, '自愈提案历史加载失败，请稍后重试')
    }
  } finally {
    if (healingProposalsInFlightKey === key) healingProposalsInFlightKey = null
    if (isCurrentHealingRequest(requestSequence, key)) healingProposalsLoading.value = false
  }
}

function reloadHealingProposalHistory(): void {
  const selection = healingSelection.value
  if (!selection || healingProposalsInFlightKey === selection.key) return
  void loadHealingProposalHistory(selection, healingRequestSequence)
}

function syncFailureAnalysisTarget(run: RunDetail): void {
  const eligibleIds = run.run_type === 'WEB_CASE' && (run.status === 'FAILED' || run.status === 'TIMEOUT')
    ? run.case_runs
      .filter((caseRun) => caseRun.status === 'FAILED' || caseRun.status === 'TIMEOUT')
      .map((caseRun) => caseRun.id)
    : []
  const currentId = failureAnalysisCaseRunId.value
  const nextId = eligibleIds.includes(currentId ?? -1)
    ? currentId
    : eligibleIds.length === 1 ? eligibleIds[0] : null
  if (nextId !== currentId) {
    failureAnalysisCaseRunId.value = nextId
    invalidateFailureAnalysisHistory()
  }
}

function failureAnalysisInputHasSensitiveText(value: string): boolean {
  return /(?:bearer\s+\S+|https?:\/\/|(?:password|passwd|token|access[_-]?token|refresh[_-]?token|authorization|cookie|secret|api[-_]?key|credential)\s*[:=]|\b(?:password|passwd|token|access[_-]?token|refresh[_-]?token|authorization|cookie|secret|api[-_]?key|credential)\b)/i.test(value)
}

function failureAnalysisSafeText(value: string, maximum: number): string {
  const normalized = value.replace(/[\r\n\t]+/g, ' ').trim()
  if (!normalized || failureAnalysisInputHasSensitiveText(normalized)) return '内容已隐藏'
  return normalized.length > maximum ? `${normalized.slice(0, maximum - 1)}…` : normalized
}

function failureAnalysisStatusLabel(status: WebFailureAnalysisResponse['status']): string {
  return status === 'COMPLETED' ? '已完成' : '生成中'
}

function failureAnalysisStatusType(status: WebFailureAnalysisResponse['status']): 'success' | 'warning' {
  return status === 'COMPLETED' ? 'success' : 'warning'
}

function failureAnalysisCategoryLabel(category: WebFailureAnalysisCategory): string {
  const labels: Record<WebFailureAnalysisCategory, string> = {
    LOCATOR_NOT_FOUND: 'Locator 未找到',
    ACTION_FAILED: '动作失败',
    ASSERTION_FAILED: '断言失败',
    TIMEOUT: '超时',
    NAVIGATION: '页面导航',
    NETWORK: '网络问题',
    SESSION: '会话问题',
    PAGE_CHANGED: '页面变化',
    UNKNOWN: '未知',
  }
  return labels[category]
}

function failureAnalysisSeverityLabel(severity: WebFailureAnalysisSeverity): string {
  const labels: Record<WebFailureAnalysisSeverity, string> = {
    LOW: '低',
    MEDIUM: '中',
    HIGH: '高',
  }
  return labels[severity]
}

function failureAnalysisSeverityType(severity: WebFailureAnalysisSeverity): 'success' | 'warning' | 'danger' {
  if (severity === 'HIGH') return 'danger'
  if (severity === 'MEDIUM') return 'warning'
  return 'success'
}

function failureAnalysisConfidenceLabel(confidence: number): string {
  return Number.isFinite(confidence) ? `${Math.round(confidence * 100)}%` : '—'
}

function failureAnalysisBooleanLabel(value: boolean | null): string {
  return value === null ? '—' : value ? '是' : '否'
}

function failureAnalysisRunSelectionLabel(caseRun: CaseRun): string {
  return `#${caseRun.sequence_no} · ${caseRunLabel(caseRun)} · ${nodeStatusLabel(caseRun.status)}`
}

function failureAnalysisConfigKeyForRun(run: RunDetail): string {
  return failureAnalysisConfigKey(run)
}

async function loadFailureAnalysisConfiguration(run: RunDetail): Promise<void> {
  const key = failureAnalysisConfigKeyForRun(run)
  if (failureAnalysisConfigInFlightKey === key || failureAnalysisConfigLoadedKey === key) return
  const requestSequence = failureAnalysisRequestSequence
  failureAnalysisConfigInFlightKey = key
  failureAnalysisPromptLoading.value = true
  failureAnalysisSchemaLoading.value = true
  failureAnalysisBindingLoading.value = true
  failureAnalysisPromptError.value = null
  failureAnalysisSchemaError.value = null
  failureAnalysisBindingError.value = null

  const [promptResult, schemaResult, bindingResult] = await Promise.allSettled([
    getPrompts(false, 'WEB_FAILURE_ANALYSIS'),
    getOutputSchemas(false),
    getModelBindings(run.project_id),
  ])
  if (isCurrentFailureAnalysisRequest(requestSequence, run.id, run.project_id)) {
    if (promptResult.status === 'fulfilled') failureAnalysisPrompts.value = promptResult.value
    else failureAnalysisPromptError.value = safeErrorMessage(promptResult.reason, 'WEB_FAILURE_ANALYSIS Prompt 加载失败，请稍后重试')
    if (schemaResult.status === 'fulfilled') failureAnalysisOutputSchemas.value = schemaResult.value
    else failureAnalysisSchemaError.value = safeErrorMessage(schemaResult.reason, '输出 Schema 加载失败，请稍后重试')
    if (bindingResult.status === 'fulfilled') failureAnalysisBindings.value = bindingResult.value
    else failureAnalysisBindingError.value = safeErrorMessage(bindingResult.reason, '项目 WEB_FAILURE_ANALYSIS 模型绑定加载失败，请稍后重试')
    failureAnalysisConfigLoadedKey = key
    const firstPrompt = availableFailureAnalysisPrompts.value[0]
    selectedFailureAnalysisPromptId.value = firstPrompt?.id
    if (!firstPrompt && !failureAnalysisPromptError.value && !failureAnalysisSchemaError.value) {
      failureAnalysisPromptError.value = '当前没有启用且绑定输出 Schema 的 Web 失败分析 Prompt，请前往 Prompt 中心配置。'
    }
    if (!failureAnalysisBinding.value && !failureAnalysisBindingError.value) {
      failureAnalysisBindingError.value = '当前项目缺少 WEB_FAILURE_ANALYSIS 模型绑定，请前往项目设置配置。'
    }
  }
  if (failureAnalysisConfigInFlightKey === key && requestSequence === failureAnalysisRequestSequence) {
    failureAnalysisConfigInFlightKey = null
    failureAnalysisPromptLoading.value = false
    failureAnalysisSchemaLoading.value = false
    failureAnalysisBindingLoading.value = false
  }
}

async function loadFailureAnalysisHistory(
  run: RunDetail,
  caseRun: CaseRun,
  preferredId?: number,
): Promise<void> {
  const key = failureAnalysisHistoryKey(run, caseRun)
  if (failureAnalysisHistoryInFlightKey === key || failureAnalysisHistoryLoadedKey === key) return
  const requestSequence = failureAnalysisRequestSequence
  failureAnalysisHistoryInFlightKey = key
  failureAnalysisHistoryLoading.value = true
  failureAnalysisHistoryError.value = null
  try {
    const response = await getWebFailureAnalyses(run.id, caseRun.id)
    if (!isCurrentFailureAnalysisRequest(requestSequence, run.id, run.project_id, caseRun.id)) return
    const items = response.items
      .filter((item) => item.run_id === run.id && item.case_run_id === caseRun.id)
      .slice()
      .sort((left, right) => {
        const leftTime = apiDateTimeMs(left.created_at)
        const rightTime = apiDateTimeMs(right.created_at)
        if (Number.isFinite(leftTime) && Number.isFinite(rightTime) && leftTime !== rightTime) return rightTime - leftTime
        return right.id - left.id
      })
    failureAnalysisItems.value = items
    failureAnalysisSelectedId.value = preferredId !== undefined && items.some((item) => item.id === preferredId)
      ? preferredId
      : items[0]?.id ?? null
    failureAnalysisHistoryLoadedKey = key
    failureAnalysisGenerationTimeoutNotice.value = null
  } catch (error) {
    if (isCurrentFailureAnalysisRequest(requestSequence, run.id, run.project_id, caseRun.id)) {
      failureAnalysisHistoryError.value = safeErrorMessage(error, 'AI Web 失败分析历史加载失败，请稍后重试')
      failureAnalysisHistoryLoadedKey = key
    }
  } finally {
    if (failureAnalysisHistoryInFlightKey === key && requestSequence === failureAnalysisRequestSequence) {
      failureAnalysisHistoryInFlightKey = null
      failureAnalysisHistoryLoading.value = false
    }
  }
}

function reloadFailureAnalysisHistory(): void {
  const run = detailRun.value
  const caseRun = failureAnalysisTargetCaseRun.value
  if (!run || !caseRun || !failureAnalysisVisible.value) return
  const key = failureAnalysisHistoryKey(run, caseRun)
  if (failureAnalysisHistoryInFlightKey === key) return
  failureAnalysisHistoryLoadedKey = null
  void loadFailureAnalysisHistory(run, caseRun)
}

function ensureFailureAnalysisData(run: RunDetail): void {
  if (!failureAnalysisVisible.value) return
  const configKey = failureAnalysisConfigKeyForRun(run)
  if (failureAnalysisConfigLoadedKey !== configKey && failureAnalysisConfigInFlightKey !== configKey) {
    void loadFailureAnalysisConfiguration(run)
  }
  const caseRun = failureAnalysisTargetCaseRun.value
  if (caseRun && failureAnalysisHistoryLoadedKey !== failureAnalysisHistoryKey(run, caseRun)) {
    void loadFailureAnalysisHistory(run, caseRun)
  }
}

function selectFailureAnalysisCaseRun(caseRunId: number): void {
  const caseRun = failureAnalysisCaseRuns.value.find((item) => item.id === caseRunId)
  const run = detailRun.value
  if (!caseRun || !run) return
  failureAnalysisCaseRunId.value = caseRunId
  invalidateFailureAnalysisHistory()
  if (failureAnalysisConfigLoadedKey !== failureAnalysisConfigKey(run)) {
    void loadFailureAnalysisConfiguration(run)
  }
  void loadFailureAnalysisHistory(run, caseRun)
}

function reloadFailureAnalysisConfiguration(): void {
  const run = detailRun.value
  if (!run || !failureAnalysisVisible.value) return
  const key = failureAnalysisConfigKey(run)
  if (failureAnalysisConfigInFlightKey === key) return
  failureAnalysisConfigLoadedKey = null
  void loadFailureAnalysisConfiguration(run)
}

async function generateFailureAnalysis(): Promise<void> {
  const run = detailRun.value
  const caseRun = failureAnalysisTargetCaseRun.value
  const promptId = selectedFailureAnalysisPromptId.value
  if (!run || !caseRun || !promptId || !failureAnalysisConfigReady.value
    || failureAnalysisGenerating.value || failureAnalysisGenerationTimeoutNotice.value) return
  const additionalInstructions = failureAnalysisAdditionalInstructions.value.trim()
  if (additionalInstructions.length > 5000 || /[\r\n]/.test(additionalInstructions) || failureAnalysisInputHasSensitiveText(additionalInstructions)) {
    ElMessage.warning('补充说明最多 5000 个字符、不能换行、不能包含敏感信息或 URL')
    return
  }
  const requestSequence = failureAnalysisRequestSequence
  const caseRunId = caseRun.id
  failureAnalysisGenerationTimeoutNotice.value = null
  failureAnalysisGenerating.value = true
  try {
    const response = await generateWebFailureAnalysis(run.id, {
      case_run_id: caseRunId,
      prompt_id: promptId,
      additional_instructions: additionalInstructions || null,
    })
    if (!isCurrentFailureAnalysisRequest(requestSequence, run.id, run.project_id, caseRunId)) return
    failureAnalysisItems.value = [response, ...failureAnalysisItems.value.filter((item) => item.id !== response.id)]
    failureAnalysisSelectedId.value = response.id
    failureAnalysisHistoryLoadedKey = null
    await loadFailureAnalysisHistory(run, caseRun, response.id)
    if (isCurrentFailureAnalysisRequest(requestSequence, run.id, run.project_id, caseRunId)) {
      ElMessage.success(response.status === 'COMPLETED' ? 'AI Web 失败分析已完成' : 'AI Web 失败分析已生成，正在处理')
    }
  } catch (error) {
    if (isCurrentFailureAnalysisRequest(requestSequence, run.id, run.project_id, caseRunId)) {
      if (isAiGenerationRequestTimeout(error)) {
        const timeoutSeconds = Math.round(WEB_FAILURE_ANALYSIS_GENERATION_TIMEOUT_MS / 1000)
        failureAnalysisGenerationTimeoutNotice.value = `AI Web 失败分析请求已等待 ${timeoutSeconds} 秒，服务端可能仍在处理，结果也可能已经落库。请先刷新分析历史核对，不要重复生成。`
        ElMessage.warning(failureAnalysisGenerationTimeoutNotice.value)
      } else {
        ElMessage.error(safeErrorMessage(error, 'AI Web 失败分析生成失败，请稍后重试'))
      }
    }
  } finally {
    if (requestSequence === failureAnalysisRequestSequence) failureAnalysisGenerating.value = false
  }
}

function selectHealingTrace(trace: WebExecutionTrace): void {
  const run = detailRun.value
  const caseRun = run ? caseRunForTrace(run, trace) : null
  if (!run || !detailVisible.value || !pageActive.value || !canOfferHealing(trace) || !caseRun) return
  const key = healingTraceSelectionKey(run, caseRun, trace)
  if (selectedHealingTraceKey.value === key) return

  invalidateHealingContext()
  selectedHealingTraceKey.value = key
  const selection = healingSelection.value
  if (!selection) return
  const requestSequence = healingRequestSequence
  void Promise.all([
    loadHealingConfiguration(selection, requestSequence),
    loadHealingProposalHistory(selection, requestSequence),
  ])
}

async function generateHealingProposalForSelection(): Promise<void> {
  const selection = healingSelection.value
  const promptId = selectedHealingPromptId.value
  if (!selection || !promptId || !healingConfigReady.value
    || healingGenerating.value || healingGenerationTimeoutNotice.value) return
  if (healingUsesScreenshot.value && !selectedHealingScreenshotId.value) {
    ElMessage.warning('视觉或智能定位阶段必须选择当前失败用例运行的截图证据')
    return
  }
  const additionalInstructions = healingAdditionalInstructions.value.trim()
  if (additionalInstructions.length > 5000
    || additionalInstructions.includes('\n')
    || additionalInstructions.includes('\r')
    || /(?:authorization|bearer|cookie|token|password|passwd|secret|credential|api[-_]?key)/i.test(additionalInstructions)) {
    ElMessage.warning('补充说明最多 5000 个字符、不能换行且不能包含凭据')
    return
  }
  const requestSequence = healingRequestSequence
  const key = selection.key
  healingGenerationTimeoutNotice.value = null
  healingGenerating.value = true
  try {
    const response = await generateWebHealingProposal(selection.run.id, {
      case_run_id: selection.caseRun.id,
      node_id: selection.trace.node_id,
      prompt_id: promptId,
      additional_instructions: additionalInstructions || null,
      analysis_stage: selectedHealingAnalysisStage.value,
      screenshot_artifact_id: healingUsesScreenshot.value ? selectedHealingScreenshotId.value : null,
    })
    if (!isCurrentHealingRequest(requestSequence, key)) return
    healingProposals.value = [
      response,
      ...healingProposals.value.filter((proposal) => proposal.id !== response.id),
    ]
    selectedHealingProposalId.value = response.id
    syncHealingCandidateSelection(response)
    await loadHealingProposalHistory(selection, requestSequence, response.id)
    if (isCurrentHealingRequest(requestSequence, key)) ElMessage.success('自愈提案已生成，请选择候选定位器后人工审核')
  } catch (error) {
    if (isCurrentHealingRequest(requestSequence, key)) {
      if (isAiGenerationRequestTimeout(error)) {
        const timeoutSeconds = Math.round(WEB_HEALING_GENERATION_TIMEOUT_MS / 1000)
        healingGenerationTimeoutNotice.value = `自愈提案请求已等待 ${timeoutSeconds} 秒，服务端可能仍在处理，结果也可能已经落库。请先刷新提案历史核对，不要重复生成。`
        ElMessage.warning(healingGenerationTimeoutNotice.value)
      } else {
        ElMessage.error(safeErrorMessage(error, '自愈提案生成失败，请稍后重试'))
      }
    }
  } finally {
    if (isCurrentHealingRequest(requestSequence, key)) healingGenerating.value = false
  }
}

async function validateSelectedHealingCandidate(): Promise<void> {
  const selection = healingSelection.value
  const proposal = selectedHealingProposal.value
  const candidate = selectedHealingCandidate.value
  if (!selection || !proposal || proposal.status !== 'DRAFT' || !candidate
    || healingValidating.value || healingValidationActive.value) return
  if (selectedHealingCandidateValidated.value) {
    ElMessage.success('所选 Locator 已通过验证 Run')
    return
  }
  const requestSequence = healingRequestSequence
  const key = selection.key
  healingValidating.value = true
  try {
    const response = await validateWebHealingProposal(selection.run.id, proposal.id, {
      locator: candidate.locator,
    })
    if (!isCurrentHealingRequest(requestSequence, key)) return
    healingProposals.value = [
      response,
      ...healingProposals.value.filter((item) => item.id !== response.id),
    ]
    selectedHealingProposalId.value = response.id
    syncHealingCandidateSelection(response)
    const validation = response.latest_validation
    ElMessage.success(validation
      ? `验证 Run ${validation.run_code} 已投递；成功后刷新提案历史即可接受`
      : '验证运行已投递')
    void loadRuns({ silent: true })
  } catch (error) {
    if (isCurrentHealingRequest(requestSequence, key)) {
      ElMessage.error(safeErrorMessage(error, 'Healing 候选验证 Run 创建失败，请稍后重试'))
    }
  } finally {
    if (isCurrentHealingRequest(requestSequence, key)) healingValidating.value = false
  }
}

async function openLatestHealingValidationRun(): Promise<void> {
  const runId = selectedHealingProposal.value?.latest_validation?.run_id
  if (!runId) return
  try {
    const run = await getRun(runId)
    await openRunDetail(run)
  } catch (error) {
    ElMessage.error(safeErrorMessage(error, '验证 Run 读取失败，请稍后重试'))
  }
}

function openHealingDecision(mode: 'accept' | 'reject'): void {
  const proposal = selectedHealingProposal.value
  if (!proposal || proposal.status !== 'DRAFT' || !healingSelection.value) return
  if (mode === 'accept' && !selectedHealingCandidate.value) {
    ElMessage.warning('请先从后端候选 Locator 中选择一项')
    return
  }
  if (mode === 'accept' && !selectedHealingCandidateValidated.value) {
    ElMessage.warning('请先运行并通过所选 Locator 的验证 Run')
    return
  }
  healingDecisionMode.value = mode
  healingDecisionNote.value = ''
  healingDecisionVisible.value = true
}

async function submitHealingDecision(): Promise<void> {
  const selection = healingSelection.value
  const proposal = selectedHealingProposal.value
  const candidate = selectedHealingCandidate.value
  if (!selection || !proposal || proposal.status !== 'DRAFT' || healingDecisionSaving.value) return
  if (healingDecisionMode.value === 'accept' && !candidate) {
    ElMessage.warning('请先从后端候选 Locator 中选择一项')
    return
  }
  if (healingDecisionMode.value === 'accept' && !selectedHealingCandidateValidated.value) {
    ElMessage.warning('所选 Locator 尚未通过验证 Run')
    return
  }
  const note = healingDecisionNote.value.trim()
  if (note.length > 500 || note.includes('\n') || note.includes('\r')
    || /(?:authorization|bearer|cookie|token|password|passwd|secret|credential|api[-_]?key)/i.test(note)) {
    ElMessage.warning('决策说明最多 500 个字符、不能换行且不能包含凭据')
    return
  }
  try {
    await ElMessageBox.confirm(
      healingDecisionMode.value === 'accept'
        ? '接受后只会创建新的 Web 用例或元素草稿版本，不会自动批准、自动重跑或覆盖旧版本。'
        : '拒绝后只保留审核记录，不会修改正式 Web Case，也不会触发重新运行。',
      healingDecisionMode.value === 'accept' ? '确认接受自愈提案？' : '确认拒绝自愈提案？',
      {
        type: 'warning',
        confirmButtonText: healingDecisionMode.value === 'accept' ? '确认接受' : '确认拒绝',
        cancelButtonText: '暂不处理',
      },
    )
  } catch {
    return
  }

  const requestSequence = healingRequestSequence
  const key = selection.key
  if (!isCurrentHealingRequest(requestSequence, key)) return
  healingDecisionSaving.value = true
  try {
    const response = healingDecisionMode.value === 'accept'
      ? await acceptWebHealingProposal(selection.run.id, proposal.id, {
        locator: candidate?.locator,
        decision_note: note || null,
      })
      : await rejectWebHealingProposal(selection.run.id, proposal.id, { decision_note: note || null })
    if (!isCurrentHealingRequest(requestSequence, key)) return
    healingDecisionVisible.value = false
    healingProposals.value = [
      response,
      ...healingProposals.value.filter((item) => item.id !== response.id),
    ]
    selectedHealingProposalId.value = response.id
    syncHealingCandidateSelection(response)
    await loadHealingProposalHistory(selection, requestSequence, response.id)
    if (!isCurrentHealingRequest(requestSequence, key)) return
    if (response.status === 'ACCEPTED') {
      const createdVersions = [
        response.created_web_case_version_id ? `Web Case Version #${response.created_web_case_version_id}` : '',
        response.created_element_version_id ? `Element Version #${response.created_element_version_id}` : '',
      ].filter(Boolean).join('、')
      ElMessage.success(`自愈提案已接受，已创建草稿 ${createdVersions || '版本'}；请前往 Web 用例管理审核批准，不会自动重跑`)
    } else {
      ElMessage.success('自愈提案已拒绝，未修改正式 Web 用例')
    }
  } catch (error) {
    if (isCurrentHealingRequest(requestSequence, key)) {
      ElMessage.error(safeErrorMessage(error, '自愈提案处理失败，请刷新后重试'))
    }
  } finally {
    if (isCurrentHealingRequest(requestSequence, key)) healingDecisionSaving.value = false
  }
}

function isCurrentHealingVersionRequest(
  requestSequence: number,
  runId: string,
  projectAtStart: number,
  expectedWebCaseId?: number,
): boolean {
  return requestSequence === healingVersionRequestSequence
    && pageActive.value
    && detailVisible.value
    && detailRun.value?.id === runId
    && projectId.value === projectAtStart
    && (expectedWebCaseId === undefined
      || (runType.value === 'WEB_CASE' && selectedWebCaseId.value === expectedWebCaseId))
}

function openHealingVersionReview(proposal: WebHealingProposalResponse): void {
  const run = detailRun.value
  const versionId = proposal.created_web_case_version_id
  if (!run || proposal.status !== 'ACCEPTED' || versionId === null || proposal.web_case_id <= 0
    || proposal.project_id !== run.project_id || run.project_id !== projectId.value) {
    ElMessage.warning('当前自愈提案缺少可定位的新 Web 用例版本')
    return
  }
  void router.push({
    name: route.meta.projectScoped ? 'project-web-assets' : 'web-assets',
    params: route.meta.projectScoped ? { projectId: run.project_id } : {},
    query: {
      project_id: String(run.project_id),
      web_case_id: String(proposal.web_case_id),
      version_id: String(versionId),
      run_id: run.id,
    },
  })
}

async function loadAcceptedHealingVersionIntoRunForm(proposal: WebHealingProposalResponse): Promise<void> {
  const selection = healingSelection.value
  const versionId = proposal.created_web_case_version_id
  if (!selection || proposal.status !== 'ACCEPTED' || versionId === null || proposal.web_case_id <= 0) return

  const run = selection.run
  const projectAtStart = run.project_id
  const runIdAtStart = run.id
  const formSelectionAtStart = runCreationSelectionKey()
  if (projectId.value !== projectAtStart || proposal.project_id !== projectAtStart) {
    ElMessage.warning('当前项目已变化，请重新打开原运行详情后再载入新版本')
    return
  }

  const requestSequence = ++healingVersionRequestSequence
  healingVersionLoading.value = true
  try {
    const [caseDetail, versions] = await Promise.all([
      getWebCase(proposal.web_case_id),
      getWebCaseVersions(proposal.web_case_id),
    ])
    if (!isCurrentHealingVersionRequest(requestSequence, runIdAtStart, projectAtStart)
      || runCreationSelectionKey() !== formSelectionAtStart) return

    const targetVersion = versions.find((version) => version.id === versionId)
    if (!targetVersion || targetVersion.web_case_id !== proposal.web_case_id
      || caseDetail.id !== proposal.web_case_id || caseDetail.project_id !== projectAtStart) {
      ElMessage.warning('新 Web Case 版本已不存在或不属于当前项目，请先刷新 Web 自动化资产')
      return
    }
    if (caseDetail.status !== 'APPROVED' || targetVersion.status !== 'APPROVED') {
      ElMessage.warning('新版本当前仍未批准，请先在 Web 自动化资产中人工批准后再载入运行表单')
      return
    }

    const existingCase = webCases.value.find((item) => item.id === caseDetail.id)
    webCases.value = existingCase
      ? webCases.value.map((item) => item.id === caseDetail.id ? caseDetail : item)
      : [...webCases.value, caseDetail]
    selectedWebCaseId.value = caseDetail.id
    runType.value = 'WEB_CASE'
    await nextTick()
    if (!isCurrentHealingVersionRequest(requestSequence, runIdAtStart, projectAtStart, caseDetail.id)) return
    const environmentBeforeVersionLoad = selectedEnvironmentId.value
    const runnerBeforeVersionLoad = selectedRunnerId.value
    const timeoutBeforeVersionLoad = totalTimeoutMsInput.value
    await loadWebCaseVersions(caseDetail.id, targetVersion.id)
    if (!isCurrentHealingVersionRequest(requestSequence, runIdAtStart, projectAtStart, caseDetail.id)
      || selectedEnvironmentId.value !== environmentBeforeVersionLoad
      || selectedRunnerId.value !== runnerBeforeVersionLoad
      || totalTimeoutMsInput.value !== timeoutBeforeVersionLoad) return

    const loadedVersion = webCaseVersions.value.find((version) => version.id === targetVersion.id)
    if (!loadedVersion || loadedVersion.status !== 'APPROVED' || loadedVersion.web_case_id !== caseDetail.id) {
      ElMessage.warning('新版本状态已变化，请刷新资源后重新确认批准状态')
      return
    }
    selectedWebCaseVersionId.value = loadedVersion.id
    selectedEnvironmentId.value = run.environment_id !== null
      && enabledEnvironments.value.some((environment) => environment.id === run.environment_id)
      ? run.environment_id
      : undefined
    const sourceRunner = run.runner_id ? runners.value.find((runner) => runner.id === run.runner_id) : undefined
    selectedRunnerId.value = sourceRunner && isRunnerSelectable(sourceRunner, 'WEB', 'WEB')
      ? sourceRunner.id
      : availableRunners.value[0]?.id
    totalTimeoutMsInput.value = Number.isInteger(run.total_timeout_ms)
      && (run.total_timeout_ms as number) >= 1000
      && (run.total_timeout_ms as number) <= 86_400_000
      ? run.total_timeout_ms
      : null
    validationResult.value = null
    detailVisible.value = false
    ElMessage.success(`已载入 Web 用例「${caseDetail.name}」V${loadedVersion.version_no}，请先校验并创建，再手动投递`)
  } catch (error) {
    if (isCurrentHealingVersionRequest(requestSequence, runIdAtStart, projectAtStart)) {
      ElMessage.error(safeErrorMessage(error, '新 Web Case 版本加载失败，请刷新后重试'))
    }
  } finally {
    if (requestSequence === healingVersionRequestSequence) healingVersionLoading.value = false
  }
}

function refreshProjectData(): void {
  if (projectId.value) void loadProjectData(projectId.value)
}

function openApiCaseManagement(): void {
  void router.push({
    name: route.meta.projectScoped ? 'project-test-cases' : 'test-cases',
    params: route.meta.projectScoped ? { projectId: projectId.value } : {},
    query: projectId.value ? { project_id: String(projectId.value) } : {},
  })
}

async function validateAndCreateApiBatch(): Promise<void> {
  const selectedIds = [...selectedApiCaseIds.value]
  const payloads = selectedIds.map((caseId) => ({ caseId, payload: apiCaseRunPayload(caseId) }))
  if (payloads.length < 2 || payloads.some((item) => item.payload === null)) {
    ElMessage.warning('请先选择项目、启用环境、至少两个有效 API 用例和可用 Runner')
    return
  }

  const requestSequence = ++runCreationSequence
  const selectionKey = runCreationSelectionKey()
  runCreationInFlightSequence = requestSequence
  validationLoading.value = true
  createLoading.value = true
  validationResult.value = null
  batchCreateSummary.value = null

  try {
    const outcomes = await Promise.all(payloads.map(async ({ caseId, payload }) => {
      const testCase = activeApiCases.value.find((item) => item.id === caseId)
      const label = testCase ? caseLabel(testCase) : `API 用例 ${caseId}`
      try {
        const validation = await validateRun(payload as RunCreateRequest)
        if (!validation.valid) {
          return {
            failure: {
              caseId,
              caseLabel: label,
              message: validation.issues.map((issue) => issue.message).join('；') || '运行校验未通过',
            } satisfies BatchCreateFailure,
          }
        }
        const created = await createRun(payload as RunCreateRequest)
        return { created }
      } catch (error) {
        return {
          failure: {
            caseId,
            caseLabel: label,
            message: safeErrorMessage(error, '运行校验或创建失败'),
          } satisfies BatchCreateFailure,
        }
      }
    }))

    if (!isCurrentRunCreationRequest(requestSequence, selectionKey)) return
    const created = outcomes.flatMap((outcome) => 'created' in outcome && outcome.created ? [outcome.created] : [])
    const failures = outcomes.flatMap((outcome) => 'failure' in outcome && outcome.failure ? [outcome.failure] : [])
    batchCreateSummary.value = {
      requested: selectedIds.length,
      created: created.length,
      failures,
    }
    createdRun.value = created.length === 1 ? created[0] : null
    if (failures.length === 0) {
      ElMessage.success(`已校验并创建 ${created.length} 条运行，可在运行列表中一键运行`)
    } else if (created.length > 0) {
      ElMessage.warning(`已创建 ${created.length} 条，另有 ${failures.length} 条未通过；请查看失败原因`)
    } else {
      ElMessage.error('所选 API 用例均未通过校验，未创建运行')
    }
    await loadRuns({ silent: true })
  } finally {
    if (runCreationInFlightSequence === requestSequence) {
      runCreationInFlightSequence = null
      validationLoading.value = false
      createLoading.value = false
    }
  }
}

async function validateAndCreate(): Promise<void> {
  if (runType.value === 'API_CASE' && selectedApiCaseIds.value.length > 1) {
    await validateAndCreateApiBatch()
    return
  }
  const payload = runPayload.value
  if (!payload) {
    if (runType.value === 'API_CASE' && apiCaseRunBlockMessage.value) {
      ElMessage.warning(apiCaseRunBlockMessage.value)
      return
    }
    ElMessage.warning(runType.value === 'SCENARIO'
      ? '请先选择项目、启用环境、可执行 Scenario 和可用 Runner'
      : runType.value === 'WEB_CASE'
        ? '请先选择项目、启用环境、APPROVED Web Case 版本和可用 Runner'
        : '请先选择项目、启用环境、ACTIVE API 用例和可用 Runner')
    return
  }

  const requestSequence = ++runCreationSequence
  const selectionKey = runCreationSelectionKey()
  runCreationInFlightSequence = requestSequence
  validationLoading.value = true
  validationResult.value = null
  try {
    const validation = await validateRun(payload)
    if (!isCurrentRunCreationRequest(requestSequence, selectionKey)) return
    validationResult.value = validation
    if (!validation.valid) {
      ElMessage.warning('运行校验未通过，请根据下方问题调整选择')
      return
    }

    createLoading.value = true
    const created = await createRun(payload)
    if (!isCurrentRunCreationRequest(requestSequence, selectionKey)) return
    createdRun.value = created
    validationResult.value = null
    ElMessage.success(`运行 ${created.run_code} 已创建，可在运行列表中启动`)
    await loadRuns({ silent: true })
  } catch (error) {
    if (!isCurrentRunCreationRequest(requestSequence, selectionKey)) return
    const message = safeErrorMessage(error, '运行校验或创建失败，请稍后重试')
    ElMessage.error(message)
  } finally {
    if (runCreationInFlightSequence === requestSequence) {
      runCreationInFlightSequence = null
      validationLoading.value = false
      createLoading.value = false
    }
  }
}

function isCurrentDetailRequest(
  requestSequence: number,
  runId: string,
  requestedProjectId: number | undefined,
  allowHidden: boolean,
): boolean {
  return requestSequence === detailRequestSequence
    && projectId.value === requestedProjectId
    && (allowHidden || (detailVisible.value && detailTargetRunId.value === runId))
}

async function fetchRunDetail(
  runId: string,
  options: { showLoading?: boolean; allowHidden?: boolean } = {},
): Promise<RunDetail | null> {
  const showLoading = options.showLoading ?? true
  const allowHidden = options.allowHidden ?? false
  if (detailInFlight.value) return null

  const requestSequence = ++detailRequestSequence
  const requestedProjectId = projectId.value
  detailInFlightSequence = requestSequence
  detailInFlight.value = true
  if (showLoading) detailLoading.value = true

  try {
    const detail = await getRun(runId)
    if (!isCurrentDetailRequest(requestSequence, runId, requestedProjectId, allowHidden)) return null
    if (detailVisible.value && detailTargetRunId.value === runId) {
      detailRun.value = detail
      detailError.value = null
      if (selectedHealingTraceKey.value && !healingSelection.value) invalidateHealingContext()
      syncFailureAnalysisTarget(detail)
      ensureFailureAnalysisData(detail)
    }
    syncCreatedRun(detail)
    return detail
  } catch (error) {
    if (isCurrentDetailRequest(requestSequence, runId, requestedProjectId, allowHidden)
      && detailVisible.value && detailTargetRunId.value === runId) {
      detailError.value = safeErrorMessage(error, '运行详情加载失败，请稍后重试')
    }
    return null
  } finally {
    if (detailInFlightSequence === requestSequence) {
      detailInFlightSequence = null
      detailInFlight.value = false
      if (showLoading) detailLoading.value = false
    }
  }
}

function isCurrentEvidenceRequest(
  requestSequence: number,
  requestedProjectId: number,
  runId: string,
): boolean {
  return requestSequence === evidenceRequestSequence
    && projectId.value === requestedProjectId
    && detailVisible.value
    && detailTargetRunId.value === runId
}

async function fetchEvidence(projectIdForRequest: number, runId: string): Promise<void> {
  if (evidenceInFlight.value) return

  const requestSequence = ++evidenceRequestSequence
  evidenceInFlightSequence = requestSequence
  evidenceInFlight.value = true
  evidenceLoading.value = true
  evidenceError.value = null

  try {
    const response = await getEvidence(projectIdForRequest, runId)
    if (!isCurrentEvidenceRequest(requestSequence, projectIdForRequest, runId)) return
    evidenceItems.value = response.items
  } catch (error) {
    if (isCurrentEvidenceRequest(requestSequence, projectIdForRequest, runId)) {
      evidenceError.value = safeErrorMessage(error, '运行证据加载失败，请稍后重试')
    }
  } finally {
    if (evidenceInFlightSequence === requestSequence) {
      evidenceInFlightSequence = null
      evidenceInFlight.value = false
      evidenceLoading.value = false
    }
  }
}

function refreshEvidence(): void {
  if (detailRun.value) void fetchEvidence(detailRun.value.project_id, detailRun.value.id)
}

async function downloadEvidenceFile(artifact: EvidenceArtifact): Promise<void> {
  if (evidenceDownloadId.value !== null) return

  evidenceDownloadId.value = artifact.id
  try {
    const blob = await downloadEvidenceBlob(artifact.id)
    const objectUrl = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = objectUrl
    anchor.download = safeDownloadFileName(artifact.file_name)
    anchor.rel = 'noopener'
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0)
  } catch (error) {
    ElMessage.error(safeErrorMessage(error, '证据下载失败，请稍后重试'))
  } finally {
    evidenceDownloadId.value = null
  }
}

async function openRunDetail(run: Run): Promise<void> {
  invalidateDetailRequest()
  invalidateEvidenceRequest()
  invalidateHealingContext()
  invalidateFailureAnalysisContext()
  detailVisible.value = true
  detailTargetRunId.value = run.id
  detailRun.value = null
  evidenceItems.value = []
  evidenceError.value = null
  detailError.value = null
  dispatchMessage.value = null
  startRunEventStream(run)
  await Promise.all([
    fetchRunDetail(run.id),
    fetchEvidence(run.project_id, run.id),
  ])
}

function openRunReport(run: Run): void {
  void router.push({
    name: route.meta.projectScoped ? 'project-report-detail' : 'report-detail',
    params: route.meta.projectScoped ? { projectId: run.project_id, runId: run.id } : { runId: run.id },
  })
}

async function refreshAfterDispatch(run: Run, runStatus: RunStatus): Promise<void> {
  const authoritativeDetail = await fetchRunDetail(run.id, { showLoading: false, allowHidden: true })
  if (authoritativeDetail) return

  if (createdRun.value?.id === run.id) {
    createdRun.value = { ...createdRun.value, status: runStatus }
    syncDispatchMessageForCreatedRun(createdRun.value)
  }
  if (detailVisible.value && detailTargetRunId.value === run.id && detailRun.value?.id === run.id) {
    detailRun.value = { ...detailRun.value, status: runStatus }
  }
}

async function syncRunDetailAfterCancel(
  detail: RunDetail,
  requestedProjectId: number,
  requestedProjectSequence: number,
): Promise<void> {
  if (!isCurrentRunRequestContext(requestedProjectId, requestedProjectSequence)
    || detail.project_id !== requestedProjectId) return

  syncCreatedRun(detail)
  if (detailVisible.value && detailTargetRunId.value === detail.id) {
    invalidateDetailRequest()
    detailRun.value = detail
    detailError.value = null
    invalidateEvidenceRequest()
    await fetchEvidence(detail.project_id, detail.id)
  }
  await loadRuns({ silent: true })
}

async function refreshRunAfterCancelConflict(
  run: Run,
  requestedProjectId: number,
  requestedProjectSequence: number,
): Promise<void> {
  if (!isCurrentRunRequestContext(requestedProjectId, requestedProjectSequence)
    || requestedProjectId !== run.project_id) return

  invalidateDetailRequest()
  const authoritativeDetail = await fetchRunDetail(run.id, { showLoading: false, allowHidden: true })
  if (authoritativeDetail
    && detailVisible.value
    && detailTargetRunId.value === authoritativeDetail.id
    && projectId.value === authoritativeDetail.project_id) {
    invalidateEvidenceRequest()
    await fetchEvidence(authoritativeDetail.project_id, authoritativeDetail.id)
  }
  await loadRuns({ silent: true })
}

async function cancelSelectedRun(run: Run): Promise<void> {
  if (!isCancellableRun(run.status)
    || dispatchingRunId.value !== null
    || cancellingRunId.value !== null
    || forceStoppingRunId.value !== null) return

  try {
    await ElMessageBox.confirm(
      cancelConfirmationMessage(run.status),
      `取消 Run ${run.run_code}？`,
      {
        type: 'warning',
        confirmButtonText: '确认取消',
        cancelButtonText: '暂不取消',
      },
    )
  } catch {
    return
  }

  if (projectId.value !== run.project_id
    || !pageActive.value
    || dispatchingRunId.value !== null
    || cancellingRunId.value !== null
    || forceStoppingRunId.value !== null) return
  const requestedProjectId = run.project_id
  const requestedProjectSequence = projectLoadSequence
  cancellingRunId.value = run.id
  try {
    const detail = await cancelRun(run.id)
    if (!isCurrentRunRequestContext(requestedProjectId, requestedProjectSequence)) return
    await syncRunDetailAfterCancel(detail, requestedProjectId, requestedProjectSequence)
    if (detail.status === 'CANCELLED') {
      ElMessage.success('Run 已取消')
    } else {
      ElMessage.success(`运行状态已更新为：${runStatusLabel(detail.status)}`)
    }
  } catch (error) {
    if (!isCurrentRunRequestContext(requestedProjectId, requestedProjectSequence)) return
    if (apiErrorStatus(error) === 409) {
      const message = 'Run 已进入执行阶段或已被处理，取消未生效；正在刷新最新状态。'
      ElMessage.warning(message)
      await refreshRunAfterCancelConflict(run, requestedProjectId, requestedProjectSequence)
    } else {
      const message = safeErrorMessage(error, 'Run 取消失败，请稍后重试')
      ElMessage.error(message)
    }
  } finally {
    cancellingRunId.value = null
  }
}

async function dispatchSelectedRun(run: Run): Promise<void> {
  if (run.status !== 'CREATED'
    || batchDispatching.value
    || dispatchingRunId.value !== null
    || cancellingRunId.value !== null
    || forceStoppingRunId.value !== null) return

  dispatchingRunId.value = run.id
  dispatchMessage.value = null
  try {
    const response = await dispatchRun(run.id)
    if (response.outbox_status === 'PUBLISHED') {
      dispatchMessage.value = `运行已投递，当前状态：${runStatusLabels[response.run_status]}`
      ElMessage.success('运行已投递')
    } else {
      dispatchMessage.value = 'Run 投递未完成，请查看当前投递状态后再处理'
      ElMessage.error('Run 投递未成功，请查看当前投递状态')
    }
    await refreshAfterDispatch(run, response.run_status)
    await loadRuns({ silent: true })
  } catch (error) {
    const message = safeErrorMessage(error, 'Run 投递失败，请稍后重试')
    dispatchMessage.value = message
    ElMessage.error(message)
  } finally {
    dispatchingRunId.value = null
  }
}

async function loadAllCreatedRuns(project: number): Promise<Run[]> {
  const firstPage = await getRuns(project, 1, 100)
  const pageCount = Math.ceil(firstPage.total / 100)
  const remainingPages = pageCount > 1
    ? await Promise.all(Array.from({ length: pageCount - 1 }, (_, index) => getRuns(project, index + 2, 100)))
    : []
  const uniqueRuns = new Map<string, Run>()
  for (const run of [firstPage, ...remainingPages].flatMap((page) => page.items)) {
    uniqueRuns.set(run.id, run)
  }
  return [...uniqueRuns.values()].filter((run) => run.status === 'CREATED')
}

async function dispatchAllCreatedRuns(): Promise<void> {
  const currentProjectId = projectId.value
  if (!currentProjectId || batchDispatching.value
    || dispatchingRunId.value !== null
    || cancellingRunId.value !== null
    || forceStoppingRunId.value !== null) return

  batchDispatching.value = true
  let pendingRuns: Run[]
  try {
    pendingRuns = await loadAllCreatedRuns(currentProjectId)
  } catch (error) {
    ElMessage.error(safeErrorMessage(error, '待执行运行加载失败，请稍后重试'))
    batchDispatching.value = false
    return
  }
  if (projectId.value !== currentProjectId || pendingRuns.length === 0) {
    ElMessage.info('当前项目没有待执行的运行')
    batchDispatching.value = false
    return
  }

  try {
    await ElMessageBox.confirm(
      `将当前项目 ${pendingRuns.length} 条“已创建”运行一次性投递到 Runner 队列。已在执行、已完成或失败的记录不会重复投递。`,
      '一键运行待执行项？',
      {
        type: 'warning',
        confirmButtonText: `运行 ${pendingRuns.length} 条`,
        cancelButtonText: '取消',
      },
    )
  } catch {
    batchDispatching.value = false
    return
  }

  if (projectId.value !== currentProjectId) {
    batchDispatching.value = false
    return
  }
  try {
    const response = await dispatchRunsBatch(currentProjectId, pendingRuns.map((run) => run.id))
    if (projectId.value !== currentProjectId) return
    const succeeded = response.dispatched
    const failed = response.failed
    await loadRuns({ silent: true })
    if (detailVisible.value && detailRun.value && pendingRuns.some((run) => run.id === detailRun.value?.id)) {
      await fetchRunDetail(detailRun.value.id, { showLoading: false })
    }
    if (failed === 0) {
      ElMessage.success(`已投递 ${succeeded} 条运行，Runner 将按可用槽位执行`)
    } else {
      ElMessage.warning(`已投递 ${succeeded} 条，${failed} 条未投递；运行列表已刷新，可按记录状态处理或重试`)
    }
  } catch (error) {
    ElMessage.error(safeErrorMessage(error, '批量投递失败，请稍后重试'))
  } finally {
    batchDispatching.value = false
  }
}

function handlePageChange(page: number): void {
  runPage.value = page
  void loadRuns()
}

function clearPollTimer(): void {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

function canPoll(): boolean {
  return pageActive.value && document.visibilityState === 'visible' && projectId.value !== undefined
}

async function pollCurrentData(): Promise<void> {
  if (!canPoll()) {
    clearPollTimer()
    return
  }
  void loadRuns({ silent: true })
  const currentDetail = detailRun.value
  if (detailVisible.value && currentDetail) {
    await fetchRunDetail(currentDetail.id, { showLoading: false })
  }
  const pendingCreatedRun = createdRun.value
  if (pendingCreatedRun && !isTerminalRunStatus(pendingCreatedRun.status)
    && pendingCreatedRun.id !== currentDetail?.id) {
    await fetchRunDetail(pendingCreatedRun.id, { showLoading: false, allowHidden: true })
  }
}

function startPolling(): void {
  clearPollTimer()
  if (!canPoll()) return
  pollTimer = window.setInterval(() => {
    if (canPoll()) {
      void pollCurrentData()
    } else {
      clearPollTimer()
    }
  }, POLL_INTERVAL_MS)
}

function handleVisibilityChange(): void {
  if (document.visibilityState === 'visible') {
    startPolling()
    void pollCurrentData()
  } else {
    clearPollTimer()
  }
}

watch(projectId, (id, previousId) => {
  if (id === previousId) return
  invalidateRunCreationRequest()
  invalidateHealingContext()
  invalidateFailureAnalysisContext()
  if (id) {
    void loadProjectData(id)
    startPolling()
  } else {
    clearPollTimer()
    invalidateDetailRequest()
    invalidateEvidenceRequest()
    invalidateWebCaseVersionRequest()
    invalidateApiCaseDetailRequest()
    invalidateFailureAnalysisContext()
    stopRunEventStream()
    detailVisible.value = false
    detailTargetRunId.value = null
    detailRun.value = null
    detailError.value = null
    evidenceItems.value = []
    evidenceError.value = null
  }
})

watch([projectId, selectedEnvironmentId, selectedApiCaseIds, selectedCaseId, selectedScenarioId, selectedScenarioVersionId, selectedWebCaseId, selectedWebCaseVersionId, selectedRunnerId, runType, totalTimeoutMsInput], () => {
  invalidateRunCreationRequest()
  validationResult.value = null
  batchCreateSummary.value = null
})

watch(selectedApiCaseIds, (caseIds) => {
  if (runType.value !== 'API_CASE') return
  selectedCaseId.value = caseIds[0]
  if (caseIds.length !== 1) {
    invalidateApiCaseDetailRequest()
    return
  }
  if (selectedApiCaseDetail.value?.id !== caseIds[0]) {
    void loadSelectedApiCaseDetail(caseIds[0])
  }
})

watch(selectedScenarioId, (scenarioId, previousScenarioId) => {
  if (scenarioId === previousScenarioId) return
  invalidateScenarioVersionRequest()
  if (scenarioId && runType.value === 'SCENARIO') {
    void loadScenarioVersions(scenarioId)
  }
})

watch(selectedCaseId, (caseId, previousCaseId) => {
  if (caseId === previousCaseId) return
  if (selectedApiCaseIds.value.length !== 1) return
  if (caseId && runType.value === 'API_CASE'
    && apiCaseDetailInFlightKey === `${projectId.value ?? ''}:${caseId}`) return
  invalidateApiCaseDetailRequest()
  if (caseId && runType.value === 'API_CASE') void loadSelectedApiCaseDetail(caseId)
})

watch(selectedWebCaseId, (webCaseId, previousWebCaseId) => {
  if (webCaseId === previousWebCaseId) return
  invalidateWebCaseVersionRequest()
  if (webCaseId && runType.value === 'WEB_CASE') void loadWebCaseVersions(webCaseId)
})

watch(runType, (type, previousType) => {
  if (type === previousType) return
  validationResult.value = null
  if (type === 'SCENARIO') {
    selectedCaseId.value = undefined
    invalidateApiCaseDetailRequest()
    selectedWebCaseId.value = undefined
    invalidateWebCaseVersionRequest()
    if (selectedScenarioId.value) void loadScenarioVersions(selectedScenarioId.value)
  } else if (type === 'WEB_CASE') {
    selectedCaseId.value = undefined
    invalidateApiCaseDetailRequest()
    selectedScenarioId.value = undefined
    invalidateScenarioVersionRequest()
    if (!selectedWebCaseId.value) selectedWebCaseId.value = executableWebCases.value[0]?.id
    else void loadWebCaseVersions(selectedWebCaseId.value)
  } else {
    selectedScenarioId.value = undefined
    selectedWebCaseId.value = undefined
    invalidateScenarioVersionRequest()
    invalidateWebCaseVersionRequest()
    if (selectedApiCaseIds.value.length === 0 && activeApiCases.value[0]) {
      selectedApiCaseIds.value = [activeApiCases.value[0].id]
    }
    selectedCaseId.value = selectedApiCaseIds.value[0]
    if (selectedCaseId.value) void loadSelectedApiCaseDetail(selectedCaseId.value)
  }
  if (!availableRunners.value.some((runner) => runner.id === selectedRunnerId.value)) {
    selectedRunnerId.value = availableRunners.value[0]?.id
  }
})

watch(detailVisible, (visible) => {
  if (!visible) {
    invalidateDetailRequest()
    invalidateEvidenceRequest()
    invalidateHealingContext()
    invalidateFailureAnalysisContext()
    stopRunEventStream()
    detailTargetRunId.value = null
    detailRun.value = null
    detailError.value = null
    evidenceItems.value = []
    evidenceError.value = null
    dispatchMessage.value = null
  }
})

watch(() => authStore.isAuthenticated, (isAuthenticated) => {
  if (!isAuthenticated) {
    stopRunEventStream('POLLING_FALLBACK', '登录状态已失效，实时连接已停止；当前保留 8 秒轮询。')
  }
})

onMounted(() => {
  document.addEventListener('visibilitychange', handleVisibilityChange)
  initializeRunDeepLink()
  void loadProjects()
  startPolling()
})

onActivated(() => {
  pageActive.value = true
  startPolling()
  if (detailVisible.value && detailRun.value) startRunEventStream(detailRun.value)
  if (projectId.value) void openRunFromDeepLink(projectId.value, projectLoadSequence)
  void pollCurrentData()
})

onDeactivated(() => {
  pageActive.value = false
  invalidateRunCreationRequest()
  invalidateHealingContext()
  invalidateFailureAnalysisContext()
  clearPollTimer()
  invalidateDetailRequest()
  invalidateEvidenceRequest()
  stopRunEventStream()
})

onUnmounted(() => {
  pageActive.value = false
  invalidateRunCreationRequest()
  invalidateHealingContext()
  invalidateFailureAnalysisContext()
  clearPollTimer()
  invalidateDetailRequest()
  invalidateEvidenceRequest()
  stopRunEventStream()
  document.removeEventListener('visibilitychange', handleVisibilityChange)
})
</script>

<template>
  <div class="runs-page">
    <div class="page-heading runs-heading">
      <div>
        <span class="eyebrow">运行控制</span>
        <h1>运行中心</h1>
        <p>可创建 API 用例、编排场景或 Web 用例运行。状态优先实时推送，异常时降级为 8 秒轮询；目标能力由服务端校验。</p>
      </div>
      <el-button :icon="Refresh" :loading="resourcesLoading || runListLoading" :disabled="!projectId" @click="refreshProjectData">
        刷新资源与运行
      </el-button>
    </div>

    <el-alert v-if="projectError" :title="projectError" type="error" show-icon :closable="false" />
    <el-alert v-if="runDeepLinkMessage" :title="runDeepLinkMessage" type="warning" show-icon :closable="false" />

    <el-card class="project-selector-card" shadow="never">
      <div class="selector-row">
        <div class="selector-field">
          <span class="field-label">当前项目</span>
          <el-select
            v-model="projectId"
            class="project-select"
            placeholder="请选择有效项目"
            :loading="projectsLoading"
            :disabled="projectsLoading || projects.length === 0"
          >
            <el-option v-for="project in projects" :key="project.id" :label="projectLabel(project)" :value="project.id" />
          </el-select>
        </div>
        <div v-if="currentProject" class="selected-project-note">
          <span>运行数据按项目隔离</span>
          <strong>{{ currentProject.name }}</strong>
        </div>
      </div>
      <el-empty v-if="!projectsLoading && projects.length === 0" description="暂无可运行的有效项目" :image-size="72" />
    </el-card>

    <template v-if="projectId">
      <el-alert v-if="resourcesError" :title="resourcesError" type="warning" show-icon :closable="false" class="section-alert" />

      <el-card class="run-target-switch-card" shadow="never">
        <div class="run-type-field">
          <span class="field-label">测试对象</span>
          <el-radio-group v-model="runType">
            <el-radio-button value="API_CASE">API 用例</el-radio-button>
            <el-radio-button value="SCENARIO">编排场景</el-radio-button>
            <el-radio-button value="WEB_CASE">Web 用例</el-radio-button>
          </el-radio-group>
          <span class="target-switch-hint">环境与 Runner 对本次选择的全部测试对象统一生效</span>
        </div>
      </el-card>

      <div class="resource-grid">
        <el-card v-loading="resourcesLoading" class="resource-card environment-resource-card" shadow="never">
          <div class="resource-card-header">
            <div>
              <span class="eyebrow">运行环境</span>
              <h2>运行环境</h2>
            </div>
            <el-tag type="info">{{ enabledEnvironments.length }} 个启用</el-tag>
          </div>
          <el-select v-model="selectedEnvironmentId" class="full-width" placeholder="选择启用环境" :disabled="resourcesLoading || enabledEnvironments.length === 0">
            <el-option v-for="environment in enabledEnvironments" :key="environment.id" :label="`${environment.name}（${environment.code}）`" :value="environment.id" />
          </el-select>
          <p v-if="!resourcesLoading && enabledEnvironments.length === 0" class="empty-hint">当前项目没有启用环境。</p>
        </el-card>

        <el-card v-loading="resourcesLoading" class="resource-card target-resource-card" shadow="never">
          <template v-if="runType === 'API_CASE'">
            <div class="resource-card-header">
              <div>
                <span class="eyebrow">API 用例</span>
                <h2>测试用例</h2>
              </div>
              <el-tag type="info">{{ activeApiCases.length }} 个有效</el-tag>
            </div>
            <el-select
              v-model="selectedApiCaseIds"
              class="full-width"
              placeholder="搜索并选择一个或多个有效 API 用例"
              multiple
              filterable
              collapse-tags
              collapse-tags-tooltip
              :max-collapse-tags="3"
              :disabled="resourcesLoading || activeApiCases.length === 0"
            >
              <el-option v-for="testCase in activeApiCases" :key="testCase.id" :label="caseLabel(testCase)" :value="testCase.id" />
            </el-select>
            <div class="multi-select-toolbar">
              <span>支持按名称或编号搜索；每个用例会生成一条独立运行记录。</span>
              <div>
                <el-button link type="primary" :disabled="selectedApiCaseIds.length === activeApiCases.length" @click="selectedApiCaseIds = activeApiCases.map((item) => item.id)">全选</el-button>
                <el-button link :disabled="selectedApiCaseIds.length === 0" @click="selectedApiCaseIds = []">清空</el-button>
              </div>
            </div>
            <p v-if="selectedApiCaseIds.length > 0" class="resource-meta">已选择 {{ selectedApiCaseIds.length }} 个 API 用例</p>
            <p v-if="!resourcesLoading && activeApiCases.length === 0" class="empty-hint">当前项目没有有效的 API 用例。</p>
            <el-alert
              v-else-if="selectedApiCaseIds.length === 1 && apiCaseRunBlockMessage"
              :title="apiCaseRunBlockMessage"
              :type="selectedApiCaseDetailLoading ? 'info' : 'error'"
              show-icon
              :closable="false"
              class="api-retry-gate-alert"
            >
              <template v-if="!selectedApiCaseDetailLoading" #default>
                <el-button link type="primary" @click="openApiCaseManagement">前往测试用例创建符合 V1 的新版本</el-button>
              </template>
            </el-alert>
          </template>
          <template v-else-if="runType === 'SCENARIO'">
            <div class="resource-card-header">
              <div>
                <span class="eyebrow">编排场景</span>
                <h2>测试场景</h2>
              </div>
              <el-tag type="info">{{ executableScenarios.length }} 个可执行</el-tag>
            </div>
            <el-select v-model="selectedScenarioId" class="full-width" placeholder="选择已批准场景" :disabled="resourcesLoading || executableScenarios.length === 0">
              <el-option v-for="scenario in executableScenarios" :key="scenario.id" :label="scenarioLabel(scenario)" :value="scenario.id" />
            </el-select>
            <el-select
              v-model="selectedScenarioVersionId"
              class="full-width scenario-version-select"
              placeholder="选择场景版本"
              :loading="scenarioVersionsLoading"
              :disabled="resourcesLoading || scenarioVersionsLoading || scenarioVersions.length === 0"
            >
              <el-option
                v-for="version in scenarioVersions"
                :key="version.id"
                :label="`V${version.version_no}${version.id === selectedScenario?.current_version_id ? ' · 当前' : ''}`"
                :value="version.id"
              />
            </el-select>
            <p v-if="scenarioVersionsError" class="empty-hint error-hint">{{ scenarioVersionsError }}</p>
            <p v-else-if="!resourcesLoading && executableScenarios.length === 0" class="empty-hint">当前项目暂无已批准场景；其他状态不会直接开放执行。</p>
            <p v-else-if="!scenarioVersionsLoading && selectedScenario && scenarioVersions.length === 0" class="empty-hint">当前场景没有可用版本，运行校验会阻止创建。</p>
          </template>
          <template v-else>
            <div class="resource-card-header">
              <div>
                <span class="eyebrow">Web 用例</span>
                <h2>Web 测试用例</h2>
              </div>
              <el-tag type="info">{{ executableWebCases.length }} 个可执行</el-tag>
            </div>
            <el-select v-model="selectedWebCaseId" class="full-width" placeholder="选择已批准 Web 用例" :disabled="resourcesLoading || executableWebCases.length === 0">
              <el-option v-for="webCase in executableWebCases" :key="webCase.id" :label="webCaseLabel(webCase)" :value="webCase.id" />
            </el-select>
            <el-select
              v-model="selectedWebCaseVersionId"
              class="full-width scenario-version-select"
              placeholder="选择已批准的 Web 用例版本"
              :loading="webCaseVersionsLoading"
              :disabled="resourcesLoading || webCaseVersionsLoading || approvedWebCaseVersions.length === 0"
            >
              <el-option
                v-for="version in approvedWebCaseVersions"
                :key="version.id"
                :label="`V${version.version_no}${version.id === selectedWebCase?.current_version_id ? ' · 当前' : ''}`"
                :value="version.id"
              />
            </el-select>
            <p v-if="webCaseVersionsError" class="empty-hint error-hint">{{ webCaseVersionsError }}</p>
            <template v-else-if="selectedWebCaseVersion">
              <p class="resource-meta">Chrome · {{ selectedWebCaseVersion.content.headless ? '无界面' : '有界面' }} · 总超时 {{ formatTotalTimeout(selectedWebCaseVersion.content.total_timeout_ms) }}</p>
              <p class="resource-meta">会话配置：{{ sessionProfileLabel(selectedWebCaseVersion.content.session_profile_id) }}</p>
              <p class="empty-hint">浏览器、模式与会话固定在不可变版本中；需到 Web 自动化资产保存新版本后再变更。</p>
            </template>
            <p v-else-if="!resourcesLoading && executableWebCases.length === 0" class="empty-hint">当前项目暂无已批准且有当前版本的 Web 用例；其他状态不会直接开放执行。</p>
            <p v-else-if="!webCaseVersionsLoading && selectedWebCase && approvedWebCaseVersions.length === 0" class="empty-hint">当前 Web 用例没有已批准版本，运行校验会阻止创建。</p>
          </template>
        </el-card>

        <el-card v-loading="resourcesLoading" class="resource-card runner-resource-card" shadow="never">
          <div class="resource-card-header">
            <div>
              <span class="eyebrow">执行器</span>
              <h2>执行 Runner</h2>
            </div>
            <el-tag :type="availableRunners.length > 0 ? 'success' : 'warning'">{{ availableRunners.length }} 个可用</el-tag>
          </div>
          <el-select v-model="selectedRunnerId" class="full-width" placeholder="选择可用 Runner" :disabled="resourcesLoading || availableRunners.length === 0">
            <el-option v-for="runner in availableRunners" :key="runner.id" :label="`${runner.name}（${runner.hostname}）`" :value="runner.id" />
          </el-select>
           <p v-if="!resourcesLoading && availableRunners.length === 0" class="empty-hint">仅允许状态有效、在线、Redis 可用、{{ selectedRunnerCapability }} 能力就绪且 {{ selectedRunnerSlotType }} 槽位不少于 1 的 Runner。</p>
           <p v-else-if="selectedRunner" class="resource-meta">{{ selectedRunnerSlotType }} 可用槽位：{{ selectedRunner.slots.find((slot) => slot.type === selectedRunnerSlotType)?.available ?? 0 }}</p>
        </el-card>
      </div>

      <el-card class="create-card" shadow="never">
        <div class="card-heading-row">
          <div>
            <span class="eyebrow">手动运行</span>
            <h2>创建 {{ runTypeLabel(runType) }} 运行</h2>
          </div>
           <el-tag type="info">{{ runTypeLabel(runType) }} · 手动触发 · {{ selectedRunnerSlotType }}/1</el-tag>
        </div>
        <p class="card-description">创建请求会先调用后端校验；API 用例支持批量校验并分别创建可追踪的运行记录。创建后可在运行列表中单条运行或一键运行全部待执行项。</p>

        <div class="fixed-run-fields">
          <span><b>运行类型</b> {{ runTypeLabel(runType) }}</span>
          <span><b>触发方式</b> 手动</span>
           <span><b>能力要求</b> {{ selectedRunnerCapability }}</span>
          <span><b>标签要求</b> 无</span>
           <span><b>槽位</b> {{ selectedRunnerSlotType }} / 1</span>
        </div>

        <div class="run-timeout-field">
          <span class="field-label">运行总超时（可选，不填使用系统默认；与步骤超时相互独立）</span>
          <el-input-number
            v-model="totalTimeoutMsInput"
            :min="1000"
            :max="86400000"
            :step="60000"
            :step-strictly="false"
            controls-position="right"
            placeholder="系统默认"
          />
        </div>

        <div v-if="validationResult" class="validation-result">
          <el-alert
            v-if="validationResult.valid"
            title="运行校验通过，可以创建"
            type="success"
            show-icon
            :closable="false"
          />
          <template v-else>
            <el-alert title="运行校验未通过" type="warning" show-icon :closable="false" />
            <ul class="issue-list">
              <li v-for="issue in validationResult.issues" :key="`${issue.code}-${issue.field ?? 'general'}`">
                <strong v-if="issue.field">{{ issue.field }}</strong>
                {{ issue.message }}
              </li>
            </ul>
          </template>
        </div>

        <div class="create-actions">
          <el-button type="primary" :icon="VideoPlay" :loading="validationLoading || createLoading" :disabled="!canValidateAndCreate" @click="validateAndCreate">
            {{ runType === 'API_CASE' && selectedApiCaseIds.length > 1 ? `批量校验并创建（${selectedApiCaseIds.length}）` : '校验并创建' }}
          </el-button>
           <span
             v-if="selectedRunner && ((runType === 'API_CASE' && selectedApiCases.length > 0) || (runType === 'SCENARIO' && selectedScenario) || (runType === 'WEB_CASE' && selectedWebCase))"
             class="action-hint"
           >
             已选择 {{ runType === 'API_CASE' ? `${selectedApiCases.length} 个 API 用例` : selectedScenario ? scenarioLabel(selectedScenario) : selectedWebCase ? webCaseLabel(selectedWebCase) : '' }} · {{ runnerLabel(selectedRunner.id) }}
          </span>
        </div>
        <div v-if="batchCreateSummary?.failures.length" class="batch-create-result">
          <el-alert
            :title="`已创建 ${batchCreateSummary.created} 条，${batchCreateSummary.failures.length} 条未通过`"
            type="warning"
            show-icon
            :closable="false"
          />
          <ul class="issue-list">
            <li v-for="failure in batchCreateSummary.failures" :key="failure.caseId">
              <strong>{{ failure.caseLabel }}</strong>{{ failure.message }}
            </li>
          </ul>
        </div>
      </el-card>

      <el-card class="run-list-card" shadow="never">
        <div class="card-heading-row">
          <div>
            <span class="eyebrow">运行记录</span>
            <h2>运行列表</h2>
          </div>
          <div class="list-heading-actions">
            <span class="polling-note">实时推送 · 8 秒轮询降级</span>
            <el-button
              type="primary"
              plain
              :icon="VideoPlay"
              :loading="batchDispatching"
              :disabled="runListLoading || dispatchingRunId !== null || cancellingRunId !== null || forceStoppingRunId !== null"
              @click="dispatchAllCreatedRuns"
            >
              一键运行待执行项
            </el-button>
            <el-button text :icon="Refresh" :loading="runListLoading" @click="() => loadRuns()">刷新</el-button>
          </div>
        </div>
        <el-alert v-if="runListError" :title="runListError" type="error" show-icon :closable="false" class="list-alert" />
        <el-table v-loading="runListLoading" :data="runs" row-key="id" class="run-table" table-layout="fixed">
          <el-table-column prop="run_code" label="运行" min-width="155" />
          <el-table-column label="目标" min-width="220">
            <template #default="{ row }">
              <div class="primary-cell">{{ runTargetLabel(row) }}</div>
               <span class="secondary-cell">{{ runTypeLabel(row.run_type) }} · 执行版本{{ runTargetVersionLabel(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="Runner" min-width="165">
            <template #default="{ row }">{{ runnerLabel(row.runner_id) }}</template>
          </el-table-column>
          <el-table-column label="状态" width="115">
            <template #default="{ row }"><el-tag :type="runStatusType(row.status)" size="small">{{ runDisplayLabel(row) }}</el-tag></template>
          </el-table-column>
          <el-table-column label="结果" min-width="220">
            <template #default="{ row }"><span class="result-summary">{{ resultCountSummary(row) }}</span></template>
          </el-table-column>
          <el-table-column label="创建时间" width="165">
            <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="耗时" width="110">
            <template #default="{ row }">{{ formatRunDuration(row) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="250" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" :icon="View" @click="openRunDetail(row)">详情</el-button>
              <el-button link type="primary" @click="openRunReport(row)">报告</el-button>
              <el-button v-if="row.status === 'CREATED'" link type="primary" :icon="VideoPlay" :loading="dispatchingRunId === row.id" :disabled="batchDispatching" @click="dispatchSelectedRun(row)">运行</el-button>
              <el-button v-if="isCancellableRun(row.status)" link type="warning" :icon="Close" :loading="cancellingRunId === row.id" :disabled="isRunOperationInFlight(row.id)" @click="cancelSelectedRun(row)">取消</el-button>
              <el-button v-else-if="isCancelInProgressRun(row.status)" link type="info" :icon="Close" disabled>取消中</el-button>
              <el-button v-if="isForceStoppableRun(row.status)" link type="danger" :icon="CircleClose" :loading="forceStoppingRunId === row.id" :disabled="isRunOperationInFlight(row.id)" @click="forceStopSelectedRun(row)">强制停止</el-button>
              <span v-else-if="!isCancellableRun(row.status) && !isCancelInProgressRun(row.status) && !isForceStoppableRun(row.status)" class="muted-action">—</span>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!runListLoading && runs.length === 0" description="当前项目暂无运行记录" :image-size="84" />
        <div v-if="runTotal > 0" class="pagination-row">
          <el-pagination
            :current-page="runPage"
            :page-size="runPageSize"
            :page-sizes="[10, 20, 50, 100]"
            :total="runTotal"
            layout="total, sizes, prev, pager, next"
            @current-change="handlePageChange"
            @size-change="(value: number) => { runPageSize = value; runPage = 1; loadRuns() }"
          />
        </div>
      </el-card>
    </template>

    <el-empty v-else-if="!projectsLoading && !projectError" description="请选择 ACTIVE 项目后开始运行" />

    <el-drawer v-model="detailVisible" title="运行详情" size="760px" destroy-on-close>
      <div v-loading="detailLoading" class="detail-panel">
        <el-alert v-if="detailError" :title="detailError" type="error" show-icon :closable="false" />
        <template v-if="detailRun">
          <div class="detail-heading">
            <div>
              <span class="eyebrow">{{ detailRun.run_code }}</span>
              <h2>{{ runTargetLabel(detailRun) }}</h2>
              <span class="secondary-cell">{{ runTypeLabel(detailRun.run_type) }} · 执行版本{{ runTargetVersionLabel(detailRun) }}</span>
            </div>
            <div class="detail-heading-actions">
              <el-tag :type="runStatusType(detailRun.status)">{{ runDisplayLabel(detailRun) }}</el-tag>
              <el-button type="primary" plain size="small" @click="openRunReport(detailRun)">查看报告</el-button>
              <el-button v-if="detailRun.status === 'CREATED'" type="primary" size="small" :icon="VideoPlay" :loading="dispatchingRunId === detailRun.id" @click="dispatchSelectedRun(detailRun)">投递</el-button>
              <el-button v-if="isCancellableRun(detailRun.status)" type="warning" size="small" :icon="Close" :loading="cancellingRunId === detailRun.id" :disabled="isRunOperationInFlight(detailRun.id)" @click="cancelSelectedRun(detailRun)">取消</el-button>
              <el-button v-else-if="isCancelInProgressRun(detailRun.status)" type="info" size="small" :icon="Close" disabled>取消中</el-button>
              <el-button v-if="isForceStoppableRun(detailRun.status)" type="danger" size="small" :icon="CircleClose" :loading="forceStoppingRunId === detailRun.id" :disabled="isRunOperationInFlight(detailRun.id)" @click="forceStopSelectedRun(detailRun)">强制停止</el-button>
            </div>
          </div>
          <el-alert
            v-if="detailRun.force_stopped"
            title="该运行已被强制停止：执行子进程已终止，清理可能未完成，请人工核对测试数据。"
            type="warning"
            show-icon
            :closable="false"
            class="force-stop-alert"
          />
          <el-alert
            v-else-if="detailRun.force_stop_requested_at"
            title="强制停止请求已发送，Runner 将在下一次检查时立即终止执行子进程。"
            type="warning"
            show-icon
            :closable="false"
            class="force-stop-alert"
          />
          <div class="stream-status-panel">
            <span class="stream-status-dot" :class="`stream-status-${streamState.toLowerCase()}`"></span>
            <strong>{{ streamStatusLabel }}</strong>
            <span v-if="streamLastEventAt">最近事件：{{ formatDate(streamLastEventAt) }}</span>
          </div>
          <el-alert v-if="streamErrorMessage" :title="streamErrorMessage" type="warning" :closable="false" class="stream-error" />
          <div v-if="dispatchMessage" class="dispatch-message">{{ dispatchMessage }}</div>

          <el-descriptions :column="2" border class="detail-descriptions">
            <el-descriptions-item label="运行类型">{{ runTypeLabel(detailRun.run_type) }}</el-descriptions-item>
            <el-descriptions-item label="触发方式">{{ detailRun.trigger_type }}</el-descriptions-item>
            <el-descriptions-item label="项目">{{ currentProject?.name ?? `项目 #${detailRun.project_id}` }}</el-descriptions-item>
            <el-descriptions-item label="环境">{{ environmentLabel(detailRun.environment_id) }}</el-descriptions-item>
            <el-descriptions-item label="Runner">{{ runnerLabel(detailRun.runner_id) }}</el-descriptions-item>
            <el-descriptions-item label="目标">{{ runTargetLabel(detailRun) }}</el-descriptions-item>
            <el-descriptions-item label="锁定版本">{{ runTargetVersionLabel(detailRun) }}</el-descriptions-item>
            <el-descriptions-item label="能力要求">{{ detailRun.required_capabilities.join('、') || '无' }}</el-descriptions-item>
            <el-descriptions-item label="槽位要求">{{ detailRun.required_slot_type }} / {{ detailRun.required_slot_count }}</el-descriptions-item>
            <el-descriptions-item label="运行总超时">
              <template v-if="formatTotalTimeout(detailRun.effective_total_timeout_ms) === '系统默认'">系统默认</template>
              <template v-else>
                {{ formatTotalTimeout(detailRun.effective_total_timeout_ms) }}
                （{{ detailRun.total_timeout_ms ? '自定义' : '系统默认' }}）
              </template>
            </el-descriptions-item>
            <el-descriptions-item label="强制停止请求时间">{{ detailRun.force_stop_requested_at ? formatDate(detailRun.force_stop_requested_at) : '—' }}</el-descriptions-item>
            <el-descriptions-item label="结果计数" :span="2">{{ resultCountSummary(detailRun) }}</el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ formatDate(detailRun.created_at) }}</el-descriptions-item>
            <el-descriptions-item label="更新时间">{{ formatDate(detailRun.updated_at) }}</el-descriptions-item>
            <el-descriptions-item label="开始时间">{{ formatDate(detailRun.started_at) }}</el-descriptions-item>
            <el-descriptions-item label="结束时间">{{ formatDate(detailRun.ended_at) }}</el-descriptions-item>
            <el-descriptions-item label="耗时">{{ formatRunDuration(detailRun) }}</el-descriptions-item>
            <el-descriptions-item label="创建人">{{ detailRun.created_by }}</el-descriptions-item>
          </el-descriptions>

          <section v-if="detailRun.web_session_recoveries?.length" class="evidence-section">
            <div class="section-title-row"><div><h3>登录态恢复审计</h3><span>仅展示固定登录版本、触发原因和状态；Cookie、会话状态与密钥不会展示。</span></div></div>
            <el-table :data="detailRun.web_session_recoveries" size="small" table-layout="fixed">
              <el-table-column label="状态" width="110"><template #default="{ row }"><el-tag :type="row.status === 'SUCCESS' ? 'success' : row.status === 'FAILED' ? 'danger' : 'info'">{{ row.status }}</el-tag></template></el-table-column>
              <el-table-column label="触发原因" min-width="150" prop="reason" />
              <el-table-column label="固定登录版本" min-width="140"><template #default="{ row }">#{{ row.login_web_case_version_id }}</template></el-table-column>
              <el-table-column label="状态写回" min-width="130" prop="persistence_status" />
              <el-table-column label="耗时" min-width="110"><template #default="{ row }">{{ row.duration_ms }} ms</template></el-table-column>
              <el-table-column label="安全错误" min-width="170"><template #default="{ row }">{{ row.error_type ?? '—' }}</template></el-table-column>
            </el-table>
          </section>

          <section v-if="detailRun.api_action_traces?.length" class="evidence-section">
            <div class="section-title-row"><div><h3>API 操作追踪</h3><span>按执行阶段记录操作结果与耗时，仅包含安全元数据，不包含变量值、响应正文或密钥。</span></div></div>
            <el-table :data="detailRun.api_action_traces" size="small" table-layout="fixed">
              <el-table-column label="用例运行" width="100"><template #default="{ row }">#{{ row.case_run_id }}</template></el-table-column>
              <el-table-column label="阶段" width="125" prop="phase" />
              <el-table-column label="序号" width="70" prop="sequence" />
              <el-table-column label="动作" min-width="140"><template #default="{ row }">{{ row.name || row.type }}</template></el-table-column>
              <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag :type="row.status === 'SUCCESS' ? 'success' : row.status === 'FAILED' ? 'danger' : 'info'">{{ row.status }}</el-tag></template></el-table-column>
              <el-table-column label="耗时" width="100"><template #default="{ row }">{{ row.duration_ms }} ms</template></el-table-column>
              <el-table-column label="错误原因" min-width="190"><template #default="{ row }">{{ apiActionErrorLabel(row.error_type) }}</template></el-table-column>
            </el-table>
          </section>

          <el-alert v-if="safeRuntimeError(detailRun.error_message, '运行执行失败')" :title="timeoutErrorLabel(detailRun)" type="error" show-icon :closable="false" class="detail-error">
            {{ safeRuntimeError(detailRun.error_message, '运行执行失败') }}
          </el-alert>

          <section class="evidence-section">
            <div class="section-title-row evidence-heading">
              <div>
                <h3>运行证据</h3>
                <span>仅展示可下载的证据元数据，不显示原始响应正文。</span>
              </div>
              <el-button text :icon="Refresh" :loading="evidenceLoading" @click="refreshEvidence">刷新</el-button>
            </div>
            <el-alert v-if="evidenceError" :title="evidenceError" type="error" show-icon :closable="false" class="evidence-error" />
            <el-table v-loading="evidenceLoading" :data="evidenceItems" size="small" table-layout="fixed">
              <el-table-column label="类型" width="110">
                <template #default="{ row }"><el-tag type="info" size="small">{{ evidenceTypeLabel(row.artifact_type) }}</el-tag></template>
              </el-table-column>
              <el-table-column label="文件名" min-width="190">
                <template #default="{ row }"><span class="file-name-cell" :title="row.file_name">{{ row.file_name }}</span></template>
              </el-table-column>
              <el-table-column label="大小" width="95">
                <template #default="{ row }">{{ formatFileSize(row.size) }}</template>
              </el-table-column>
              <el-table-column label="生成时间" min-width="155">
                <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
              </el-table-column>
              <el-table-column label="安全元数据" min-width="125">
                <template #default="{ row }">{{ evidenceMetadataLabel(row) }}</template>
              </el-table-column>
              <el-table-column label="操作" width="90" fixed="right">
                <template #default="{ row }">
                  <el-button link type="primary" :loading="evidenceDownloadId === row.id" @click="downloadEvidenceFile(row)">下载</el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-if="!evidenceLoading && !evidenceError && evidenceItems.length === 0" description="暂无运行证据" :image-size="64" />
          </section>

          <section v-if="failureAnalysisVisible" class="failure-analysis-section">
            <div class="section-title-row">
              <div>
                <h3>AI Web 失败分析</h3>
                <span>只读分析，不创建缺陷、不修改资产、不自动重跑。</span>
              </div>
              <div class="section-heading-actions">
                <el-tag type="info" size="small">只读</el-tag>
                <el-button
                  link
                  type="primary"
                  :loading="failureAnalysisHistoryLoading"
                  :disabled="!failureAnalysisTargetCaseRun"
                  @click="reloadFailureAnalysisHistory"
                >刷新分析历史</el-button>
              </div>
            </div>
            <el-alert
              title="安全边界"
              description="分析只读取后端生成的安全结果；页面不展示来源快照、DOM、Locator 值、证据正文或文件路径。"
              type="info"
              show-icon
              :closable="false"
              class="failure-analysis-alert"
            />
            <el-alert
              v-if="failureAnalysisGenerationTimeoutNotice"
              :title="failureAnalysisGenerationTimeoutNotice"
              type="warning"
              show-icon
              :closable="false"
              class="failure-analysis-alert"
            />
            <template v-if="failureAnalysisCaseRuns.length">
              <div class="failure-analysis-target-row">
                <span class="field-label">分析目标用例运行</span>
                <el-select
                  v-if="failureAnalysisCaseRuns.length > 1"
                  v-model="failureAnalysisCaseRunId"
                  placeholder="选择失败或超时的用例运行"
                  class="failure-analysis-case-select"
                  @change="selectFailureAnalysisCaseRun"
                >
                  <el-option
                    v-for="caseRun in failureAnalysisCaseRuns"
                    :key="caseRun.id"
                    :label="failureAnalysisRunSelectionLabel(caseRun)"
                    :value="caseRun.id"
                  />
                </el-select>
                <span v-else class="failure-analysis-target-label">{{ failureAnalysisRunSelectionLabel(failureAnalysisCaseRuns[0]) }}</span>
              </div>

              <div v-loading="failureAnalysisConfigLoading" class="failure-analysis-config-panel">
                <el-alert v-if="failureAnalysisPromptError" :title="failureAnalysisPromptError" type="warning" show-icon :closable="false" />
                <el-alert v-if="failureAnalysisSchemaError" :title="failureAnalysisSchemaError" type="warning" show-icon :closable="false" />
                <el-alert v-if="failureAnalysisBindingError" :title="failureAnalysisBindingError" type="warning" show-icon :closable="false" />
                <div class="failure-analysis-generate-row">
                  <el-select
                    v-model="selectedFailureAnalysisPromptId"
                    filterable
                    class="failure-analysis-prompt-select"
                    :disabled="!availableFailureAnalysisPrompts.length || failureAnalysisGenerating"
                    placeholder="选择启用的 WEB_FAILURE_ANALYSIS Prompt"
                  >
                    <el-option
                      v-for="prompt in availableFailureAnalysisPrompts"
                      :key="prompt.id"
                      :label="promptOptionLabel(prompt)"
                      :value="prompt.id"
                    />
                  </el-select>
                  <el-input
                    v-model="failureAnalysisAdditionalInstructions"
                    type="textarea"
                    :rows="2"
                    maxlength="5000"
                    show-word-limit
                    :disabled="failureAnalysisGenerating"
                    placeholder="补充说明（可选，不能换行、不能包含敏感信息或 URL）"
                  />
                  <el-button
                    type="primary"
                    :loading="failureAnalysisGenerating"
                    :disabled="!failureAnalysisConfigReady || !selectedFailureAnalysisPromptId || !failureAnalysisTargetCaseRun || Boolean(failureAnalysisGenerationTimeoutNotice)"
                    @click="generateFailureAnalysis"
                  >生成分析</el-button>
                </div>
                <div class="failure-analysis-config-notes">
                  <span v-if="!failureAnalysisConfigLoading && !failureAnalysisPromptError && !failureAnalysisSchemaError && !availableFailureAnalysisPrompts.length" class="safe-note">没有可用 Prompt：请在 Prompt 中心启用 WEB_FAILURE_ANALYSIS，并为当前版本绑定启用的 Output Schema。</span>
                  <span v-if="!failureAnalysisConfigLoading && !failureAnalysisBindingError && !failureAnalysisBinding" class="safe-note">没有项目模型绑定：请在项目设置中配置 WEB_FAILURE_ANALYSIS；页面不会自动创建模型配置。</span>
                  <el-button v-if="failureAnalysisPromptError || failureAnalysisSchemaError || failureAnalysisBindingError" link type="primary" @click="reloadFailureAnalysisConfiguration">重新加载分析配置</el-button>
                </div>
              </div>

              <div v-if="failureAnalysisHistoryError" class="failure-analysis-history-error">
                <el-alert :title="failureAnalysisHistoryError" type="error" show-icon :closable="false" class="failure-analysis-alert" />
                <el-button link type="primary" @click="reloadFailureAnalysisHistory">重新加载历史</el-button>
              </div>
              <div v-loading="failureAnalysisHistoryLoading" class="failure-analysis-history-panel">
                <div v-if="failureAnalysisItems.length" class="failure-analysis-history">
                  <div class="section-title-row"><h4>分析历史</h4><span>{{ failureAnalysisItems.length }} 条，最新优先</span></div>
                  <div class="failure-analysis-history-list">
                    <button
                      v-for="analysis in failureAnalysisItems"
                      :key="analysis.id"
                      class="failure-analysis-history-item"
                      :class="{ active: analysis.id === selectedFailureAnalysis?.id }"
                      @click="failureAnalysisSelectedId = analysis.id"
                    >
                      <span><strong>#{{ analysis.id }}</strong><small>{{ formatDate(analysis.created_at) }}</small></span>
                      <el-tag size="small" :type="failureAnalysisStatusType(analysis.status)">{{ failureAnalysisStatusLabel(analysis.status) }}</el-tag>
                    </button>
                  </div>
                </div>
                <el-empty v-if="!failureAnalysisHistoryLoading && !failureAnalysisHistoryError && !failureAnalysisItems.length" description="该用例运行暂无失败分析" :image-size="52" />
                <template v-if="selectedFailureAnalysis">
                  <div class="failure-analysis-meta-grid">
                    <div><span>状态</span><strong><el-tag size="small" :type="failureAnalysisStatusType(selectedFailureAnalysis.status)">{{ failureAnalysisStatusLabel(selectedFailureAnalysis.status) }}</el-tag></strong></div>
                    <div><span>AI Call ID</span><strong>{{ selectedFailureAnalysis.ai_call_id ?? '—' }}</strong></div>
                    <div><span>实际模型</span><strong>{{ selectedFailureAnalysis.actual_model || '—' }}</strong></div>
                    <div><span>Prompt 版本 ID</span><strong>{{ selectedFailureAnalysis.prompt_version_id }}</strong></div>
                    <div><span>输出 Schema ID</span><strong>{{ selectedFailureAnalysis.output_schema_id }}</strong></div>
                    <div><span>备用模型 / 输出修复</span><strong>{{ failureAnalysisBooleanLabel(selectedFailureAnalysis.fallback_used) }} / {{ failureAnalysisBooleanLabel(selectedFailureAnalysis.repair_used) }}</strong></div>
                    <div><span>来源摘要</span><strong>{{ healingShortHash(selectedFailureAnalysis.source_snapshot_sha256) }} · {{ formatFileSize(selectedFailureAnalysis.source_snapshot_size) }}</strong></div>
                    <div><span>创建人</span><strong>{{ selectedFailureAnalysis.created_by }}</strong></div>
                    <div><span>创建时间</span><strong>{{ formatDate(selectedFailureAnalysis.created_at) }}</strong></div>
                  </div>
                  <el-alert
                    v-if="selectedFailureAnalysis.status !== 'COMPLETED'"
                    title="分析正在生成"
                    description="后端尚未返回结构化分析结果，当前只展示生成记录。"
                    type="info"
                    show-icon
                    :closable="false"
                    class="failure-analysis-alert"
                  />
                  <el-alert
                    v-else-if="!selectedFailureAnalysisResult"
                    title="暂无结构化分析结果"
                    description="后端未返回可展示的结构化结果。"
                    type="warning"
                    show-icon
                    :closable="false"
                    class="failure-analysis-alert"
                  />
                  <template v-else>
                    <div class="failure-analysis-result-grid">
                      <div><span>失败类别</span><strong>{{ failureAnalysisCategoryLabel(selectedFailureAnalysisResult.failure_category) }}</strong></div>
                      <div><span>严重程度</span><strong><el-tag size="small" :type="failureAnalysisSeverityType(selectedFailureAnalysisResult.severity)">{{ failureAnalysisSeverityLabel(selectedFailureAnalysisResult.severity) }}</el-tag></strong></div>
                      <div><span>置信度</span><strong>{{ failureAnalysisConfidenceLabel(selectedFailureAnalysisResult.confidence) }}</strong></div>
                      <div><span>需要人工复核</span><strong>{{ selectedFailureAnalysisResult.needs_human_review ? '是' : '否' }}</strong></div>
                      <div class="failure-analysis-wide"><span>摘要</span><strong>{{ failureAnalysisSafeText(selectedFailureAnalysisResult.summary, 1000) }}</strong></div>
                      <div class="failure-analysis-wide"><span>根因</span><strong>{{ failureAnalysisSafeText(selectedFailureAnalysisResult.root_cause, 2000) }}</strong></div>
                      <div class="failure-analysis-wide"><span>建议</span><ul><li v-for="(recommendation, index) in selectedFailureAnalysisResult.recommendations" :key="index">{{ failureAnalysisSafeText(recommendation, 500) }}</li></ul></div>
                      <div class="failure-analysis-wide"><span>关联节点</span><strong>{{ selectedFailureAnalysisResult.evidence_node_ids.length ? selectedFailureAnalysisResult.evidence_node_ids.join('、') : '—' }}</strong></div>
                    </div>
                  </template>
                </template>
              </div>
            </template>
            <el-alert
              v-else
              title="没有可安全选择的失败用例运行"
              description="当前运行未返回失败或超时的用例运行，暂不生成分析。"
              type="warning"
              show-icon
              :closable="false"
            />
          </section>

          <div class="case-runs-section">
            <div class="section-title-row">
              <h3>执行明细</h3>
              <span>{{ detailRun.case_runs.length }} 个{{ detailRun.run_type === 'SCENARIO' ? 'Scenario' : detailRun.run_type === 'WEB_CASE' ? 'Web Case' : '用例' }}运行</span>
            </div>
            <el-empty v-if="detailRun.case_runs.length === 0" description="暂无执行明细" :image-size="64" />
            <div v-for="caseRun in detailRun.case_runs" :key="caseRun.id" class="case-run-block">
              <div class="case-run-header">
                <div>
                  <strong>#{{ caseRun.sequence_no }} · {{ caseRunLabel(caseRun) }}</strong>
                  <span class="secondary-cell">{{ caseRun.step_runs.length }} 个步骤 · {{ formatDurationMilliseconds(caseRun.duration) }} · 重试 {{ retryCountLabel(caseRun.retry_count) }}</span>
                </div>
                <el-tag :type="nodeStatusType(caseRun.status)" size="small">{{ nodeStatusLabel(caseRun.status) }}</el-tag>
              </div>
              <el-alert v-if="safeRuntimeError(caseRun.error_message, '用例执行失败')" :title="caseRun.error_type || '用例错误'" type="error" :closable="false" class="case-error">
                {{ safeRuntimeError(caseRun.error_message, '用例执行失败') }}
              </el-alert>
              <el-table :data="caseRun.step_runs" size="small" table-layout="fixed">
                <el-table-column prop="sequence_no" label="#" width="52" />
                <el-table-column label="步骤" min-width="170">
                  <template #default="{ row }">
                    <div class="primary-cell">{{ stepRunLabel(row) }}</div>
                    <span class="secondary-cell">节点 {{ row.node_id }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="状态" width="95">
                  <template #default="{ row }"><el-tag :type="nodeStatusType(row.status)" size="small">{{ nodeStatusLabel(row.status) }}</el-tag></template>
                </el-table-column>
                <el-table-column label="重试" width="85">
                  <template #default="{ row }">{{ retryCountLabel(row.retry_count) }}</template>
                </el-table-column>
                <el-table-column label="耗时" width="100">
                  <template #default="{ row }">{{ formatDurationMilliseconds(row.duration) }}</template>
                </el-table-column>
                <el-table-column label="错误" min-width="180">
                  <template #default="{ row }">{{ safeRuntimeError(row.error_message, '步骤执行失败') || '—' }}</template>
                </el-table-column>
              </el-table>
            </div>
            <section v-if="detailRun.run_type === 'WEB_CASE'" class="web-traces-section">
              <div class="section-title-row">
                <h3>Web 执行追踪</h3>
                <span>仅展示节点状态与 Locator 尝试结果，不展示 Locator 值。</span>
              </div>
              <el-table v-if="webTracesOf(detailRun).length" :data="webTracesOf(detailRun)" size="small" table-layout="fixed">
                <el-table-column prop="node_id" label="节点" min-width="130" />
                <el-table-column label="状态" width="95"><template #default="{ row }"><el-tag :type="row.status === 'SUCCESS' ? 'success' : row.status === 'FAILED' || row.status === 'TIMEOUT' ? 'danger' : 'info'" size="small">{{ webTraceStatusLabel(row.status) }}</el-tag></template></el-table-column>
                <el-table-column label="耗时" width="100"><template #default="{ row }">{{ formatDurationMilliseconds(row.duration_ms) }}</template></el-table-column>
                <el-table-column label="Locator 尝试" min-width="260">
                  <template #default="{ row }">
                    <span v-if="!row.locator_attempts.length" class="secondary-cell">无 Locator 尝试记录</span>
                    <span v-else class="trace-attempts"><el-tag v-for="(attempt, index) in row.locator_attempts" :key="`${row.node_id}-${index}`" size="small" :type="locatorAttemptStatusType(attempt.status)">{{ attempt.strategy }} · P{{ attempt.priority }} · {{ locatorAttemptStatusLabel(attempt.status) }}</el-tag></span>
                 </template>
                 </el-table-column>
                 <el-table-column label="错误" min-width="200"><template #default="{ row }">{{ safeRuntimeError(row.error_message, 'Web 节点执行失败') || '—' }}</template></el-table-column>
                 <el-table-column label="Healing" width="150" fixed="right">
                   <template #default="{ row }">
                     <el-button v-if="canOfferHealing(row)" link type="warning" @click="selectHealingTrace(row)">{{ healingSelection?.trace.node_id === row.node_id ? '已选择' : '生成自愈提案' }}</el-button>
                     <span v-else class="muted-action">—</span>
                   </template>
                 </el-table-column>
               </el-table>
               <el-empty v-else description="暂无 Web 执行追踪" :image-size="60" />
             </section>
             <section v-if="healingSelection" class="healing-section">
               <div class="section-title-row">
                 <div>
                   <h3>定位器自愈审核</h3>
                    <span>节点 {{ healingSelection.trace.node_id }} · 候选须先通过临时验证运行；接受只创建草稿，不自动批准。</span>
                 </div>
                 <div class="section-heading-actions">
                   <el-tag type="warning" size="small">人工审核</el-tag>
                   <el-button link type="primary" :loading="healingProposalsLoading" @click="reloadHealingProposalHistory">刷新提案历史</el-button>
                 </div>
               </div>
                <el-alert title="安全边界" description="自愈仅基于后端脱敏候选；验证运行只临时覆盖失败节点，不修改资产。接受操作不覆盖旧版本，只创建待审批草稿。" type="info" show-icon :closable="false" class="healing-alert" />
               <el-alert v-if="healingGenerationTimeoutNotice" :title="healingGenerationTimeoutNotice" type="warning" show-icon :closable="false" class="healing-alert" />
               <div v-loading="healingConfigLoading || healingProposalsLoading" class="healing-config-panel">
                 <el-alert v-if="healingPromptError" :title="healingPromptError" type="warning" show-icon :closable="false" />
                 <el-alert v-if="healingSchemaError" :title="healingSchemaError" type="warning" show-icon :closable="false" />
                 <el-alert v-if="healingBindingError" :title="healingBindingError" type="warning" show-icon :closable="false" />
                  <div class="healing-generate-row">
                    <el-select v-model="selectedHealingPromptId" filterable :disabled="!availableHealingPrompts.length || healingGenerating" placeholder="选择启用的定位器自愈 Prompt" class="healing-prompt-select">
                      <el-option v-for="prompt in availableHealingPrompts" :key="prompt.id" :label="promptOptionLabel(prompt)" :value="prompt.id" />
                    </el-select>
                    <el-select v-model="selectedHealingAnalysisStage" :disabled="healingGenerating" class="healing-prompt-select">
                      <el-option label="LLM DOM Analysis" value="LLM_DOM" />
                      <el-option label="Vision Screenshot Analysis" value="VISION_SCREENSHOT" />
                      <el-option label="Agent Locate（最后辅助）" value="AGENT_LOCATE" />
                    </el-select>
                    <el-select v-if="healingUsesScreenshot" v-model="selectedHealingScreenshotId" filterable :disabled="healingGenerating" placeholder="选择失败截图证据" class="healing-prompt-select">
                      <el-option v-for="item in healingScreenshotEvidence" :key="item.id" :label="`${item.file_name} · ${formatFileSize(item.size)}`" :value="item.id" />
                    </el-select>
                    <el-alert v-if="healingUsesScreenshot" title="所选遮罩截图将发送给当前项目绑定的外部模型" description="仅发送同一失败用例运行的已校验 PNG 证据；不读取执行追踪、控制台、网络或其他文件正文。" type="warning" show-icon :closable="false" />
                    <el-input v-model="healingAdditionalInstructions" type="textarea" :rows="2" maxlength="5000" show-word-limit :disabled="healingGenerating" placeholder="补充说明（可选，不要填写凭据、Cookie、Token 或原始 DOM）" />
                    <el-button type="primary" :loading="healingGenerating" :disabled="!healingConfigReady || !selectedHealingPromptId || Boolean(healingGenerationTimeoutNotice) || (healingUsesScreenshot && !selectedHealingScreenshotId)" @click="generateHealingProposalForSelection">生成自愈提案</el-button>
                 </div>
                 <span v-if="!healingConfigLoading && !healingPromptError && !healingSchemaError && !availableHealingPrompts.length" class="safe-note">没有可用 Prompt：请在 Prompt 中心启用定位器自愈任务，并为当前版本绑定启用的输出 Schema。</span>
                 <span v-if="!healingConfigLoading && !healingBindingError && !healingBinding" class="safe-note">没有项目模型绑定：请在项目设置中配置 LOCATOR_HEALING，页面不会自动创建模型配置。</span>
               </div>
               <el-alert v-if="healingProposalsError" :title="healingProposalsError" type="error" show-icon :closable="false" class="healing-alert" />
               <div v-if="!healingProposalsLoading && !healingProposals.length && !healingProposalsError" class="healing-empty"><el-empty description="该失败节点暂无自愈提案" :image-size="52" /></div>
               <div v-if="healingProposals.length" class="healing-history">
                 <div class="section-title-row"><h4>提案历史</h4><span>{{ healingProposals.length }} 条</span></div>
                 <div class="healing-history-list">
                   <button v-for="proposal in healingProposals" :key="proposal.id" class="healing-history-item" :class="{ active: proposal.id === selectedHealingProposal?.id }" @click="selectHealingProposal(proposal)">
                     <span><strong>#{{ proposal.id }}</strong><small>{{ formatDate(proposal.created_at) }}</small></span>
                     <el-tag size="small" :type="healingStatusType(proposal.status)">{{ healingStatusLabel(proposal.status) }}</el-tag>
                   </button>
                 </div>
               </div>
               <template v-if="selectedHealingProposal">
                 <div class="healing-audit-grid">
                   <div><span>状态</span><strong><el-tag size="small" :type="healingStatusType(selectedHealingProposal.status)">{{ healingStatusLabel(selectedHealingProposal.status) }}</el-tag></strong></div>
                   <div><span>置信度</span><strong>{{ healingConfidenceLabel(selectedHealingProposal.confidence) }}</strong></div>
                    <div><span>AI Call ID</span><strong>{{ selectedHealingProposal.ai_call_id ?? '—' }}</strong></div>
                    <div><span>分析阶段</span><strong>{{ selectedHealingProposal.analysis_stage }}</strong></div>
                    <div><span>截图 Evidence</span><strong>{{ selectedHealingProposal.screenshot_artifact_id || '—' }}</strong></div>
                   <div><span>Prompt 版本 ID</span><strong>{{ selectedHealingProposal.prompt_version_id ?? '—' }}</strong></div>
                   <div><span>输出 Schema</span><strong>{{ healingSchemaLabel(selectedHealingProposal.output_schema_id) }}</strong></div>
                   <div><span>实际模型</span><strong>{{ selectedHealingProposal.actual_model || '—' }}</strong></div>
                   <div><span>备用模型 / 输出修复</span><strong>{{ selectedHealingProposal.fallback_used ? '已使用' : '无' }} / {{ selectedHealingProposal.repair_used ? '已使用' : '无' }}</strong></div>
                   <div><span>来源快照</span><strong>{{ healingShortHash(selectedHealingProposal.source_snapshot_sha256) }} · {{ formatFileSize(selectedHealingProposal.source_snapshot_size) }}</strong></div>
                   <div><span>创建人 / 时间</span><strong>{{ selectedHealingProposal.created_by }} · {{ formatDate(selectedHealingProposal.created_at) }}</strong></div>
                   <div><span>审核人 / 时间</span><strong>{{ selectedHealingProposal.reviewed_by || '—' }} · {{ formatDate(selectedHealingProposal.reviewed_at) }}</strong></div>
                 </div>
                 <div class="healing-locator-grid">
                   <div><span>旧定位器</span><strong>{{ healingOldLocatorLabel(selectedHealingProposal.old_locator) }}</strong></div>
                   <div><span>AI 建议定位器</span><strong>{{ healingLocatorLabel(selectedHealingProposal.proposed_locator) }}</strong></div>
                   <div><span>用户最终定位器</span><strong>{{ healingLocatorLabel(selectedHealingProposal.human_locator) }}</strong></div>
                   <div><span>生成依据</span><strong>{{ healingValueLabel(selectedHealingProposal.reason) }}</strong></div>
                 </div>
                  <div class="healing-candidate-panel">
                    <div class="section-title-row"><div><h4>候选定位器</h4><span>只能接受已通过完整 Web 用例验证运行的后端候选。</span></div></div>
                    <el-radio-group v-model="selectedHealingCandidateKey" :disabled="selectedHealingProposal.status !== 'DRAFT' || healingDecisionSaving" class="healing-candidate-list">
                      <el-radio v-for="candidate in selectedHealingProposal.candidate_locators" :key="healingCandidateKey(candidate)" :value="healingCandidateKey(candidate)" class="healing-candidate-option">候选 #{{ candidate.candidate_index }} · 相似度 {{ Math.round(candidate.similarity_score * 100) }}% · {{ healingLocatorLabel(candidate.locator) }}</el-radio>
                    </el-radio-group>
                  </div>
                  <el-alert
                    v-if="selectedHealingProposal.latest_validation"
                    :title="`最近验证：${selectedHealingProposal.latest_validation.run_code} · ${selectedHealingProposal.latest_validation.run_status}`"
                    :description="`Locator：${healingLocatorLabel(selectedHealingProposal.latest_validation.locator)}`"
                    :type="selectedHealingProposal.latest_validation.run_status === 'SUCCESS' ? 'success' : selectedHealingProposal.latest_validation.run_status === 'FAILED' ? 'error' : 'warning'"
                    show-icon
                    :closable="false"
                    class="healing-alert"
                  >
                    <template #default><el-button link type="primary" @click="openLatestHealingValidationRun">查看验证 Run</el-button></template>
                  </el-alert>
                 <el-alert v-if="selectedHealingProposal.created_web_case_version_id || selectedHealingProposal.created_element_version_id" title="已创建新的草稿版本" description="请前往 Web 用例管理检查并批准；运行中心不会自动重跑该运行。" type="success" show-icon :closable="false" class="healing-alert">
                   <template #default>
                     <div class="healing-version-actions">
                       <span v-if="selectedHealingProposal.created_web_case_version_id">Web 用例版本 #{{ selectedHealingProposal.created_web_case_version_id }}</span><span v-if="selectedHealingProposal.created_element_version_id"> · 元素版本 #{{ selectedHealingProposal.created_element_version_id }}</span>
                       <el-button v-if="selectedHealingProposal.status === 'ACCEPTED' && selectedHealingProposal.created_web_case_version_id" link type="primary" @click="openHealingVersionReview(selectedHealingProposal)">前往审核新版本</el-button>
                       <el-button v-if="selectedHealingProposal.status === 'ACCEPTED' && selectedHealingProposal.created_web_case_version_id" type="primary" plain :loading="healingVersionLoading" @click="loadAcceptedHealingVersionIntoRunForm(selectedHealingProposal)">检查并载入新版本到运行表单</el-button>
                     </div>
                   </template>
                 </el-alert>
                  <div v-if="selectedHealingProposal.status === 'DRAFT'" class="healing-decision-actions"><el-button type="primary" plain :loading="healingValidating" :disabled="!selectedHealingCandidate || healingDecisionSaving || healingValidationActive || selectedHealingCandidateValidated" @click="validateSelectedHealingCandidate">{{ selectedHealingCandidateValidated ? '候选已验证' : healingValidationActive ? '验证运行执行中' : '验证所选候选' }}</el-button><el-button type="success" :disabled="!selectedHealingCandidate || !selectedHealingCandidateValidated || healingDecisionSaving" @click="openHealingDecision('accept')">接受并创建草稿</el-button><el-button type="danger" plain :loading="healingDecisionMode === 'reject' && healingDecisionSaving" :disabled="healingDecisionSaving" @click="openHealingDecision('reject')">拒绝提案</el-button></div>
               </template>
             </section>
           </div>
        </template>
      </div>
    </el-drawer>
    <el-dialog v-model="healingDecisionVisible" :title="healingDecisionMode === 'accept' ? '确认接受自愈提案' : '确认拒绝自愈提案'" width="560px">
      <el-alert
        v-if="healingDecisionMode === 'accept'"
        title="只创建草稿版本"
        :description="`将使用已选候选：${selectedHealingCandidate ? healingLocatorLabel(selectedHealingCandidate.locator) : '—'}。不会自动批准、自动重跑或覆盖旧版本。`"
        type="warning"
        show-icon
        :closable="false"
      />
      <el-alert
        v-else
        title="只记录人工拒绝"
        description="不会修改正式 Web 用例，也不会触发重新运行；之后可以重新生成新的自愈提案。"
        type="warning"
        show-icon
        :closable="false"
      />
      <el-form label-position="top" class="healing-decision-form">
        <el-form-item label="决策说明（可选）"><el-input v-model="healingDecisionNote" type="textarea" :rows="3" maxlength="500" show-word-limit placeholder="不要填写凭据、Cookie、Token 或原始 DOM" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="healingDecisionVisible = false">取消</el-button><el-button :type="healingDecisionMode === 'accept' ? 'success' : 'danger'" :loading="healingDecisionSaving" @click="submitHealingDecision">{{ healingDecisionMode === 'accept' ? '确认接受并创建草稿' : '确认拒绝' }}</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.runs-page {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.runs-heading {
  align-items: flex-start;
}

.runs-heading p,
.card-description {
  margin: 6px 0 0;
  color: var(--text-secondary);
  font-size: 13px;
}

.project-selector-card,
.run-target-switch-card,
.resource-card,
.create-card,
.run-list-card {
  border: 1px solid var(--border-color);
  border-radius: 14px;
}

.selector-row,
.card-heading-row,
.resource-card-header,
.list-heading-actions,
.detail-heading,
.detail-heading-actions,
.case-run-header,
.section-title-row,
.pagination-row,
.created-alert-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.selector-field {
  display: flex;
  align-items: center;
  gap: 12px;
}

.run-timeout-field {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 12px 0;
}

.run-type-field {
  display: flex;
  align-items: center;
  gap: 14px;
  margin: 0;
}

.target-switch-hint {
  margin-left: auto;
  color: var(--text-secondary);
  font-size: 12px;
}

.scenario-version-select {
  margin-top: 10px;
}

.force-stop-alert {
  margin: 12px 0;
}

.field-label,
.eyebrow {
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.project-select {
  width: min(420px, 60vw);
}

.selected-project-note {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 3px;
  color: var(--text-secondary);
  font-size: 12px;
}

.selected-project-note strong {
  color: var(--text-primary);
  font-size: 14px;
}

.section-alert,
.list-alert,
.created-alert,
.detail-error,
.case-error {
  margin: 0;
}

.resource-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}

.environment-resource-card {
  order: 1;
}

.runner-resource-card {
  order: 2;
}

.target-resource-card {
  order: 3;
  grid-column: 1 / -1;
}

.resource-card {
  min-height: 170px;
}

.resource-card h2,
.create-card h2,
.run-list-card h2,
.detail-panel h2 {
  margin: 4px 0 0;
  color: var(--text-primary);
  font-size: 18px;
}

.resource-card-header {
  align-items: flex-start;
  margin-bottom: 18px;
}

.full-width {
  width: 100%;
}

.empty-hint,
.resource-meta,
.action-hint,
.polling-note,
.muted-action,
.secondary-cell,
.section-title-row > span {
  color: var(--text-secondary);
  font-size: 12px;
}

.empty-hint {
  margin: 12px 0 0;
  line-height: 1.5;
}

.api-retry-gate-alert {
  margin-top: 12px;
}

.error-hint {
  color: var(--el-color-danger);
}

.resource-meta {
  margin: 10px 0 0;
}

.multi-select-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 8px;
  color: var(--text-secondary);
  font-size: 12px;
}

.multi-select-toolbar > div {
  display: flex;
  flex: 0 0 auto;
}

.fixed-run-fields {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 20px;
  margin: 18px 0;
  padding: 12px 14px;
  border-radius: 10px;
  background: var(--surface-subtle);
  color: var(--text-secondary);
  font-size: 13px;
}

.fixed-run-fields b {
  color: var(--text-primary);
  font-weight: 600;
}

.validation-result {
  margin: 16px 0;
}

.batch-create-result {
  margin-top: 16px;
}

.issue-list {
  margin: 10px 0 0;
  padding: 10px 12px 10px 30px;
  border: 1px solid var(--el-color-warning-light-5);
  border-radius: 8px;
  background: var(--el-color-warning-light-9);
  color: var(--text-primary);
  font-size: 13px;
  line-height: 1.6;
}

.issue-list strong {
  margin-right: 6px;
}

.create-actions {
  display: flex;
  align-items: center;
  gap: 16px;
}

.created-alert-actions {
  justify-content: flex-start;
  margin-top: 6px;
}

.run-list-card {
  overflow: hidden;
}

.run-table {
  margin-top: 16px;
}

.primary-cell {
  overflow: hidden;
  color: var(--text-primary);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.secondary-cell {
  display: block;
  margin-top: 3px;
}

.result-summary {
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.4;
}

.dispatch-message {
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--surface-subtle);
  color: var(--text-secondary);
  font-size: 13px;
}

.pagination-row {
  justify-content: flex-end;
  margin-top: 16px;
  color: var(--text-secondary);
  font-size: 12px;
}

.detail-panel {
  min-height: 260px;
}

.detail-heading {
  align-items: flex-start;
  margin-bottom: 20px;
}

.detail-heading-actions {
  justify-content: flex-end;
}

.stream-status-panel {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: -8px 0 12px;
  color: var(--text-secondary);
  font-size: 12px;
}

.stream-status-panel strong {
  color: var(--text-primary);
  font-weight: 600;
}

.stream-status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-secondary);
}

.stream-status-connected {
  background: var(--el-color-success);
}

.stream-status-connecting,
.stream-status-reconnecting {
  background: var(--el-color-warning);
}

.stream-status-polling_fallback {
  background: var(--el-color-danger);
}

.stream-error {
  margin-bottom: 12px;
}

.detail-descriptions {
  margin-bottom: 20px;
}

.case-runs-section {
  margin-top: 24px;
}

.evidence-section {
  margin-top: 24px;
}

.evidence-heading {
  align-items: flex-start;
  justify-content: space-between;
}

.evidence-heading > div {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.evidence-heading > div > span {
  color: var(--text-secondary);
  font-size: 12px;
}

.evidence-error {
  margin-bottom: 12px;
}

.file-name-cell {
  display: block;
  overflow: hidden;
  color: var(--text-primary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.section-title-row {
  justify-content: flex-start;
  margin-bottom: 12px;
}

.section-heading-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.section-title-row h3 {
  margin: 0;
  color: var(--text-primary);
  font-size: 16px;
}

.case-run-block {
  margin-bottom: 14px;
  padding: 14px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
}

.case-run-header {
  align-items: flex-start;
  margin-bottom: 12px;
}

.case-run-header strong {
  display: block;
  color: var(--text-primary);
  font-size: 13px;
}

.web-traces-section {
  margin-top: 24px;
}

.trace-attempts {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.failure-analysis-section {
  margin-top: 24px;
  padding-top: 18px;
  border-top: 1px solid var(--border-color);
}

.failure-analysis-alert {
  margin-bottom: 12px;
}

.failure-analysis-target-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 14px 0;
}

.failure-analysis-case-select {
  min-width: min(420px, 100%);
}

.failure-analysis-target-label {
  color: var(--text-primary);
  font-size: 13px;
}

.failure-analysis-config-panel {
  margin-bottom: 14px;
}

.failure-analysis-generate-row {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(260px, 1.4fr) auto;
  gap: 10px;
  align-items: start;
  margin-top: 12px;
}

.failure-analysis-prompt-select {
  width: 100%;
}

.failure-analysis-config-notes {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.failure-analysis-history-panel {
  min-height: 60px;
}

.failure-analysis-history-error {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.failure-analysis-history {
  margin-top: 14px;
}

.failure-analysis-history-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.failure-analysis-history-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.failure-analysis-history-item:hover,
.failure-analysis-history-item.active {
  border-color: var(--el-color-primary);
  background: var(--surface-subtle);
}

.failure-analysis-history-item span {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.failure-analysis-history-item small {
  color: var(--text-secondary);
  font-size: 11px;
}

.failure-analysis-meta-grid,
.failure-analysis-result-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-top: 14px;
}

.failure-analysis-result-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.failure-analysis-meta-grid > div,
.failure-analysis-result-grid > div {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  padding: 10px;
  border-radius: 8px;
  background: var(--surface-subtle);
}

.failure-analysis-meta-grid span,
.failure-analysis-result-grid span {
  color: var(--text-secondary);
  font-size: 12px;
}

.failure-analysis-meta-grid strong,
.failure-analysis-result-grid strong {
  color: var(--text-primary);
  font-size: 13px;
  word-break: break-word;
}

.failure-analysis-wide {
  grid-column: span 2;
}

.failure-analysis-wide ul {
  margin: 0;
  padding-left: 18px;
  color: var(--text-primary);
  font-size: 13px;
}

.healing-section {
  margin-top: 24px;
  padding-top: 18px;
  border-top: 1px solid var(--border-color);
}

.healing-alert {
  margin-bottom: 12px;
}

.healing-version-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.healing-config-panel {
  margin-bottom: 14px;
}

.healing-generate-row {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(260px, 1.4fr) auto;
  gap: 10px;
  align-items: start;
  margin-top: 12px;
}

.healing-prompt-select {
  width: 100%;
}

.healing-history {
  margin-top: 14px;
}

.healing-history-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.healing-history-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.healing-history-item:hover,
.healing-history-item.active {
  border-color: var(--el-color-primary);
  background: var(--surface-subtle);
}

.healing-history-item span {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.healing-history-item small {
  color: var(--text-secondary);
  font-size: 11px;
}

.healing-audit-grid,
.healing-locator-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-top: 14px;
}

.healing-locator-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.healing-audit-grid > div,
.healing-locator-grid > div {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  padding: 10px;
  border-radius: 8px;
  background: var(--surface-subtle);
}

.healing-audit-grid span,
.healing-locator-grid span {
  color: var(--text-secondary);
  font-size: 12px;
}

.healing-audit-grid strong,
.healing-locator-grid strong {
  color: var(--text-primary);
  font-size: 13px;
  word-break: break-word;
}

.healing-candidate-panel {
  margin-top: 14px;
  padding: 12px;
  border: 1px dashed var(--border-color);
  border-radius: 8px;
}

.healing-candidate-list {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  margin-top: 10px;
}

.healing-candidate-option {
  height: auto;
  line-height: 1.5;
  white-space: normal;
}

.healing-decision-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 14px;
}

.healing-decision-form {
  margin-top: 14px;
}

@media (max-width: 1100px) {
  .resource-grid {
    grid-template-columns: 1fr;
  }

  .target-resource-card {
    grid-column: auto;
  }

  .resource-card {
    min-height: auto;
  }

  .failure-analysis-generate-row {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 680px) {
  .selector-row,
  .selector-field,
  .create-actions,
  .card-heading-row,
  .run-type-field,
  .runs-heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .project-select {
    width: 100%;
  }

  .selected-project-note {
    align-items: flex-start;
  }

  .list-heading-actions {
    width: 100%;
    flex-wrap: wrap;
    justify-content: space-between;
  }

  .target-switch-hint {
    margin-left: 0;
  }

  .failure-analysis-target-row {
    align-items: flex-start;
    flex-direction: column;
  }

  .failure-analysis-case-select,
  .failure-analysis-generate-row > * {
    width: 100%;
  }

  .failure-analysis-meta-grid,
  .failure-analysis-result-grid {
    grid-template-columns: 1fr;
  }

  .failure-analysis-wide {
    grid-column: auto;
  }
}
</style>
