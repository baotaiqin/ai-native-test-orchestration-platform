<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { FullScreen, MagicStick, Refresh, Select } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute } from 'vue-router'

import { getPrompts } from '@/api/prompt-center'
import { getRequirementTree } from '@/api/requirements'
import {
  bulkDecideCaseSuggestions, bulkDeleteCaseSuggestions, bulkEditCaseSuggestions,
  createCaseDesignTask, createCaseGenerationTask, decideCaseSuggestion, editCaseSuggestion,
  getCaseDesignTasks, getCaseGenerationTasks, getCaseGenerations, getRequirementCaseLinks,
  recompileCaseGeneration,
} from '@/api/test-cases'
import type { PromptDefinition } from '@/types/prompt-center'
import type { RequirementTreeNode } from '@/types/requirement'
import type {
  ApiRequestTemplate, CaseDesignPlan, CaseDesignTask, CaseGeneration, CaseGenerationTask,
  CasePriority, CaseSuggestion, CaseType, RetryCondition, RetryPolicy, SuggestedCase,
} from '@/types/test-case'
import {
  API_STEP_RETRY_LIMIT_ERROR_MESSAGE,
  isV1ApiStepMaxRetries,
  retryAwareApiErrorMessage,
  v1RetryPolicyError,
} from '@/utils/v1-retry-policy'
import { showAiGenerationError } from '@/utils/ai-error'
import { formatApiDateTime } from '@/utils/datetime'
import { promptOptionLabel } from '@/utils/prompt-display'
import { RECORD_PAGE_SIZES, useClientPagination } from '@/composables/useClientPagination'

const props = defineProps<{
  requirementId: number
  autoOpen?: boolean
}>()
const emit = defineEmits<{ designTaskCreated: []; openDesignRecords: [] }>()
const route = useRoute()
const generations = ref<CaseGeneration[]>([])
const generationTasks = ref<CaseGenerationTask[]>([])
const prompts = ref<PromptDefinition[]>([])
const designPrompts = ref<PromptDefinition[]>([])
const coverageCount = ref(0)
const checkedIds = ref<number[]>([])
const selected = ref<CaseSuggestion | null>(null)
const generateVisible = ref(false)
const bulkEditVisible = ref(false)
const detailVisible = ref(false)
const resultVisible = ref(false)
const resultFullscreen = ref(false)
const resultGenerationId = ref<number | null>(null)
const resultTab = ref<'CASES' | 'COVERAGE'>('CASES')
const generating = ref(false)
const designLoading = ref(false)
const designStep = ref(0)
const designPlan = ref<CaseDesignPlan | null>(null)
const designTask = ref<CaseDesignTask | null>(null)
const selectedApiIds = ref<number[]>([])
const savingSuggestionId = ref<number | null>(null)
const decidingSuggestionId = ref<number | null>(null)
const bulkSaving = ref(false)
const recompiling = ref(false)
const editBaseResult = ref<ExtensibleSuggestedCase | null>(null)
let loadSequence = 0
let panelContextSequence = 0
let panelActive = true
let taskPollingTimer: number | undefined
let designPollingTimer: number | undefined
const generateForm = reactive({ promptId: undefined as number | undefined, instructions: '' })
const requirementOptions = ref<RequirementTreeNode[]>([])
const requirementOptionsProjectId = ref<number | null>(null)
const requirementOptionsLoading = ref(false)
const bulkEditForm = reactive({
  applyPriority: false, priority: 'P1' as SuggestedCase['priority'],
  applyTags: false, tags: '',
  applyRequirements: false, requirementIds: [] as number[],
})
const editForm = reactive({
  title: '', caseType: 'API' as CaseType, priority: 'P1' as CasePriority,
  preconditions: '', steps: '', testData: '{}', expectedResult: '', tags: '',
  confidence: 0.8, note: '',
  requestMethod: 'GET' as ApiRequestTemplate['method'], requestUrl: '{{base_url}}/',
  requestQuery: '[]', requestHeaders: '[]', requestCookies: '[]',
  requestBodyType: 'NONE' as ApiRequestTemplate['body']['type'], requestBody: '',
  authType: 'NONE' as ApiRequestTemplate['auth']['type'], authToken: '',
  authUsername: '', authPassword: '', authKeyName: '', authKeyValue: '',
  authPlacement: 'HEADER' as 'HEADER' | 'QUERY', timeoutMs: 30000,
  followRedirects: true,
  retryPolicy: { max_retries: 0, backoff_ms: 500, retry_on: [] } as RetryPolicy,
  preActions: '[]', postActions: '[]', cleanup: '[]',
  dataSource: 'null', extractors: '[]', assertions: '[]',
})
type ExtensibleSuggestedCase = SuggestedCase & Record<string, unknown>
type ReviewEditorStep = 'basic' | 'request' | 'actions' | 'verification' | 'outcome'
type ReviewEditorStepItem = { key: ReviewEditorStep; title: string; description: string }
interface PanelContext {
  requirementId: number
  contextSequence: number
}
interface SuggestionOperation extends PanelContext {
  suggestionId: number
  projectId: number
  note?: string
}
interface ReviewOperation extends SuggestionOperation {
  payload: ExtensibleSuggestedCase
}
interface QualityCheck {
  label: string
  passed: boolean
  weight: number
  detail: string
}
interface QualityAssessment {
  score: number
  passedCount: number
  checks: QualityCheck[]
}
const suggestions = computed(() => generations.value.flatMap((item) => item.suggestions))
const resultGeneration = computed(() => (
  generations.value.find((item) => item.id === resultGenerationId.value) ?? null
))
const resultSuggestions = computed(() => resultGeneration.value?.suggestions ?? [])
const designCheckPoints = computed(() => designPlan.value?.check_points ?? [])
const designRecommendedApis = computed(() => designPlan.value?.recommended_apis ?? [])
const {
  items: pagedGenerationTasks, total: generationTaskTotal, page: generationTaskPage,
  pageSize: generationTaskPageSize, changePage: changeGenerationTaskPage,
  changePageSize: changeGenerationTaskPageSize,
} = useClientPagination(generationTasks)
const {
  items: pagedSuggestions, total: suggestionTotal, page: suggestionPage,
  pageSize: suggestionPageSize, changePage: changeSuggestionPage,
  changePageSize: changeSuggestionPageSize,
} = useClientPagination(resultSuggestions)
const {
  items: pagedDesignCheckPoints, total: designCheckPointTotal, page: designCheckPointPage,
  pageSize: designCheckPointPageSize, changePage: changeDesignCheckPointPage,
  changePageSize: changeDesignCheckPointPageSize,
} = useClientPagination(designCheckPoints)
const {
  items: pagedDesignRecommendedApis, total: designRecommendedApiTotal,
  page: designRecommendedApiPage, pageSize: designRecommendedApiPageSize,
  changePage: changeDesignRecommendedApiPage,
  changePageSize: changeDesignRecommendedApiPageSize,
} = useClientPagination(designRecommendedApis)
const resultCoveredKeys = computed(() => {
  const generation = resultGeneration.value
  if (!generation) return new Set<string>()
  const plannedKeys = new Set(generation.coverage_plan.check_points.map((item) => item.key))
  return new Set(generation.suggestions.flatMap((item) => (
    effectiveSuggestion(item).tags
      .map((tag) => tag.match(/^coverage:(CP-\d+)$/i)?.[1]?.toUpperCase())
      .filter((key): key is string => Boolean(key) && plannedKeys.has(key as string))
  )))
})
const resultCoveragePercent = computed(() => {
  const total = resultGeneration.value?.coverage_plan.check_points.length ?? 0
  return total ? Math.round((resultCoveredKeys.value.size / total) * 100) : 0
})
const hasActiveGenerationTask = computed(() => generationTasks.value.some(
  (item) => item.status === 'QUEUED' || item.status === 'RUNNING',
))
const taskStatusLabel = {
  QUEUED: '等待执行', RUNNING: '正在生成', SUCCEEDED: '生成成功', FAILED: '生成失败',
} as const
const taskStatusType = {
  QUEUED: 'info', RUNNING: 'warning', SUCCEEDED: 'success', FAILED: 'danger',
} as const
const draftSuggestions = computed(() => resultSuggestions.value.filter((item) => item.status === 'DRAFT'))
const acceptedSuggestionCount = computed(() => resultSuggestions.value.filter(
  (item) => item.status === 'ACCEPTED',
).length)
const rejectedSuggestionCount = computed(() => resultSuggestions.value.filter(
  (item) => item.status === 'REJECTED',
).length)
const statusType = { DRAFT: 'warning', ACCEPTED: 'success', REJECTED: 'danger' } as const
const statusLabel = { DRAFT: '草稿', ACCEPTED: '已保存', REJECTED: '已拒绝' } as const
const caseTypeLabel = { API: 'API', WEB: 'Web', MANUAL: '手工' } as const
const operationBusy = computed(() => (
  savingSuggestionId.value !== null || decidingSuggestionId.value !== null
  || bulkSaving.value || recompiling.value
))
const retryNeedsCorrection = computed(() => (
  editForm.caseType === 'API'
  && Boolean(editBaseResult.value?.request)
  && !isV1ApiStepMaxRetries(editForm.retryPolicy.max_retries)
))
const draftSuggestionIds = computed(() => draftSuggestions.value.map((item) => item.id))
const allDraftsChecked = computed(() => (
  draftSuggestionIds.value.length > 0
  && draftSuggestionIds.value.every((id) => checkedIds.value.includes(id))
))
const someDraftsChecked = computed(() => (
  !allDraftsChecked.value
  && draftSuggestionIds.value.some((id) => checkedIds.value.includes(id))
))
const reviewEditorStep = ref<ReviewEditorStep>('basic')
const reviewEditorSteps = computed<ReviewEditorStepItem[]>(() => {
  const steps: ReviewEditorStepItem[] = [
    { key: 'basic', title: '基础信息', description: '名称、类型与通用数据' },
  ]
  if (editForm.caseType === 'API') {
    steps.push(
      { key: 'request', title: '请求配置', description: '地址、参数、认证与重试' },
      { key: 'actions', title: '动作与清理', description: '前后置动作和资源回收' },
      { key: 'verification', title: '数据与断言', description: '数据源、提取器与校验规则' },
    )
  }
  steps.push({ key: 'outcome', title: '结果与说明', description: '预期结果、标签与审核说明' })
  return steps
})
const reviewEditorStepIndex = computed(() => Math.max(
  0, reviewEditorSteps.value.findIndex((item) => item.key === reviewEditorStep.value),
))
const reviewEditorStepMeta = computed(() => reviewEditorSteps.value[reviewEditorStepIndex.value])
const reviewEditorIsFirst = computed(() => reviewEditorStepIndex.value === 0)
const reviewEditorIsLast = computed(() => reviewEditorStepIndex.value === reviewEditorSteps.value.length - 1)
const reviewEditorLocked = computed(() => selected.value?.status !== 'DRAFT' || operationBusy.value)
const retryConditionOptions: Array<{ value: RetryCondition; label: string }> = [
  { value: 'TARGET_NETWORK_ERROR', label: '网络错误' },
  { value: 'TARGET_TIMEOUT', label: '请求超时' },
  { value: 'HTTP_5XX', label: '服务端 5xx 响应' },
]
const selectedGeneration = computed(() => (
  generations.value.find((generation) => checkedIds.value.some(
    (id) => generation.suggestions.some((suggestion) => suggestion.id === id),
  ))
))
const currentGeneration = computed(() => (
  selected.value ? generationForSuggestion(selected.value.id) : undefined
))
const selectedQuality = computed(() => (
  selected.value ? assessCaseQuality(selected.value) : undefined
))
const selectedAiConfidence = computed(() => (
  selected.value ? explicitAiConfidence(selected.value) : undefined
))
const selectedResultIndex = computed(() => (
  selected.value ? resultSuggestions.value.findIndex((item) => item.id === selected.value?.id) : -1
))

function effectiveSuggestion(item: CaseSuggestion): SuggestedCase {
  return item.human_result ?? item.structured_result
}

function sensitiveValuesAreBound(value: unknown, runtimeNames: Set<string>): boolean {
  if (Array.isArray(value)) return value.every((item) => sensitiveValuesAreBound(item, runtimeNames))
  if (!value || typeof value !== 'object') return true
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    if (/(password|passwd|secret|token|authorization|cookie|api.?key|credential)/i.test(key)) {
      if (typeof child !== 'string') return false
      const secret = child.match(/^\{\{secret\.([A-Za-z_][A-Za-z0-9_.-]*)\}\}$/)
      const runtime = child.match(/^\{\{([A-Za-z_][A-Za-z0-9_.-]*)\}\}$/)
      if (!secret && !(runtime?.[1] && runtimeNames.has(runtime[1]))) return false
    }
    if (!sensitiveValuesAreBound(child, runtimeNames)) return false
  }
  return true
}

function isBoundSensitiveReference(value: unknown, runtimeNames: Set<string>): boolean {
  if (typeof value !== 'string') return false
  if (/^\{\{secret\.[A-Za-z_][A-Za-z0-9_.-]*\}\}$/.test(value)) return true
  const runtime = value.match(/^\{\{([A-Za-z_][A-Za-z0-9_.-]*)\}\}$/)
  return Boolean(runtime?.[1] && runtimeNames.has(runtime[1]))
}

function requestExecutionContextReady(
  request: ApiRequestTemplate,
  runtimeNames: Set<string>,
  sensitiveRuntimeNames: Set<string>,
): boolean {
  const references = [...JSON.stringify(request).matchAll(/\{\{([A-Za-z_][A-Za-z0-9_.-]*)\}\}/g)]
    .map((match) => match[1]!)
    .filter((name) => !name.startsWith('secret.'))
  if (references.some((name) => !['base_url', 'project_id', 'environment_id'].includes(name)
    && !runtimeNames.has(name))) return false
  if (!sensitiveValuesAreBound(request.body.content, sensitiveRuntimeNames)) return false
  if (request.cookies.some((item) => item.enabled
    && !isBoundSensitiveReference(item.value, sensitiveRuntimeNames))) return false
  if ([...request.headers, ...request.query_params].some((item) => item.enabled
    && /(password|passwd|secret|token|authorization|cookie|api.?key|credential)/i.test(item.name)
    && !isBoundSensitiveReference(item.value, sensitiveRuntimeNames))) return false
  if (request.auth.type === 'BEARER') {
    return isBoundSensitiveReference(request.auth.token, sensitiveRuntimeNames)
  }
  if (request.auth.type === 'BASIC') {
    return Boolean(request.auth.username)
      && isBoundSensitiveReference(request.auth.password, sensitiveRuntimeNames)
  }
  if (request.auth.type === 'API_KEY') {
    return Boolean(request.auth.key_name)
      && isBoundSensitiveReference(request.auth.key_value, sensitiveRuntimeNames)
  }
  return true
}

function executionContextReady(result: SuggestedCase): boolean {
  if (result.case_type !== 'API') return true
  if (!result.request) return false
  const runtimeNames = new Set<string>()
  const sensitiveRuntimeNames = new Set<string>()
  for (const action of result.pre_actions ?? []) {
    if (action.enabled === false) continue
    if ('type' in action && (action.type === 'GET_TOKEN' || action.type === 'API_SETUP')) {
      if (!action.request
        || !requestExecutionContextReady(action.request, runtimeNames, sensitiveRuntimeNames)) {
        return false
      }
    }
    const outputName = 'result_variable' in action ? action.result_variable
      : 'name' in action ? action.name : undefined
    if (typeof outputName === 'string') runtimeNames.add(outputName)
    if ('type' in action && ['GET_TOKEN', 'API_SETUP', 'FAKER'].includes(action.type)
      && typeof outputName === 'string') sensitiveRuntimeNames.add(outputName)
  }
  if (!requestExecutionContextReady(result.request, runtimeNames, sensitiveRuntimeNames)) return false
  const title = result.title.toLowerCase()
  const hasLoginContext = result.request.auth.type !== 'NONE'
    || result.request.cookies.some((item) => item.enabled)
    || (result.pre_actions ?? []).some((item) => 'type' in item && item.type === 'GET_TOKEN' && item.enabled)
  if (/(已登录|受保护接口|认证用户)/.test(title) && !hasLoginContext) return false
  if (/(过滤|筛选|filter)/.test(title)
    && !result.request.query_params.some((item) => item.enabled)) return false
  if (/(重试|retry)/.test(title) && (result.request.retry_policy?.max_retries ?? 0) < 1) return false
  return true
}

function assessCaseQuality(item: CaseSuggestion): QualityAssessment {
  const result = effectiveSuggestion(item)
  const assertions = (result.assertions ?? []).filter((assertion) => assertion.enabled !== false)
  const hasBasicContent = Boolean(result.title.trim() && result.expected_result.trim()
    && result.steps.length && result.steps.every((step) => step.action.trim() && step.expected.trim()))
  const hasRequestConfiguration = result.case_type !== 'API'
    || Boolean(result.request?.method && result.request.url.trim())
  const hasBasicAssertion = result.case_type !== 'API' || assertions.length > 0
  const hasResponseAssertion = assertions.some((assertion) => (
    !['STATUS_CODE', 'RESPONSE_TIME'].includes(assertion.type.trim().toUpperCase())
  ))
  const hasTestContext = executionContextReady(result)
  const checks: QualityCheck[] = [
    {
      label: '基础内容完整', weight: 20,
      passed: hasBasicContent,
      detail: hasBasicContent ? '标题、步骤和期望结果均已填写' : '请补全标题、步骤和期望结果',
    },
    {
      label: result.case_type === 'API' ? '请求配置完整' : '步骤可以执行', weight: 25,
      passed: hasRequestConfiguration,
      detail: result.case_type === 'API'
        ? (hasRequestConfiguration ? '包含请求方法和请求地址' : '缺少请求方法或请求地址，请重新生成或在正式用例中补充')
        : '包含明确的执行步骤',
    },
    {
      label: '基础断言有效', weight: 20,
      passed: hasBasicAssertion,
      detail: hasBasicAssertion ? '至少包含一条启用的断言' : '请至少添加一条启用的断言',
    },
    {
      label: '响应验证充分', weight: 20,
      passed: result.case_type !== 'API' || hasResponseAssertion,
      detail: result.case_type !== 'API' || hasResponseAssertion
        ? '除状态码或耗时外，还验证了响应内容'
        : '请添加响应正文、字段或结构断言',
    },
    {
      label: '执行上下文完整', weight: 15,
      passed: hasTestContext,
      detail: hasTestContext
        ? '请求变量、敏感值与登录前置均具备可执行来源'
        : '存在明文敏感值、未解析变量，或标题声明的登录/过滤/重试配置缺失',
    },
  ]
  return {
    score: checks.reduce((total, check) => total + (check.passed ? check.weight : 0), 0),
    passedCount: checks.filter((check) => check.passed).length,
    checks,
  }
}

function explicitAiConfidence(item: CaseSuggestion): number | undefined {
  const generation = generationForSuggestion(item.id)
  return generation?.ai_confidence_by_sequence?.[String(item.sequence_no)]
}

function flattenRequirements(items: RequirementTreeNode[]): RequirementTreeNode[] {
  return items.flatMap((item) => [item, ...flattenRequirements(item.children)])
}
const flatRequirementOptions = computed(() => flattenRequirements(requirementOptions.value))
const selectedCoveragePoints = computed(() => {
  if (!selected.value || !currentGeneration.value) return []
  const keys = new Set(
    effectiveSuggestion(selected.value).tags
      .map((tag) => tag.match(/^coverage:(CP-\d+)$/i)?.[1]?.toUpperCase())
      .filter((key): key is string => Boolean(key)),
  )
  return currentGeneration.value.coverage_plan.check_points.filter((point) => keys.has(point.key))
})
const selectedRequirementRefs = computed(() => {
  const byCode = new Map(flatRequirementOptions.value.map((item) => [item.code, item]))
  const refs = selectedCoveragePoints.value.map((point) => {
    const sourceCode = flatRequirementOptions.value.find((item) => (
      point.source === item.code || point.source.startsWith(`${item.code} ·`)
    ))?.code ?? point.source
    const requirement = byCode.get(sourceCode)
    return {
      key: sourceCode,
      code: requirement?.code ?? sourceCode,
      title: requirement?.title ?? point.title,
    }
  })
  if (selected.value) {
    for (const requirementId of selected.value.linked_requirement_ids) {
      const requirement = flatRequirementOptions.value.find((item) => item.id === requirementId)
      if (requirement) refs.push({ key: requirement.code, code: requirement.code, title: requirement.title })
    }
  }
  return [...new Map(refs.map((item) => [item.key, item])).values()]
})

async function load(): Promise<void> {
  const requestedRequirementId = props.requirementId
  const requestedContextSequence = panelContextSequence
  const requestSequence = ++loadSequence
  const [generationItems, taskItems, links] = await Promise.all([
    getCaseGenerations(requestedRequirementId), getCaseGenerationTasks(requestedRequirementId),
    getRequirementCaseLinks(requestedRequirementId),
  ])
  if (!panelActive
    || requestSequence !== loadSequence
    || panelContextSequence !== requestedContextSequence
    || props.requirementId !== requestedRequirementId) return
  generations.value = generationItems
  generationTasks.value = taskItems
  coverageCount.value = links.length
  checkedIds.value = checkedIds.value.filter((id) =>
    draftSuggestions.value.some((item) => item.id === id),
  )
  if (detailVisible.value && selected.value) {
    selected.value = suggestions.value.find((item) => item.id === selected.value?.id) ?? selected.value
  }
  scheduleTaskPolling()
}

function scheduleTaskPolling(): void {
  if (taskPollingTimer !== undefined) window.clearTimeout(taskPollingTimer)
  taskPollingTimer = undefined
  if (!panelActive || !hasActiveGenerationTask.value) return
  taskPollingTimer = window.setTimeout(() => { void load() }, 3_000)
}

function formatTaskTime(value: string): string {
  return formatApiDateTime(value)
}

function toggleAllDrafts(value: unknown): void {
  checkedIds.value = value ? [...draftSuggestionIds.value] : []
}

async function ensureRequirementOptions(projectId: number): Promise<void> {
  if (requirementOptionsProjectId.value === projectId && requirementOptions.value.length) return
  requirementOptionsLoading.value = true
  try {
    const items = await getRequirementTree(projectId)
    if (!panelActive) return
    requirementOptions.value = items
    requirementOptionsProjectId.value = projectId
  } catch {
    if (panelActive) ElMessage.warning('对应需求标题加载失败，将仅显示需求编号')
  } finally {
    requirementOptionsLoading.value = false
  }
}

async function copyTechnicalTrace(): Promise<void> {
  const generation = currentGeneration.value
  if (!generation) return
  if (!navigator.clipboard?.writeText) {
    ElMessage.warning('当前浏览器不支持复制，请手工记录追踪信息')
    return
  }
  const trace = [
    `需求记录 ID：${generation.requirement_id}`,
    `需求版本记录 ID：${generation.requirement_version_id}`,
    `AI 调用记录 ID：${generation.ai_call_id}`,
    `提示词版本记录 ID：${generation.prompt_version_id}`,
    `输出结构记录 ID：${generation.output_schema_id ?? '未绑定'}`,
  ].join('\n')
  try {
    await navigator.clipboard.writeText(trace)
    ElMessage.success('技术追踪信息已复制')
  } catch {
    ElMessage.error('复制失败，请手工记录追踪信息')
  }
}

function openTaskResult(task: CaseGenerationTask): void {
  if (!task.generation_id) return
  const generation = generations.value.find((item) => item.id === task.generation_id)
  if (!generation) {
    ElMessage.info('生成结果已完成，请刷新记录后查看')
    return
  }
  resultGenerationId.value = generation.id
  resultTab.value = 'CASES'
  resultFullscreen.value = false
  resultVisible.value = true
  checkedIds.value = []
  changeSuggestionPage(1)
  void ensureRequirementOptions(generation.project_id)
}

function generationSuggestionCount(task: CaseGenerationTask): number {
  if (!task.generation_id) return 0
  return generations.value.find((item) => item.id === task.generation_id)?.suggestions.length ?? 0
}

function closeResult(): void {
  resultVisible.value = false
  resultFullscreen.value = false
  resultGenerationId.value = null
  checkedIds.value = []
  detailVisible.value = false
  selected.value = null
  editBaseResult.value = null
}

function openAdjacentSuggestion(offset: -1 | 1): void {
  const target = resultSuggestions.value[selectedResultIndex.value + offset]
  if (target) openDetail(target)
}

async function retryGenerationTask(task: CaseGenerationTask): Promise<void> {
  const context = capturePanelContext()
  generateVisible.value = true
  designLoading.value = true
  try {
    const designTaskId = Number(
      (task.coverage_plan as CaseDesignPlan & { design_task_id?: unknown }).design_task_id,
    )
    const [loadedPrompts, designTasks] = await Promise.all([
      prompts.value.length ? Promise.resolve(prompts.value) : getPrompts(false).then(
        (items) => items.filter((item) => item.task_type === 'API_CASE_GENERATE'),
      ),
      getCaseDesignTasks(context.requirementId),
    ])
    if (!panelContextIsCurrent(context)) return
    const sourceDesignTask = designTasks.find(
      (item) => item.id === designTaskId && item.status === 'SUCCEEDED' && item.plan,
    )
    if (!sourceDesignTask) throw new Error('原 AI 设计记录不可用，请重新开始 AI 设计')
    prompts.value = loadedPrompts
    applyDesignTask(sourceDesignTask)
    selectedApiIds.value = [...task.selected_api_definition_ids]
    generateForm.promptId = loadedPrompts[0]?.id
    generateForm.instructions = task.additional_instructions ?? ''
    changeDesignCheckPointPage(1)
    changeDesignRecommendedApiPage(1)
    designStep.value = 2
  } catch (error) {
    generateVisible.value = false
    if (panelContextIsCurrent(context)) showAiGenerationError(error, '恢复失败任务的设计上下文失败')
  } finally {
    designLoading.value = false
  }
}

async function recompileSavedCases(): Promise<void> {
  const generation = resultGeneration.value
  if (!generation || recompiling.value) return
  try {
    await ElMessageBox.confirm(
      '系统将复用本次已经保存的 AI 原始意图，用最新平台编译器为符合条件的正式用例创建新版本。不会再次调用 AI，也不会覆盖人工修改过的版本。',
      '重新编译已保存用例',
      { confirmButtonText: '开始重新编译', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  recompiling.value = true
  try {
    const result = await recompileCaseGeneration(generation.id)
    await load()
    if (result.failed_count) {
      ElMessage.warning(
        `已创建 ${result.recompiled_count} 个新版本，跳过 ${result.skipped_count} 条，${result.failed_count} 条未通过编译门禁`,
      )
    } else {
      ElMessage.success(
        `已创建 ${result.recompiled_count} 个新版本，跳过 ${result.skipped_count} 条；未调用 AI`,
      )
    }
  } catch (error) {
    showAiGenerationError(error, '重新编译已保存用例失败')
  } finally {
    recompiling.value = false
  }
}

function requestedApiIds(): number[] {
  const queryValue = route.query.design_api_ids
  const queryItems = Array.isArray(queryValue) ? queryValue : [queryValue]
  return [...new Set(queryItems
    .flatMap((item) => String(item ?? '').split(','))
    .map(Number)
    .filter((item) => Number.isInteger(item) && item > 0))]
}

function applyDesignTask(task: CaseDesignTask): void {
  designTask.value = task
  if (!task.plan) return
  designPlan.value = task.plan
  selectedApiIds.value = task.plan.recommended_apis
    .filter((item) => item.selected_by_default)
    .map((item) => item.api_definition_id)
  designLoading.value = false
}

function scheduleDesignPolling(context: PanelContext): void {
  if (designPollingTimer !== undefined) window.clearTimeout(designPollingTimer)
  designPollingTimer = undefined
  if (!panelContextIsCurrent(context) || !generateVisible.value || !designTask.value
    || !['QUEUED', 'RUNNING'].includes(designTask.value.status)) return
  designPollingTimer = window.setTimeout(async () => {
    try {
      const items = await getCaseDesignTasks(context.requirementId)
      if (!panelContextIsCurrent(context)) return
      const current = items.find((item) => item.id === designTask.value?.id)
      if (current) applyDesignTask(current)
      if (current?.status === 'FAILED') {
        designLoading.value = false
        ElMessage.error(current.error_message || 'AI 测试设计失败，请重新分析')
      } else scheduleDesignPolling(context)
    } catch {
      if (panelContextIsCurrent(context)) scheduleDesignPolling(context)
    }
  }, 2_500)
}

async function openGenerate(forceRefresh = false): Promise<void> {
  if (hasActiveGenerationTask.value) {
    ElMessage.info('当前需求已有 AI 任务在执行，可在生成记录中查看进度')
    return
  }
  const context = capturePanelContext()
  generateVisible.value = true
  designStep.value = 0
  designPlan.value = null
  designTask.value = null
  changeDesignCheckPointPage(1)
  changeDesignRecommendedApiPage(1)
  selectedApiIds.value = []
  designLoading.value = true
  try {
    const [loadedPrompts, loadedDesignPrompts] = await Promise.all([
      prompts.value.length ? Promise.resolve(prompts.value) : getPrompts(false).then((items) => items.filter(
        (item) => item.task_type === 'API_CASE_GENERATE',
      )),
      designPrompts.value.length ? Promise.resolve(designPrompts.value) : getPrompts(false).then(
        (items) => items.filter((item) => item.task_type === 'API_TEST_DESIGN'),
      ),
    ])
    if (!panelContextIsCurrent(context)) return
    prompts.value = loadedPrompts
    designPrompts.value = loadedDesignPrompts
    if (!loadedDesignPrompts.length) throw new Error('未配置 AI 测试设计 Prompt')
    generateForm.promptId = prompts.value[0]?.id
    generateForm.instructions = ''
    const task = await createCaseDesignTask(context.requirementId, {
      prompt_id: loadedDesignPrompts[0].id,
      include_api_ids: requestedApiIds(),
      force_refresh: forceRefresh,
    })
    if (!panelContextIsCurrent(context)) return
    applyDesignTask(task)
    emit('designTaskCreated')
    if (task.status === 'QUEUED' || task.status === 'RUNNING') scheduleDesignPolling(context)
  } catch (error) {
    designLoading.value = false
    if (panelContextIsCurrent(context)) showAiGenerationError(error, 'AI 测试设计启动失败')
  } finally {
    if (designPlan.value) designLoading.value = false
  }
}

async function openDesignRecord(task: CaseDesignTask): Promise<void> {
  if (task.requirement_id !== props.requirementId) return
  const context = capturePanelContext()
  generateVisible.value = true
  designStep.value = 0
  designPlan.value = null
  designTask.value = task
  changeDesignCheckPointPage(1)
  changeDesignRecommendedApiPage(1)
  selectedApiIds.value = []
  designLoading.value = task.status === 'QUEUED' || task.status === 'RUNNING'
  try {
    if (!prompts.value.length) {
      prompts.value = (await getPrompts(false)).filter(
        (item) => item.task_type === 'API_CASE_GENERATE',
      )
    }
    if (!panelContextIsCurrent(context)) return
    generateForm.promptId = prompts.value[0]?.id
    applyDesignTask(task)
    if (task.status === 'QUEUED' || task.status === 'RUNNING') {
      scheduleDesignPolling(context)
    }
  } catch (error) {
    if (panelContextIsCurrent(context)) showAiGenerationError(error, 'AI 设计记录打开失败')
  }
}

defineExpose({ openDesignRecord })

function nextDesignStep(): void {
  if (designStep.value === 1 && !selectedApiIds.value.length) {
    ElMessage.warning('请至少选择一个 API，再生成可执行用例')
    return
  }
  designStep.value = Math.min(2, designStep.value + 1)
}

async function generate(): Promise<void> {
  if (!generateForm.promptId) { ElMessage.warning('请先创建 API 用例生成提示词'); return }
  const context = capturePanelContext()
  const promptId = generateForm.promptId
  const instructions = generateForm.instructions
  generating.value = true
  try {
    await createCaseGenerationTask(context.requirementId, {
      prompt_id: promptId,
      additional_instructions: instructions || undefined,
      selected_api_definition_ids: selectedApiIds.value,
      design_task_id: designTask.value?.id,
    })
    if (!panelContextIsCurrent(context)) return
    generateVisible.value = false
    ElMessage.success('AI 生成任务已创建，可离开页面后稍后查看')
    await load()
  } catch (error) {
    if (panelContextIsCurrent(context)) showAiGenerationError(error, 'AI 用例建议生成失败')
  }
  finally { generating.value = false }
}

function lines(values: string[]): string { return values.join('\n') }
function parseLines(value: string): string[] {
  return value.split('\n').map((item) => item.trim()).filter(Boolean)
}

function jsonText(value: unknown, fallback: unknown): string {
  return JSON.stringify(value ?? fallback, null, 2)
}

function parseJsonArray(value: string, label: string): unknown[] {
  const parsed: unknown = JSON.parse(value)
  if (!Array.isArray(parsed)) throw new Error(`${label}必须是 JSON 数组`)
  return parsed
}

function parseJsonObject(value: string, label: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value)
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(`${label}必须是 JSON 对象`)
  }
  return parsed as Record<string, unknown>
}

function parseJsonObjectOrNull(value: string, label: string): Record<string, unknown> | null {
  const parsed: unknown = JSON.parse(value)
  if (parsed === null) return null
  if (typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(`${label}必须是 JSON 对象或 null`)
  }
  return parsed as Record<string, unknown>
}

function normalizedRetryPolicy(value: Partial<RetryPolicy> | null | undefined): RetryPolicy {
  return {
    max_retries: value?.max_retries ?? 0,
    backoff_ms: value?.backoff_ms ?? 500,
    retry_on: Array.isArray(value?.retry_on) ? [...value.retry_on] : [],
  }
}

function cloneResult<T extends SuggestedCase>(value: T): T & Record<string, unknown> {
  return JSON.parse(JSON.stringify(value)) as T & Record<string, unknown>
}

function generationForSuggestion(id: number): CaseGeneration | undefined {
  return generations.value.find((generation) => (
    generation.suggestions.some((suggestion) => suggestion.id === id)
  ))
}

function openDetail(item: CaseSuggestion): void {
  selected.value = item
  const result = cloneResult(item.human_result ?? item.structured_result)
  const request = result.request
  editBaseResult.value = result
  Object.assign(editForm, {
    title: result.title, caseType: result.case_type, priority: result.priority,
    preconditions: lines(result.preconditions), steps: JSON.stringify(result.steps, null, 2),
    testData: JSON.stringify(result.test_data, null, 2),
    expectedResult: result.expected_result, tags: result.tags.join(', '),
    confidence: result.confidence, note: item.decision_note ?? '',
    requestMethod: request?.method ?? 'GET', requestUrl: request?.url ?? '{{base_url}}/',
    requestQuery: jsonText(request?.query_params, []),
    requestHeaders: jsonText(request?.headers, []),
    requestCookies: jsonText(request?.cookies, []),
    requestBodyType: request?.body.type ?? 'NONE',
    requestBody: request?.body.type === 'RAW'
      ? String(request.body.content ?? '')
      : request?.body.type === 'NONE' ? '' : jsonText(request?.body.content, {}),
    authType: request?.auth.type ?? 'NONE', authToken: request?.auth.token ?? '',
    authUsername: request?.auth.username ?? '', authPassword: request?.auth.password ?? '',
    authKeyName: request?.auth.key_name ?? '', authKeyValue: request?.auth.key_value ?? '',
    authPlacement: request?.auth.placement ?? 'HEADER', timeoutMs: request?.timeout_ms ?? 30000,
    followRedirects: request?.follow_redirects ?? true,
    retryPolicy: normalizedRetryPolicy(request?.retry_policy),
    preActions: jsonText(result.pre_actions, []), postActions: jsonText(result.post_actions, []),
    cleanup: jsonText(result.cleanup, []), dataSource: jsonText(result.data_source, null),
    extractors: jsonText(result.extractors, []), assertions: jsonText(result.assertions, []),
  })
  reviewEditorStep.value = 'basic'
  const generation = generationForSuggestion(item.id)
  if (generation) void ensureRequirementOptions(generation.project_id)
  detailVisible.value = true
}

function formResult(base = editBaseResult.value): ExtensibleSuggestedCase {
  if (!base) throw new Error('建议内容已失效，请重新打开')
  const result = cloneResult(base)
  const steps = parseJsonArray(editForm.steps, '步骤')
  if (!steps.length) throw new Error('步骤至少需要一项')
  const testData = parseJsonObject(editForm.testData, '测试数据')
  const preActions = parseJsonArray(editForm.preActions, '前置动作')
  const postActions = parseJsonArray(editForm.postActions, '后置动作')
  const extractors = parseJsonArray(editForm.extractors, '提取器')
  const dataSource = parseJsonObjectOrNull(editForm.dataSource, '数据源')
  let request: ApiRequestTemplate | null = null
  let assertions: unknown[] = []
  let cleanup: unknown[] = []
  if (editForm.caseType === 'API') {
    const bodyContent = editForm.requestBodyType === 'NONE'
      ? null
      : editForm.requestBodyType === 'RAW'
        ? editForm.requestBody
        : JSON.parse(editForm.requestBody)
    request = {
      method: editForm.requestMethod,
      url: editForm.requestUrl.trim(),
      query_params: parseJsonArray(editForm.requestQuery, 'Query') as ApiRequestTemplate['query_params'],
      headers: parseJsonArray(editForm.requestHeaders, 'Headers') as ApiRequestTemplate['headers'],
      cookies: parseJsonArray(editForm.requestCookies, 'Cookies') as ApiRequestTemplate['cookies'],
      body: { type: editForm.requestBodyType, content: bodyContent },
      auth: {
        type: editForm.authType, token: editForm.authToken || null,
        username: editForm.authUsername || null, password: editForm.authPassword || null,
        key_name: editForm.authKeyName || null, key_value: editForm.authKeyValue || null,
        placement: editForm.authPlacement,
      },
      timeout_ms: editForm.timeoutMs,
      follow_redirects: editForm.followRedirects,
      retry_policy: normalizedRetryPolicy(editForm.retryPolicy),
    }
    assertions = parseJsonArray(editForm.assertions, '断言')
    cleanup = parseJsonArray(editForm.cleanup, '清理配置')
  }
  Object.assign(result, {
    title: editForm.title,
    case_type: editForm.caseType,
    priority: editForm.priority as SuggestedCase['priority'],
    preconditions: parseLines(editForm.preconditions),
    steps,
    test_data: testData,
    expected_result: editForm.expectedResult,
    tags: editForm.tags.split(',').map((item) => item.trim()).filter(Boolean),
    confidence: editForm.confidence,
    request,
    pre_actions: preActions,
    post_actions: postActions,
    extractors,
    data_source: dataSource,
    assertions,
    cleanup,
  })
  return result
}

const currentEditPreview = computed(() => {
  try {
    return { value: formResult(), error: '' }
  } catch (error) {
    return {
      value: editBaseResult.value,
      error: error instanceof Error ? error.message : '当前编辑值无法解析',
    }
  }
})

function selectReviewEditorStep(step: ReviewEditorStep): void {
  reviewEditorStep.value = step
}

function moveReviewEditorStep(offset: -1 | 1): void {
  const next = reviewEditorSteps.value[reviewEditorStepIndex.value + offset]
  if (next) reviewEditorStep.value = next.key
}

function capturePanelContext(): PanelContext {
  return { requirementId: props.requirementId, contextSequence: panelContextSequence }
}

function panelContextIsCurrent(context: PanelContext): boolean {
  return panelActive
    && panelContextSequence === context.contextSequence
    && props.requirementId === context.requirementId
}

function captureSuggestionOperation(): SuggestionOperation | null {
  const item = selected.value
  if (!item || item.status !== 'DRAFT') return null
  const generation = generationForSuggestion(item.id)
  if (!generation || generation.requirement_id !== props.requirementId) return null
  return {
    suggestionId: item.id,
    requirementId: props.requirementId,
    contextSequence: panelContextSequence,
    projectId: generation.project_id,
    note: editForm.note || undefined,
  }
}

function captureReviewOperation(): ReviewOperation | null {
  const operation = captureSuggestionOperation()
  if (!operation) return null
  return { ...operation, payload: formResult() }
}

function operationContextIsCurrent(operation: SuggestionOperation): boolean {
  const generation = generationForSuggestion(operation.suggestionId)
  return panelContextIsCurrent(operation)
    && generation?.requirement_id === operation.requirementId
    && generation.project_id === operation.projectId
}

function updateOpenSuggestion(operation: ReviewOperation, updated: CaseSuggestion): void {
  if (!operationContextIsCurrent(operation) || selected.value?.id !== operation.suggestionId) return
  selected.value = updated
  editBaseResult.value = cloneResult(updated.human_result ?? operation.payload)
}

async function persistOperation(operation: ReviewOperation): Promise<CaseSuggestion> {
  const updated = await editCaseSuggestion(
    operation.suggestionId, operation.payload, operation.note,
  )
  updateOpenSuggestion(operation, updated)
  return updated
}

async function saveEdit(): Promise<boolean> {
  if (operationBusy.value) return false
  let operation: ReviewOperation | null = null
  try {
    operation = captureReviewOperation()
    if (!operation) return false
    savingSuggestionId.value = operation.suggestionId
    await persistOperation(operation)
    if (!operationContextIsCurrent(operation)) return false
    ElMessage.success('用例建议修改已保存')
    await load()
    return true
  } catch (error) {
    if (!operation || operationContextIsCurrent(operation)) {
      ElMessage.error(retryAwareApiErrorMessage(error, '保存失败，请检查步骤或测试数据 JSON'))
    }
    return false
  } finally {
    if (operation && savingSuggestionId.value === operation.suggestionId) {
      savingSuggestionId.value = null
    }
  }
}

async function decide(action: 'ACCEPT' | 'REJECT'): Promise<void> {
  if (operationBusy.value) return
  let operation: SuggestionOperation | null = null
  try {
    operation = action === 'ACCEPT' ? captureReviewOperation() : captureSuggestionOperation()
    if (!operation) return
    if (action === 'ACCEPT') {
      const reviewOperation = operation as ReviewOperation
      const retryError = v1RetryPolicyError(reviewOperation.payload)
      if (retryError) { ElMessage.error(retryError); return }
    }
    decidingSuggestionId.value = operation.suggestionId
    if (action === 'ACCEPT') {
      await persistOperation(operation as ReviewOperation)
      if (!operationContextIsCurrent(operation)) {
        return
      }
    }
    await decideCaseSuggestion(operation.suggestionId, action, operation.note)
    if (!operationContextIsCurrent(operation)) return
    if (selected.value?.id === operation.suggestionId) detailVisible.value = false
    ElMessage.success(action === 'ACCEPT' ? '已生成正式测试用例 V1' : '建议已拒绝')
    await load()
  } catch (error) {
    if (!operation || operationContextIsCurrent(operation)) {
      ElMessage.error(retryAwareApiErrorMessage(error, '该建议可能已经完成决策'))
    }
  } finally {
    if (operation && decidingSuggestionId.value === operation.suggestionId) {
      decidingSuggestionId.value = null
    }
  }
}

async function bulk(action: 'ACCEPT' | 'REJECT'): Promise<void> {
  if (operationBusy.value) return
  if (!checkedIds.value.length) { ElMessage.info('请先勾选待审核建议'); return }
  const context = capturePanelContext()
  const ids = [...checkedIds.value]
  if (action === 'ACCEPT') {
    const invalidIds = ids.filter((id) => {
      const item = draftSuggestions.value.find((suggestion) => suggestion.id === id)
      return item ? Boolean(v1RetryPolicyError(item.human_result ?? item.structured_result)) : true
    })
    if (invalidIds.length) {
      ElMessage.error(`${API_STEP_RETRY_LIMIT_ERROR_MESSAGE}；未发送批量请求：建议编号 ${invalidIds.join('、')}`)
      return
    }
  }
  bulkSaving.value = true
  try {
    await bulkDecideCaseSuggestions(ids, action)
    if (!panelContextIsCurrent(context)) return
    ElMessage.success(action === 'ACCEPT' ? '已批量生成正式用例' : '已批量拒绝建议')
    checkedIds.value = []
    await load()
  } catch (error) {
    if (panelContextIsCurrent(context)) {
      ElMessage.error(retryAwareApiErrorMessage(error, '批量审核失败，部分建议可能已决策'))
    }
  } finally {
    bulkSaving.value = false
  }
}

async function openBulkEdit(): Promise<void> {
  if (!checkedIds.value.length) { ElMessage.info('请先勾选待审核建议'); return }
  const context = capturePanelContext()
  const generation = selectedGeneration.value
  if (!generation) return
  try {
    requirementOptions.value = await getRequirementTree(generation.project_id)
    if (!panelContextIsCurrent(context)) return
    Object.assign(bulkEditForm, {
      applyPriority: false, priority: 'P1', applyTags: false, tags: '',
      applyRequirements: false, requirementIds: [],
    })
    bulkEditVisible.value = true
  } catch { ElMessage.error('需求列表加载失败') }
}

async function submitBulkEdit(): Promise<void> {
  if (!bulkEditForm.applyPriority && !bulkEditForm.applyTags
    && !bulkEditForm.applyRequirements) {
    ElMessage.info('请选择至少一项批量修改'); return
  }
  const context = capturePanelContext()
  const ids = [...checkedIds.value]
  const payload: Parameters<typeof bulkEditCaseSuggestions>[1] = {}
  if (bulkEditForm.applyPriority) payload.priority = bulkEditForm.priority
  if (bulkEditForm.applyTags) {
    payload.tags = bulkEditForm.tags.split(',').map((item) => item.trim()).filter(Boolean)
  }
  if (bulkEditForm.applyRequirements) payload.requirement_ids = bulkEditForm.requirementIds
  bulkSaving.value = true
  try {
    await bulkEditCaseSuggestions(ids, payload)
    if (!panelContextIsCurrent(context)) return
    bulkEditVisible.value = false
    ElMessage.success('批量优先级、标签与需求关联已保存')
    await load()
  } catch (error) {
    if (panelContextIsCurrent(context)) {
      ElMessage.error(retryAwareApiErrorMessage(error, '批量修改失败'))
    }
  } finally { bulkSaving.value = false }
}

async function bulkDelete(): Promise<void> {
  if (!checkedIds.value.length) { ElMessage.info('请先勾选待审核建议'); return }
  try {
    await ElMessageBox.confirm(
      `确定删除 ${checkedIds.value.length} 条待审核草稿？AI 生成批次和调用审计仍会保留。`,
      '批量删除草稿', { type: 'warning' },
    )
  } catch { return }
  const context = capturePanelContext()
  const ids = [...checkedIds.value]
  bulkSaving.value = true
  try {
    const count = await bulkDeleteCaseSuggestions(ids)
    if (!panelContextIsCurrent(context)) return
    checkedIds.value = []
    ElMessage.success(`已删除 ${count} 条建议草稿`)
    await load()
  } catch (error) {
    if (panelContextIsCurrent(context)) {
      ElMessage.error(retryAwareApiErrorMessage(error, '批量删除失败'))
    }
  } finally { bulkSaving.value = false }
}

watch(() => props.requirementId, () => {
  panelContextSequence += 1
  loadSequence += 1
  checkedIds.value = []
  selected.value = null
  editBaseResult.value = null
  detailVisible.value = false
  resultVisible.value = false
  resultFullscreen.value = false
  resultGenerationId.value = null
  bulkEditVisible.value = false
  generateVisible.value = false
  designTask.value = null
  designPlan.value = null
  void load()
})
watch(generateVisible, (visible) => {
  if (!visible && designPollingTimer !== undefined) {
    window.clearTimeout(designPollingTimer)
    designPollingTimer = undefined
  }
})
watch(() => editForm.caseType, () => {
  if (!reviewEditorSteps.value.some((item) => item.key === reviewEditorStep.value)) {
    reviewEditorStep.value = 'basic'
  }
})
onBeforeUnmount(() => {
  panelActive = false
  panelContextSequence += 1
  loadSequence += 1
  if (taskPollingTimer !== undefined) window.clearTimeout(taskPollingTimer)
  if (designPollingTimer !== undefined) window.clearTimeout(designPollingTimer)
})
onMounted(async () => {
  await load()
  if (props.autoOpen) await openGenerate()
})
</script>

<template>
  <div class="case-suggestion-panel">
    <section class="ai-feature-card ai-design-card">
      <div class="ai-feature-icon"><el-icon><MagicStick /></el-icon></div>
      <div class="ai-feature-copy"><strong>AI 测试设计</strong><span>从需求中提取检查点，推荐关联 API，再生成可审核的测试用例。</span><small>当前需求已有 {{ coverageCount }} 条正式用例</small></div>
      <div class="ai-feature-actions"><el-button @click="emit('openDesignRecords')">本需求设计记录</el-button><el-button type="primary" :icon="MagicStick" :disabled="hasActiveGenerationTask" @click="openGenerate()">开始 AI 设计</el-button></div>
    </section>
    <section v-if="generationTasks.length" class="generation-task-list">
      <header><div><strong>AI 生成记录</strong><span>每条记录对应一次独立生成，结果、覆盖率和审核状态互不混合。</span></div><el-button link :icon="Refresh" @click="load">刷新</el-button></header>
      <article v-for="task in pagedGenerationTasks" :key="task.id">
        <div class="task-summary"><strong>用例生成</strong><span>提交于 {{ formatTaskTime(task.created_at) }} · 已确认 {{ task.selected_api_definition_ids.length }} 个 API<template v-if="task.status === 'SUCCEEDED'"> · 已生成 {{ generationSuggestionCount(task) }} 条</template></span><small v-if="task.error_message">{{ task.error_message }}</small></div>
        <div class="task-actions"><el-tag :type="taskStatusType[task.status]" effect="light">{{ taskStatusLabel[task.status] }}</el-tag><el-button v-if="task.status === 'SUCCEEDED'" link type="primary" @click="openTaskResult(task)">查看结果</el-button><el-button v-if="task.status === 'FAILED'" link type="primary" @click="retryGenerationTask(task)">重新生成</el-button></div>
      </article>
      <el-pagination v-if="generationTaskTotal" class="records-pagination compact" small layout="total, sizes, prev, next" :current-page="generationTaskPage" :page-size="generationTaskPageSize" :page-sizes="[...RECORD_PAGE_SIZES]" :total="generationTaskTotal" @current-change="changeGenerationTaskPage" @size-change="changeGenerationTaskPageSize" />
    </section>
    <el-empty v-if="!generationTasks.length && !hasActiveGenerationTask" description="暂无 AI 生成记录" :image-size="70" />

    <el-dialog v-model="resultVisible" class="case-result-dialog" :fullscreen="resultFullscreen" width="min(1180px, 96vw)" top="3vh" append-to-body destroy-on-close @closed="closeResult">
      <template #header>
        <div class="result-dialog-title">
          <div><strong>AI 用例生成结果</strong><span v-if="resultGeneration">{{ resultGeneration.requirement_code }} · {{ resultGeneration.requirement_title }} · {{ formatTaskTime(resultGeneration.created_at) }}</span></div>
          <el-button :icon="FullScreen" @click="resultFullscreen = !resultFullscreen">{{ resultFullscreen ? '退出全屏' : '全屏查看' }}</el-button>
        </div>
      </template>
      <template v-if="resultGeneration">
        <section class="result-summary-grid">
          <div><span>生成总数</span><strong>{{ resultSuggestions.length }}</strong></div>
          <div><span>待审核</span><strong>{{ draftSuggestions.length }}</strong></div>
          <div><span>正式用例</span><strong>{{ acceptedSuggestionCount }}</strong></div>
          <div><span>已拒绝</span><strong>{{ rejectedSuggestionCount }}</strong></div>
          <div><span>检查点覆盖</span><strong>{{ resultCoveragePercent }}%</strong></div>
          <div><span>本轮 API</span><strong>{{ resultGeneration.selected_api_definition_ids.length }}</strong></div>
        </section>
        <el-alert
          v-if="resultGeneration.coverage_plan.intent_count !== undefined"
          class="compiler-summary"
          type="success"
          :closable="false"
          show-icon
          :title="`AI 提出 ${resultGeneration.coverage_plan.intent_count} 条测试意图，平台已编译 ${resultGeneration.coverage_plan.compiled_count ?? resultSuggestions.length} 条可执行用例${resultGeneration.coverage_plan.compile_skipped_count ? `，${resultGeneration.coverage_plan.compile_skipped_count} 条契约不完整已转入缺口` : ''}`"
        />
        <div class="result-run-meta">
          <span>模型：{{ resultGeneration.actual_model }}</span>
          <span>生成时间：{{ formatTaskTime(resultGeneration.created_at) }}</span>
          <span>规则：{{ resultGeneration.prompt_name }} · V{{ resultGeneration.prompt_version_no }}</span>
          <el-button
            v-if="acceptedSuggestionCount"
            class="recompile-cases-button"
            type="primary"
            plain
            :loading="recompiling"
            :disabled="operationBusy && !recompiling"
            @click="recompileSavedCases"
          >用最新平台规则修复已保存用例</el-button>
        </div>
        <el-tabs v-model="resultTab" class="result-tabs">
          <el-tab-pane :label="`生成用例（${resultSuggestions.length}）`" name="CASES">
            <div class="result-list-intro"><div><strong>审核并保存本轮用例</strong><span>点击任意用例可查看完整内容；审核详情内可按上一条、下一条连续处理。</span></div></div>
            <div v-if="draftSuggestions.length" class="bulk-bar"><el-checkbox :model-value="allDraftsChecked" :indeterminate="someDraftsChecked" :disabled="operationBusy" @change="toggleAllDrafts">全选本轮草稿（{{ draftSuggestions.length }}）</el-checkbox><span v-if="checkedIds.length">已选 {{ checkedIds.length }} 条</span><el-button link :disabled="operationBusy || !checkedIds.length" @click="openBulkEdit">批量设置公共属性</el-button><el-button link type="danger" :loading="bulkSaving" :disabled="operationBusy || !checkedIds.length" @click="bulkDelete">批量删除</el-button><el-button link type="danger" :loading="bulkSaving" :disabled="operationBusy || !checkedIds.length" @click="bulk('REJECT')">批量拒绝</el-button><el-button link type="success" :icon="Select" :loading="bulkSaving" :disabled="operationBusy || !checkedIds.length" @click="bulk('ACCEPT')">批量保存为正式用例</el-button></div>
            <el-checkbox-group v-model="checkedIds" class="result-case-list">
              <article v-for="item in pagedSuggestions" :key="item.id" class="case-suggestion-card">
                <el-checkbox v-if="item.status === 'DRAFT'" :value="item.id" :disabled="operationBusy" />
                <button class="suggestion-card-button" @click="openDetail(item)">
                  <div class="suggestion-heading"><strong class="suggestion-title"><small>#{{ item.sequence_no }}</small>{{ effectiveSuggestion(item).title }}</strong><el-tag class="suggestion-status" :type="statusType[item.status]" size="small">{{ statusLabel[item.status] }}</el-tag></div>
                  <div class="suggestion-meta"><span>{{ caseTypeLabel[effectiveSuggestion(item).case_type] }}</span><span>{{ effectiveSuggestion(item).priority }}</span><span>质量评分 {{ assessCaseQuality(item).score }} 分</span><span>{{ effectiveSuggestion(item).steps.length }} 个步骤</span></div>
                  <div class="suggestion-preview">{{ effectiveSuggestion(item).expected_result }}</div>
                  <div class="suggestion-links"><span v-if="item.linked_requirement_ids.length">另关联 {{ item.linked_requirement_ids.length }} 条需求</span><span v-if="item.test_case_id">已生成正式用例</span><span class="view-detail-link">查看详情 →</span></div>
                </button>
              </article>
            </el-checkbox-group>
            <el-pagination v-if="suggestionTotal" class="result-pagination" background layout="total, sizes, prev, pager, next" :current-page="suggestionPage" :page-size="suggestionPageSize" :page-sizes="[...RECORD_PAGE_SIZES]" :total="suggestionTotal" @current-change="changeSuggestionPage" @size-change="changeSuggestionPageSize" />
            <el-empty v-if="!resultSuggestions.length" description="本轮建议已全部删除" :image-size="70" />
          </el-tab-pane>
          <el-tab-pane :label="`覆盖与缺口（${resultGeneration.coverage_plan.check_points.length}）`" name="COVERAGE">
            <section v-if="resultGeneration.coverage_plan.check_points.length" class="coverage-overview">
              <header><div><strong>本轮覆盖情况</strong><span>按需求检查点与实际生成用例标签确定，不是 AI 自评分</span></div><strong>{{ resultCoveragePercent }}%</strong></header>
              <el-progress :percentage="resultCoveragePercent" :stroke-width="10" />
              <div class="coverage-points">
                <span v-for="point in resultGeneration.coverage_plan.check_points" :key="point.key" :class="{ covered: resultCoveredKeys.has(point.key) }"><el-tag :type="resultCoveredKeys.has(point.key) ? 'success' : 'warning'" size="small">{{ resultCoveredKeys.has(point.key) ? '已覆盖' : '待补充' }}</el-tag><strong>{{ point.key }}</strong>{{ point.title }}</span>
              </div>
            </section>
            <section class="capability-gaps">
              <header><strong>能力缺口与风险</strong><span>这些项目会随本轮设计记录保留，不阻止保存能够正常执行的用例。</span></header>
              <el-alert v-for="gap in resultGeneration.coverage_plan.gaps" :key="gap" :title="gap" type="warning" :closable="false" show-icon />
              <el-empty v-if="!resultGeneration.coverage_plan.gaps.length" description="本轮没有识别到额外能力缺口" :image-size="64" />
            </section>
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-dialog>
    <el-dialog v-model="generateVisible" title="AI 测试设计" width="980px" destroy-on-close append-to-body>
      <div v-loading="designLoading" class="test-design-dialog">
        <el-steps :active="designStep" finish-status="success" align-center>
          <el-step title="确认检查点" description="明确要验证什么" />
          <el-step title="确认接口闭环" description="选择怎么验证" />
          <el-step title="创建任务" description="后台生成并审核" />
        </el-steps>

        <section v-if="designStep === 0 && !designPlan && designTask?.status !== 'FAILED'" class="design-step-content design-running-state">
          <el-icon class="is-loading" :size="34"><MagicStick /></el-icon>
          <strong>AI 正在阅读需求并分析相关 API</strong>
          <span>系统会校验模型推荐的接口编号，并自动补充必要的登录、结果验证或数据清理接口。</span>
          <small>这是一条后台记录，你可以关闭弹窗；再次打开时会继续显示结果。</small>
        </section>

        <section v-if="designStep === 0 && !designPlan && designTask?.status === 'FAILED'" class="design-step-content design-running-state">
          <el-alert :title="designTask.error_message || 'AI 测试设计失败'" type="error" :closable="false" show-icon />
          <el-button type="primary" plain @click="openGenerate(true)">重新发起设计</el-button>
        </section>

        <section v-if="designStep === 0 && designPlan" class="design-step-content">
          <div class="design-section-title"><div><strong>AI 提取的验收检查点</strong><span>所选范围共 {{ designPlan.scope_requirement_total }} 条需求，本轮 API 设计纳入 {{ designPlan.scope_requirement_count }} 条；生成后将逐项统计覆盖。</span></div><div class="design-title-actions"><el-tag :type="designPlan.source === 'AI' ? 'success' : 'warning'">{{ designPlan.source === 'AI' ? (designPlan.platform_completed_checkpoint_count ? 'AI + 平台补全' : 'AI 推荐') : '规则候选' }}</el-tag><el-tag v-if="designPlan.existing_case_count" type="info">已有 {{ designPlan.existing_case_count }} 条正式用例</el-tag><el-button link type="primary" @click="openGenerate(true)">重新分析</el-button></div></div>
          <el-alert v-if="designPlan.source === 'RULE_FALLBACK'" :title="designTask?.error_message || 'AI 输出未通过结构或覆盖门禁，平台已生成规则候选，请人工核对。'" type="warning" :closable="false" show-icon />
          <el-alert v-if="designPlan.platform_completed_checkpoint_count" :title="`模型结果可用；平台又补全了 ${designPlan.platform_completed_checkpoint_count} 个遗漏检查点${designPlan.ignored_ai_reference_count ? `，并忽略 ${designPlan.ignored_ai_reference_count} 个无效引用` : ''}`" type="success" :closable="false" show-icon />
          <section v-if="designPlan.excluded_requirements.length" class="design-routing-summary">
            <header><strong>已自动分流 {{ designPlan.excluded_requirements.length }} 条非 API 需求</strong><span>这些内容不会再导致 API 设计失败，仍保留在对应验证流程中。</span></header>
            <div><el-tag v-for="item in designPlan.excluded_requirements" :key="item.requirement_code" type="info" effect="plain">{{ item.requirement_code }} · {{ item.requirement_title }}：{{ item.reason }}</el-tag></div>
          </section>
          <div class="check-point-list">
            <article v-for="point in pagedDesignCheckPoints" :key="point.key"><el-tag size="small">{{ point.key }}</el-tag><div><strong>{{ point.title }}</strong><span>{{ point.source }}</span></div></article>
          </div>
          <el-pagination v-if="designCheckPointTotal" class="design-check-point-pagination" background layout="total, sizes, prev, pager, next" :current-page="designCheckPointPage" :page-size="designCheckPointPageSize" :page-sizes="[...RECORD_PAGE_SIZES]" :total="designCheckPointTotal" @current-change="changeDesignCheckPointPage" @size-change="changeDesignCheckPointPageSize" />
          <el-alert v-if="designPlan.existing_case_count" title="本轮仍会生成建议；审核时可根据最新覆盖情况只接受补充项，避免重复沉淀正式用例。" type="warning" :closable="false" show-icon />
        </section>

        <section v-if="designStep === 1 && designPlan" class="design-step-content">
          <div class="design-section-title"><div><strong>AI 推荐的 API 闭环</strong><span>模型先推荐，平台逐项校验检查点覆盖，并补齐缺失接口和前后置依赖。</span></div><span>已选 {{ selectedApiIds.length }} 个</span></div>
          <el-checkbox-group v-model="selectedApiIds" class="recommended-api-list">
            <el-checkbox v-for="api in pagedDesignRecommendedApis" :key="api.api_definition_id" :value="api.api_definition_id" :disabled="api.required" border>
              <div class="recommended-api-main"><div><el-tag size="small" effect="dark">{{ api.method }}</el-tag><code>{{ api.path }}</code><el-tag size="small" type="info">{{ api.role }}</el-tag><el-tag v-if="api.required" size="small" type="warning">核心</el-tag></div><strong>{{ api.name }}</strong><span>{{ api.reason }}</span><small v-if="api.check_point_keys.length">对应 {{ api.check_point_keys.join('、') }}</small></div>
            </el-checkbox>
          </el-checkbox-group>
          <el-pagination v-if="designRecommendedApiTotal" class="design-check-point-pagination" background layout="total, sizes, prev, pager, next" :current-page="designRecommendedApiPage" :page-size="designRecommendedApiPageSize" :page-sizes="[...RECORD_PAGE_SIZES]" :total="designRecommendedApiTotal" @current-change="changeDesignRecommendedApiPage" @size-change="changeDesignRecommendedApiPageSize" />
          <el-empty v-if="!designPlan.recommended_apis.length" description="没有可推荐的 API，请先在 API 定义中导入 OpenAPI" :image-size="70" />
          <el-alert v-for="gap in designPlan.gaps" :key="gap" :title="gap" type="warning" :closable="false" show-icon />
        </section>

        <section v-if="designStep === 2" class="design-step-content">
          <el-alert :title="`将使用 ${selectedApiIds.length} 个已确认 API 覆盖 ${designPlan?.check_points.length ?? 0} 个检查点`" type="success" :closable="false" show-icon />
          <el-form label-position="top">
            <el-form-item label="生成规则"><el-select v-model="generateForm.promptId"><el-option v-for="prompt in prompts" :key="prompt.id" :label="promptOptionLabel(prompt)" :value="prompt.id" /></el-select></el-form-item>
            <el-form-item label="本次补充要求（可选）"><el-input v-model="generateForm.instructions" type="textarea" :rows="4" placeholder="例如：重点覆盖鉴权失败、边界值和数据清理" /></el-form-item>
          </el-form>
          <el-alert title="创建后弹窗立即关闭，AI 在后台继续执行。你可以离开页面，稍后从生成记录查看结果和覆盖缺口。" type="info" :closable="false" />
        </section>
      </div>
      <template #footer>
        <el-button :disabled="generating" @click="generateVisible = false">关闭，后台继续</el-button>
        <el-button v-if="designStep > 0" :disabled="generating" @click="designStep -= 1">上一步</el-button>
        <el-button v-if="designStep < 2" type="primary" :disabled="!designPlan || (designStep === 1 && !selectedApiIds.length)" @click="nextDesignStep">下一步</el-button>
        <el-button v-else type="primary" :loading="generating" @click="generate">{{ generating ? '正在创建任务…' : '创建后台生成任务' }}</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="bulkEditVisible" title="批量设置公共属性" width="680px">
      <el-form label-position="top">
        <el-alert :title="`以下选中属性会同时应用到 ${checkedIds.length} 条草稿`" description="不会修改各用例的请求、步骤、测试数据和断言；这些内容请进入单条详情编辑。" type="warning" :closable="false" show-icon class="bulk-edit-warning" />
        <el-form-item><el-checkbox v-model="bulkEditForm.applyPriority">统一设置优先级</el-checkbox><el-select v-model="bulkEditForm.priority" :disabled="!bulkEditForm.applyPriority"><el-option value="P0" /><el-option value="P1" /><el-option value="P2" /><el-option value="P3" /></el-select></el-form-item>
        <el-form-item><el-checkbox v-model="bulkEditForm.applyTags">统一替换标签</el-checkbox><el-input v-model="bulkEditForm.tags" :disabled="!bulkEditForm.applyTags" placeholder="smoke, regression；留空可清空" /></el-form-item>
        <el-form-item><el-checkbox v-model="bulkEditForm.applyRequirements">统一关联额外需求</el-checkbox><el-select v-model="bulkEditForm.requirementIds" multiple filterable collapse-tags :disabled="!bulkEditForm.applyRequirements" placeholder="选择同项目的有效需求"><el-option v-for="item in flatRequirementOptions" :key="item.id" :label="`${item.code} · ${item.title}`" :value="item.id" :disabled="item.id === props.requirementId || item.status !== 'ACTIVE' || !item.current_version_id" /></el-select></el-form-item>
        <el-alert title="发起设计的需求关联始终保留。" type="info" :closable="false" />
      </el-form>
      <template #footer><el-button @click="bulkEditVisible = false">取消</el-button><el-button type="primary" :loading="bulkSaving" @click="submitBulkEdit">应用到 {{ checkedIds.length }} 条草稿</el-button></template>
    </el-dialog>
    <el-drawer v-model="detailVisible" title="AI 用例建议审核" size="82%">
      <template v-if="selected">
        <div class="review-detail-header">
          <div>
            <el-tag :type="statusType[selected.status]">{{ statusLabel[selected.status] }}</el-tag>
            <span>AI 建议<template v-if="selected.test_case_id"> · 已生成正式用例</template></span>
          </div>
          <div class="review-detail-actions">
            <span v-if="selectedResultIndex >= 0" class="review-sequence">第 {{ selectedResultIndex + 1 }} / {{ resultSuggestions.length }} 条</span>
            <el-button :disabled="selectedResultIndex <= 0 || operationBusy" @click="openAdjacentSuggestion(-1)">上一条</el-button>
            <el-button :disabled="selectedResultIndex < 0 || selectedResultIndex >= resultSuggestions.length - 1 || operationBusy" @click="openAdjacentSuggestion(1)">下一条</el-button>
            <template v-if="selected.status === 'DRAFT'">
              <el-button :loading="savingSuggestionId === selected.id" :disabled="operationBusy" @click="saveEdit">保存编辑</el-button>
              <el-button type="danger" plain :loading="decidingSuggestionId === selected.id" :disabled="operationBusy" @click="decide('REJECT')">拒绝</el-button>
              <el-button type="success" :loading="decidingSuggestionId === selected.id" :disabled="operationBusy || retryNeedsCorrection" @click="decide('ACCEPT')">保存为正式用例</el-button>
            </template>
          </div>
        </div>

        <template v-if="currentGeneration">
          <el-descriptions :column="2" border class="generation-business-summary">
            <el-descriptions-item label="来源需求">
              {{ currentGeneration.requirement_code }} · {{ currentGeneration.requirement_title }}
            </el-descriptions-item>
            <el-descriptions-item label="调用模型">{{ currentGeneration.actual_model }}</el-descriptions-item>
            <el-descriptions-item label="生成规则">
              {{ currentGeneration.prompt_name }} · V{{ currentGeneration.prompt_version_no }}
            </el-descriptions-item>
            <el-descriptions-item label="处理情况">
              <el-tag v-if="!currentGeneration.fallback_used && !currentGeneration.repair_used" size="small" type="success">直接生成成功</el-tag>
              <el-tag v-if="currentGeneration.fallback_used" size="small" type="warning">使用备用模型</el-tag>
              <el-tag v-if="currentGeneration.repair_used" size="small" type="warning">执行输出修复</el-tag>
            </el-descriptions-item>
          </el-descriptions>

          <section class="suggestion-requirement-scope" v-loading="requirementOptionsLoading">
            <div><strong>本用例对应需求</strong><span>由用例的 coverage 标签与本轮检查点确定</span></div>
            <div v-if="selectedRequirementRefs.length" class="requirement-ref-list">
              <el-tag v-for="item in selectedRequirementRefs" :key="item.key" type="primary" effect="plain">{{ item.code }} · {{ item.title }}</el-tag>
            </div>
            <el-alert v-else title="该用例没有有效的 coverage 检查点标记，暂时只能确定它属于本轮设计范围。" type="warning" :closable="false" show-icon />
            <div v-if="selectedCoveragePoints.length" class="coverage-point-list">
              <span v-for="point in selectedCoveragePoints" :key="point.key"><strong>{{ point.key }}</strong>{{ point.title }}</span>
            </div>
          </section>

          <section v-if="selectedQuality" class="suggestion-quality-panel">
            <header>
              <div>
                <span>平台质量评分</span>
                <strong>{{ selectedQuality.score }}<small>/100</small></strong>
              </div>
              <p>{{ selectedQuality.passedCount }}/{{ selectedQuality.checks.length }} 项检查通过。评分来自确定性规则，不代表用例正确概率。</p>
            </header>
            <div class="quality-checks">
              <div v-for="check in selectedQuality.checks" :key="check.label" :class="{ passed: check.passed }">
                <el-tag :type="check.passed ? 'success' : 'info'" size="small">{{ check.passed ? '通过' : '待完善' }}</el-tag>
                <span><strong>{{ check.label }}</strong><small>{{ check.detail }} · {{ check.weight }} 分</small></span>
              </div>
            </div>
            <div class="ai-self-assessment">
              <span>AI 自评</span>
              <strong v-if="selectedAiConfidence !== undefined">{{ Math.round(selectedAiConfidence * 100) }}%</strong>
              <strong v-else>未评估</strong>
              <small>{{ selectedAiConfidence !== undefined ? '由模型明确返回，仅供人工审核参考。' : '模型未明确返回自评分数，平台不会自动补成 80%。' }}</small>
            </div>
          </section>

          <el-alert
            v-if="effectiveSuggestion(selected).case_type === 'API' && !effectiveSuggestion(selected).request"
            class="legacy-incomplete-alert"
            title="这条建议缺少可执行请求配置"
            description="它是修复前生成的历史结果，因此仍会按实际内容扣分。请关闭审核窗口后重新生成；新任务会使用项目 API 定义生成请求方法、地址和断言。"
            type="warning"
            :closable="false"
            show-icon
          />

          <el-collapse class="generation-audit-sections">
            <el-collapse-item title="生成详情" name="generation-detail">
              <el-descriptions :column="2" border>
                <el-descriptions-item label="需求版本">V{{ currentGeneration.requirement_version_no }}</el-descriptions-item>
                <el-descriptions-item label="输出结构">
                  <template v-if="currentGeneration.output_schema_name">
                    {{ currentGeneration.output_schema_name }} · V{{ currentGeneration.output_schema_version_no }}
                  </template>
                  <template v-else>未绑定输出结构</template>
                </el-descriptions-item>
                <el-descriptions-item label="备用模型">{{ currentGeneration.fallback_used ? '已使用' : '未使用' }}</el-descriptions-item>
                <el-descriptions-item label="输出修复">{{ currentGeneration.repair_used ? '已执行' : '未执行' }}</el-descriptions-item>
                <el-descriptions-item label="生成时间" :span="2">{{ formatTaskTime(currentGeneration.created_at) }}</el-descriptions-item>
              </el-descriptions>
            </el-collapse-item>
            <el-collapse-item title="技术追踪信息" name="technical-trace">
              <div class="technical-trace-header">
                <span>仅供开发排障和审计查询使用。</span>
                <el-button link type="primary" @click="copyTechnicalTrace">复制追踪信息</el-button>
              </div>
              <dl class="technical-trace-grid">
                <div><dt>需求记录 ID</dt><dd>{{ currentGeneration.requirement_id }}</dd></div>
                <div><dt>需求版本记录 ID</dt><dd>{{ currentGeneration.requirement_version_id }}</dd></div>
                <div><dt>AI 调用记录 ID</dt><dd>{{ currentGeneration.ai_call_id }}</dd></div>
                <div><dt>提示词版本记录 ID</dt><dd>{{ currentGeneration.prompt_version_id }}</dd></div>
                <div><dt>输出结构记录 ID</dt><dd>{{ currentGeneration.output_schema_id ?? '未绑定' }}</dd></div>
              </dl>
            </el-collapse-item>
            <el-collapse-item title="AI 原始响应（已服务端脱敏）" name="raw-response">
              <pre class="raw-response">{{ currentGeneration.raw_response }}</pre>
            </el-collapse-item>
          </el-collapse>
        </template>

        <el-form label-position="top" class="review-case-editor">
          <div class="review-editor-shell">
            <nav class="review-editor-nav" aria-label="建议用例配置步骤">
              <div class="review-editor-nav-title">配置步骤</div>
              <button v-for="(step, index) in reviewEditorSteps" :key="step.key" type="button" class="review-editor-nav-item" :class="{ active: reviewEditorStep === step.key }" @click="selectReviewEditorStep(step.key)">
                <span>{{ index + 1 }}</span><div><strong>{{ step.title }}</strong><small>{{ step.description }}</small></div>
              </button>
            </nav>
            <main class="review-editor-workspace">
              <header class="review-editor-heading">
                <div><span>步骤 {{ reviewEditorStepIndex + 1 }} / {{ reviewEditorSteps.length }}</span><h3>{{ reviewEditorStepMeta?.title }}</h3><p>{{ reviewEditorStepMeta?.description }}</p></div>
                <el-tag effect="plain">{{ editForm.caseType }} Case</el-tag>
              </header>

              <section v-show="reviewEditorStep === 'basic'" class="review-editor-panel">
                <el-form-item label="用例名称"><el-input v-model="editForm.title" maxlength="255" :disabled="reviewEditorLocked" /></el-form-item>
                <div class="review-form-grid">
                  <el-form-item label="类型"><el-select v-model="editForm.caseType" :disabled="reviewEditorLocked"><el-option label="API" value="API" /><el-option label="Web" value="WEB" /><el-option label="手工" value="MANUAL" /></el-select></el-form-item>
                  <el-form-item label="优先级"><el-select v-model="editForm.priority" :disabled="reviewEditorLocked"><el-option value="P0" /><el-option value="P1" /><el-option value="P2" /><el-option value="P3" /></el-select></el-form-item>
                  <el-form-item label="人工置信度"><el-input-number v-model="editForm.confidence" :min="0" :max="1" :step="0.05" :disabled="reviewEditorLocked" /></el-form-item>
                </div>
                <el-form-item label="前置条件（每行一条）"><el-input v-model="editForm.preconditions" type="textarea" :rows="4" :disabled="reviewEditorLocked" /></el-form-item>
                <el-form-item label="步骤 JSON"><el-input v-model="editForm.steps" type="textarea" :rows="8" class="code-input" :disabled="reviewEditorLocked" /></el-form-item>
                <el-form-item label="测试数据 JSON"><el-input v-model="editForm.testData" type="textarea" :rows="5" class="code-input" :disabled="reviewEditorLocked" /></el-form-item>
              </section>

              <section v-if="editForm.caseType === 'API'" v-show="reviewEditorStep === 'request'" class="review-editor-panel">
                <div class="review-request-line"><el-form-item label="方法"><el-select v-model="editForm.requestMethod" :disabled="reviewEditorLocked"><el-option v-for="method in ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']" :key="method" :value="method" /></el-select></el-form-item><el-form-item label="URL（支持 Runtime Context 变量）"><el-input v-model="editForm.requestUrl" :disabled="reviewEditorLocked" /></el-form-item></div>
                <el-tabs type="border-card">
                  <el-tab-pane label="Query"><el-input v-model="editForm.requestQuery" type="textarea" :rows="6" class="code-input" :disabled="reviewEditorLocked" /></el-tab-pane>
                  <el-tab-pane label="Headers"><el-input v-model="editForm.requestHeaders" type="textarea" :rows="6" class="code-input" :disabled="reviewEditorLocked" /></el-tab-pane>
                  <el-tab-pane label="Cookies"><el-input v-model="editForm.requestCookies" type="textarea" :rows="6" class="code-input" :disabled="reviewEditorLocked" /></el-tab-pane>
                  <el-tab-pane label="Body"><div class="review-body-editor"><el-select v-model="editForm.requestBodyType" :disabled="reviewEditorLocked"><el-option v-for="bodyType in ['NONE', 'JSON', 'FORM_URLENCODED', 'MULTIPART', 'RAW']" :key="bodyType" :value="bodyType" /></el-select><el-input v-if="editForm.requestBodyType !== 'NONE'" v-model="editForm.requestBody" type="textarea" :rows="7" class="code-input" :disabled="reviewEditorLocked" /></div></el-tab-pane>
                  <el-tab-pane label="认证"><div class="review-auth-editor"><el-select v-model="editForm.authType" :disabled="reviewEditorLocked"><el-option label="无认证" value="NONE" /><el-option label="Bearer Token" value="BEARER" /><el-option label="基础认证" value="BASIC" /><el-option label="API Key" value="API_KEY" /></el-select><el-input v-if="editForm.authType === 'BEARER'" v-model="editForm.authToken" placeholder="Token 或 {{secret.token}}" :disabled="reviewEditorLocked" /><template v-if="editForm.authType === 'BASIC'"><el-input v-model="editForm.authUsername" placeholder="用户名" :disabled="reviewEditorLocked" /><el-input v-model="editForm.authPassword" show-password placeholder="密码或密钥模板" :disabled="reviewEditorLocked" /></template><template v-if="editForm.authType === 'API_KEY'"><el-input v-model="editForm.authKeyName" placeholder="参数名称" :disabled="reviewEditorLocked" /><el-input v-model="editForm.authKeyValue" show-password placeholder="值或密钥模板" :disabled="reviewEditorLocked" /><el-radio-group v-model="editForm.authPlacement" :disabled="reviewEditorLocked"><el-radio-button value="HEADER">请求头</el-radio-button><el-radio-button value="QUERY">查询参数</el-radio-button></el-radio-group></template></div></el-tab-pane>
                </el-tabs>
                <div class="review-request-settings"><el-form-item label="超时（毫秒）"><el-input-number v-model="editForm.timeoutMs" :min="100" :max="120000" :step="1000" :disabled="reviewEditorLocked" /></el-form-item><el-checkbox v-model="editForm.followRedirects" :disabled="reviewEditorLocked">跟随重定向</el-checkbox></div>
                <el-divider content-position="left">失败重试</el-divider>
                <el-alert title="V1 最大额外重试次数只允许 0 或 1。" :type="retryNeedsCorrection ? 'error' : 'warning'" :closable="false" show-icon />
                <div class="review-form-grid">
                  <el-form-item label="最大额外重试次数" :error="retryNeedsCorrection ? API_STEP_RETRY_LIMIT_ERROR_MESSAGE : ''"><el-select v-model="editForm.retryPolicy.max_retries" :disabled="reviewEditorLocked"><el-option label="0 · 不重试" :value="0" /><el-option label="1 · 最多额外一次" :value="1" /></el-select></el-form-item>
                  <el-form-item label="重试间隔（毫秒）"><el-input-number v-model="editForm.retryPolicy.backoff_ms" :min="0" :max="30000" :step="100" :disabled="reviewEditorLocked || editForm.retryPolicy.max_retries === 0" /></el-form-item>
                </div>
                <el-form-item label="重试条件"><el-checkbox-group v-model="editForm.retryPolicy.retry_on" :disabled="reviewEditorLocked || editForm.retryPolicy.max_retries === 0"><el-checkbox v-for="option in retryConditionOptions" :key="option.value" :label="option.value">{{ option.label }}</el-checkbox></el-checkbox-group></el-form-item>
              </section>

              <section v-if="editForm.caseType === 'API'" v-show="reviewEditorStep === 'actions'" class="review-editor-panel">
                <el-alert title="此处与正式用例的“动作与清理”字段一一对应；修改后的 JSON 会进入当前编辑值。" type="info" :closable="false" show-icon />
                <el-form-item label="前置动作 JSON"><el-input v-model="editForm.preActions" type="textarea" :rows="8" class="code-input" :disabled="reviewEditorLocked" /></el-form-item>
                <el-form-item label="后置动作 JSON"><el-input v-model="editForm.postActions" type="textarea" :rows="8" class="code-input" :disabled="reviewEditorLocked" /></el-form-item>
                <el-form-item label="Cleanup JSON"><el-input v-model="editForm.cleanup" type="textarea" :rows="8" class="code-input" :disabled="reviewEditorLocked" /></el-form-item>
              </section>

              <section v-if="editForm.caseType === 'API'" v-show="reviewEditorStep === 'verification'" class="review-editor-panel">
                <el-alert title="数据源、响应提取器和断言会随用例版本固化；保存时仍由后端执行完整结构与安全校验。" type="info" :closable="false" show-icon />
                <el-form-item label="数据源 JSON（不使用时为 null）"><el-input v-model="editForm.dataSource" type="textarea" :rows="5" class="code-input" :disabled="reviewEditorLocked" /></el-form-item>
                <el-form-item label="Extractors JSON"><el-input v-model="editForm.extractors" type="textarea" :rows="7" class="code-input" :disabled="reviewEditorLocked" /></el-form-item>
                <el-form-item label="Assertions JSON"><el-input v-model="editForm.assertions" type="textarea" :rows="10" class="code-input" :disabled="reviewEditorLocked" /></el-form-item>
              </section>

              <section v-show="reviewEditorStep === 'outcome'" class="review-editor-panel">
                <el-alert title="完成最后几项说明后即可保存；五个步骤中的内容会一起写入人工编辑版本。" type="info" :closable="false" show-icon />
                <el-form-item label="预期结果"><el-input v-model="editForm.expectedResult" type="textarea" :rows="5" :disabled="reviewEditorLocked" /></el-form-item>
                <el-form-item label="标签（逗号分隔）"><el-input v-model="editForm.tags" :disabled="reviewEditorLocked" /><p class="quality-edit-note">平台质量评分会根据保存后的用例内容重新计算；模型原始自评保持不变。</p></el-form-item>
                <el-form-item label="审核说明"><el-input v-model="editForm.note" maxlength="500" :disabled="reviewEditorLocked" /></el-form-item>
              </section>

              <div class="review-editor-actions"><el-button :disabled="reviewEditorIsFirst" @click="moveReviewEditorStep(-1)">上一步</el-button><span>{{ reviewEditorStepMeta?.title }}</span><el-button v-if="!reviewEditorIsLast" type="primary" @click="moveReviewEditorStep(1)">下一步</el-button><el-button v-else type="primary" plain :loading="savingSuggestionId === selected.id" :disabled="reviewEditorLocked || Boolean(currentEditPreview.error)" @click="saveEdit">检查并保存编辑</el-button></div>
            </main>
          </div>
        </el-form>
        <el-alert v-if="currentEditPreview.error" :title="`当前编辑内容尚不能生成有效用例：${currentEditPreview.error}`" type="error" :closable="false" show-icon class="edit-preview-error" />
        <section class="suggestion-diff"><div><strong>AI 原始结构</strong><pre>{{ JSON.stringify(selected.structured_result, null, 2) }}</pre></div><div><strong>当前编辑值（实时预览）</strong><pre>{{ JSON.stringify(currentEditPreview.value, null, 2) }}</pre></div></section>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.case-suggestion-panel { min-width: 0; container-type: inline-size; }
.ai-feature-card { display: grid; grid-template-columns: 42px minmax(0, 1fr); gap: 10px; margin: 4px 0 16px; padding: 14px; border: 1px solid #cfe0f5; border-radius: 12px; background: linear-gradient(145deg, #f5f9ff 0%, #edf5ff 100%); box-shadow: 0 8px 22px rgba(36, 95, 168, .08); }
.ai-feature-icon { display: grid; width: 42px; height: 42px; place-items: center; border-radius: 12px; background: linear-gradient(145deg, #1677ff, #52a7ff); color: #fff; font-size: 21px; box-shadow: 0 8px 18px rgba(22, 119, 255, .22); }
.ai-feature-copy { display: flex; min-width: 0; flex-direction: column; gap: 5px; }
.ai-feature-copy strong { color: #183b66; font-size: 16px; line-height: 1.4; }
.ai-feature-copy span { color: #526b88; font-size: 13px; line-height: 1.55; }
.ai-feature-copy small { color: #7c8fa7; font-size: 12px; line-height: 1.45; }
.ai-feature-actions { display: grid; grid-column: 1 / -1; grid-template-columns: minmax(0, 1fr); gap: 8px; padding-top: 4px; }
.ai-feature-actions :deep(.el-badge), .ai-feature-actions :deep(.el-button) { width: 100%; min-width: 0; margin-left: 0; }
.ai-feature-actions :deep(.el-badge) { display: flex; }
.test-design-dialog { min-height: 430px; }
.test-design-dialog :deep(.el-steps) { margin: 8px 0 26px; }
.design-step-content { display: flex; flex-direction: column; gap: 14px; min-height: 330px; }
.design-running-state { align-items: center; justify-content: center; color: #718096; text-align: center; }
.design-running-state strong { color: #27364d; font-size: 17px; }
.design-running-state span { max-width: 620px; line-height: 1.6; }
.design-running-state small { color: #91a0b5; }
.design-section-title { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.design-section-title > div { display: flex; flex-direction: column; gap: 5px; }
.design-section-title strong { color: #1f2a44; font-size: 16px; line-height: 1.5; }
.design-section-title span { color: #718096; font-size: 13px; line-height: 1.5; }
.design-routing-summary { display: grid; gap: 9px; padding: 12px 14px; border: 1px solid #dbe6f4; border-radius: 10px; background: #f7faff; }
.design-routing-summary header strong, .design-routing-summary header span { display: block; }
.design-routing-summary header span { margin-top: 3px; color: #718096; font-size: 12px; }
.design-routing-summary > div { display: flex; flex-wrap: wrap; gap: 7px; }
.design-title-actions { align-items: center !important; flex-flow: row wrap !important; justify-content: flex-end; }
.check-point-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.check-point-list article { display: flex; min-width: 0; align-items: flex-start; gap: 10px; padding: 13px; border: 1px solid #dfe7f2; border-radius: 10px; background: #f8fbff; }
.check-point-list article > div { display: flex; min-width: 0; flex-direction: column; gap: 4px; }
.check-point-list article strong { color: #344158; font-size: 14px; line-height: 1.5; word-break: break-word; }
.check-point-list article span { color: #8490a3; font-size: 12px; }
.design-check-point-pagination { display: flex; justify-content: flex-end; margin-top: 2px; }
.recommended-api-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.recommended-api-list :deep(.el-checkbox) { width: 100%; height: auto; min-height: 106px; margin: 0; padding: 12px; align-items: flex-start; white-space: normal; }
.recommended-api-list :deep(.el-checkbox__input) { margin-top: 3px; }
.recommended-api-list :deep(.el-checkbox__label) { width: calc(100% - 24px); min-width: 0; }
.recommended-api-main { display: flex; min-width: 0; flex-direction: column; gap: 5px; }
.recommended-api-main > div { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.recommended-api-main code { overflow: hidden; color: #245fa8; font-size: 13px; text-overflow: ellipsis; }
.recommended-api-main > strong { color: #2c3a52; font-size: 14px; line-height: 1.45; }
.recommended-api-main > span, .recommended-api-main > small { color: #748198; font-size: 12px; line-height: 1.5; }
.result-dialog-title { display: flex; align-items: center; justify-content: space-between; gap: 18px; padding-right: 32px; }
.result-dialog-title > div { display: flex; min-width: 0; flex-direction: column; gap: 4px; }
.result-dialog-title strong { color: #1f2a44; font-size: 18px; line-height: 1.4; }
.result-dialog-title span { overflow: hidden; color: #748198; font-size: 13px; line-height: 1.5; text-overflow: ellipsis; white-space: nowrap; }
:global(.case-result-dialog .el-dialog__body) { max-height: calc(94vh - 92px); overflow-y: auto; }
:global(.case-result-dialog.el-dialog.is-fullscreen) { display: flex; flex-direction: column; overflow: hidden; }
:global(.case-result-dialog.el-dialog.is-fullscreen .el-dialog__body) { max-height: none; min-height: 0; flex: 1; overflow-y: auto; }
.result-summary-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; }
.result-summary-grid > div { display: flex; min-width: 0; flex-direction: column; gap: 6px; padding: 14px 16px; border: 1px solid #dce6f3; border-radius: 10px; background: linear-gradient(145deg, #f8fbff, #f2f7fd); }
.result-summary-grid span { color: #718096; font-size: 12px; }
.result-summary-grid strong { color: #245fa8; font-size: 23px; line-height: 1.1; }
.compiler-summary { margin-top: 12px; }
.result-run-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 24px; margin: 12px 0 4px; color: #7b8798; font-size: 12px; line-height: 1.5; }
.recompile-cases-button { margin-left: auto; }
.result-tabs { margin-top: 10px; }
.result-list-intro { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.result-list-intro > div { display: flex; flex-direction: column; gap: 4px; }
.result-list-intro strong { color: #27364d; font-size: 15px; }
.result-list-intro span { color: #7b8798; font-size: 12px; }
.result-case-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.result-case-list .case-suggestion-card { min-height: 146px; margin: 0; padding: 14px; align-items: flex-start; border-color: #dce6f3; background: #fbfdff; }
.result-case-list .case-suggestion-card:hover { border-color: #9ec5f4; box-shadow: 0 6px 18px rgba(36, 95, 168, .08); }
.result-case-list .suggestion-title { display: flex; align-items: baseline; gap: 7px; font-size: 14px; }
.result-case-list .suggestion-title small { display: inline; flex: 0 0 auto; margin: 0; color: #7da4d2; font-size: 11px; font-weight: 500; }
.suggestion-preview { display: -webkit-box !important; min-height: 42px; margin-top: 9px; overflow: hidden; color: #5c6a80; font-size: 12px; line-height: 1.7; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.result-case-list .suggestion-links { margin-top: 9px; }
.result-case-list .view-detail-link { margin-left: auto !important; color: #1677ff !important; }
.result-pagination { display: flex; justify-content: flex-end; margin-top: 18px; }
.capability-gaps { display: flex; flex-direction: column; gap: 10px; margin-top: 16px; }
.capability-gaps > header { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; }
.capability-gaps > header strong { color: #27364d; font-size: 15px; }
.capability-gaps > header span { color: #7b8798; font-size: 12px; }
.coverage-overview { margin: 12px 0 16px; padding: 14px; border: 1px solid #dce6f3; border-radius: 12px; background: #f8fbff; }
.coverage-overview > header { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.coverage-overview > header > div { display: flex; min-width: 0; flex-direction: column; gap: 3px; }
.coverage-overview > header strong { color: #245fa8; font-size: 18px; }
.coverage-overview > header > div strong { color: #27364d; font-size: 14px; }
.coverage-overview > header span { color: #7b8798; font-size: 12px; }
.coverage-points { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; margin-top: 10px; }
.coverage-points > span { display: flex; min-width: 0; align-items: center; gap: 6px; color: #8a5a20; font-size: 12px; line-height: 1.45; }
.coverage-points > span.covered { color: #47705a; }
.coverage-points strong { flex: 0 0 auto; }
.generation-task-list { margin: 12px 0 18px; overflow: hidden; border: 1px solid #dce6f3; border-radius: 12px; background: #f8fbff; }
.generation-task-list > header, .generation-task-list > article { display: flex; align-items: center; gap: 12px; padding: 12px 14px; }
.generation-task-list > header { justify-content: space-between; border-bottom: 1px solid #e5edf7; }
.generation-task-list > header div, .task-summary { display: flex; min-width: 0; flex: 1; flex-direction: column; gap: 3px; }
.generation-task-list > header span, .task-summary span, .task-summary small { color: #7b8798; font-size: 12px; line-height: 1.45; }
.task-summary strong { color: #27364d; font-size: 14px; line-height: 1.4; }
.task-summary small { color: #d74747; word-break: break-word; }
.task-actions { display: flex; flex: 0 0 auto; align-items: center; gap: 4px; }
.generation-task-list > article + article { border-top: 1px solid #edf2f8; }
.case-suggestion-card { min-height: 72px; align-items: center; padding: 12px; }
.suggestion-card-button { display: block; width: 100%; min-width: 0; }
.suggestion-heading { display: grid !important; grid-template-columns: minmax(0, 1fr) auto; align-items: start !important; gap: 8px !important; }
.suggestion-title { display: block; overflow: hidden; line-height: 1.45; text-overflow: ellipsis; white-space: normal !important; word-break: break-word; }
.suggestion-status { align-self: start; flex: 0 0 auto; }
.suggestion-status :deep(.el-tag__content) { display: inline; margin: 0; color: inherit; font-size: 12px; line-height: 1; }
.suggestion-meta, .suggestion-links { display: flex !important; flex-wrap: wrap; align-items: center; justify-content: flex-start !important; gap: 4px 10px !important; margin-top: 7px; }
.suggestion-meta span, .suggestion-links span { display: inline !important; margin: 0 !important; color: #768399 !important; font-size: 12px !important; line-height: 1.5; }
.suggestion-links span { color: #5e7da4 !important; }
.review-detail-header { gap: 12px; }
.review-detail-header > div { min-width: 0; flex-wrap: wrap; }
.review-detail-actions { justify-content: flex-end; }
.review-sequence { margin-right: 2px; color: #526078 !important; font-weight: 600; }
.suggestion-selects { width: 100%; }
.suggestion-selects :deep(.el-select) { min-width: 0; flex: 1 1 0; }
.suggestion-selects :deep(.el-select__wrapper) { width: 100%; }
.generation-business-summary { margin: 16px 0 10px; }
.generation-business-summary :deep(.el-descriptions__label),
.generation-business-summary :deep(.el-descriptions__content),
.generation-audit-sections :deep(.el-descriptions__label),
.generation-audit-sections :deep(.el-descriptions__content) { font-size: 14px; line-height: 1.55; }
.generation-business-summary :deep(.el-tag + .el-tag) { margin-left: 6px; }
.suggestion-quality-panel { margin: 12px 0 16px; padding: 16px; border: 1px solid #dce6f3; border-radius: 12px; background: #f8fbff; }
.suggestion-quality-panel > header { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 14px; }
.suggestion-quality-panel > header div { display: flex; align-items: baseline; gap: 10px; }
.suggestion-quality-panel > header div > span { color: #526078; font-size: 14px; font-weight: 600; }
.suggestion-quality-panel > header div > strong { color: #245fa8; font-size: 28px; line-height: 1; }
.suggestion-quality-panel > header div > strong small { margin-left: 2px; color: #78879d; font-size: 13px; font-weight: 500; }
.suggestion-quality-panel > header p { max-width: 520px; margin: 0; color: #667085; font-size: 13px; line-height: 1.55; text-align: right; }
.quality-checks { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; }
.quality-checks > div { display: flex; min-width: 0; align-items: flex-start; gap: 8px; padding: 10px; border: 1px solid #e4e9f1; border-radius: 8px; background: white; }
.quality-checks > div.passed { border-color: #d5ead0; background: #fbfefb; }
.quality-checks > div > span { display: flex; min-width: 0; flex-direction: column; gap: 3px; }
.quality-checks strong { color: #344158; font-size: 13px; line-height: 1.4; }
.quality-checks small { color: #7b8798; font-size: 12px; line-height: 1.45; }
.ai-self-assessment { display: grid; grid-template-columns: auto auto minmax(0, 1fr); align-items: center; gap: 10px; margin-top: 12px; padding-top: 12px; border-top: 1px solid #e4eaf2; }
.ai-self-assessment > span { color: #526078; font-size: 14px; font-weight: 600; }
.ai-self-assessment > strong { color: #344158; font-size: 14px; }
.ai-self-assessment > small { color: #7b8798; font-size: 12px; line-height: 1.5; }
.quality-edit-note { margin: 10px 0 0; color: #7b8798; font-size: 12px; line-height: 1.5; }
.legacy-incomplete-alert { margin: -4px 0 16px; }
.generation-audit-sections { margin: 0 0 22px; border-top: 0; }
.generation-audit-sections :deep(.el-collapse-item__header) { color: #344158; font-size: 14px; font-weight: 600; }
.technical-trace-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; color: #7b8798; font-size: 13px; }
.technical-trace-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; margin: 0; overflow: hidden; border: 1px solid #e4e9f1; border-radius: 8px; background: #e4e9f1; }
.technical-trace-grid div { display: grid; grid-template-columns: minmax(120px, 1fr) minmax(60px, .6fr); gap: 8px; padding: 10px 12px; background: #f8fafc; }
.technical-trace-grid dt { color: #667085; font-size: 13px; }
.technical-trace-grid dd { margin: 0; color: #344158; font-family: "JetBrains Mono", Consolas, monospace; font-size: 13px; text-align: right; word-break: break-all; }
.bulk-edit-warning { margin-bottom: 16px; }
.suggestion-requirement-scope { margin: 12px 0 16px; padding: 14px 16px; border: 1px solid #dce6f3; border-radius: 10px; background: #fbfcff; }
.suggestion-requirement-scope > div:first-child { display: flex; align-items: baseline; gap: 10px; }
.suggestion-requirement-scope > div:first-child span { color: #7b8798; font-size: 12px; }
.requirement-ref-list { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.coverage-point-list { display: grid; gap: 6px; margin-top: 10px; }
.coverage-point-list span { display: flex; gap: 8px; color: #526078; font-size: 13px; }
.coverage-point-list strong { color: #245fa8; }
.review-case-editor { margin-top: 18px; }
.review-editor-shell { display: grid; min-height: 620px; grid-template-columns: 220px minmax(0, 1fr); overflow: hidden; border: 1px solid #e1e6ef; border-radius: 12px; }
.review-editor-nav { padding: 16px 12px; background: linear-gradient(180deg, #f7f9ff 0%, #f4f6fb 100%); border-right: 1px solid #e1e6ef; }
.review-editor-nav-title { padding: 0 10px 10px; color: #7a8599; font-size: 13px; font-weight: 700; letter-spacing: .08em; }
.review-editor-nav-item { display: flex; width: 100%; align-items: flex-start; gap: 10px; margin: 3px 0; padding: 12px 10px; border: 1px solid transparent; border-radius: 9px; background: transparent; color: #344054; text-align: left; cursor: pointer; }
.review-editor-nav-item:hover, .review-editor-nav-item.active { background: #fff; border-color: #b9c7ff; }
.review-editor-nav-item > span { display: inline-flex; flex: 0 0 25px; width: 25px; height: 25px; align-items: center; justify-content: center; border-radius: 8px; background: #e6ebff; color: #3b5ccc; font-weight: 700; }
.review-editor-nav-item.active > span { background: #4263eb; color: #fff; }
.review-editor-nav-item strong, .review-editor-nav-item small { display: block; }
.review-editor-nav-item small { margin-top: 3px; color: #7a8599; font-size: 12px; line-height: 1.35; }
.review-editor-workspace { display: flex; min-width: 0; flex-direction: column; padding: 20px 24px; }
.review-editor-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; padding-bottom: 14px; border-bottom: 1px solid #edf0f5; }
.review-editor-heading span { color: #6c7a94; font-size: 13px; }
.review-editor-heading h3 { margin: 4px 0 2px; color: #1f2a44; font-size: 20px; }
.review-editor-heading p { margin: 0; color: #7a8599; font-size: 13px; }
.review-editor-panel { flex: 1; padding-top: 18px; }
.review-editor-panel :deep(.el-select) { width: 100%; }
.review-form-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.review-request-line { display: grid; grid-template-columns: 140px minmax(0, 1fr); gap: 14px; }
.review-body-editor, .review-auth-editor { display: grid; gap: 12px; }
.review-request-settings { display: flex; align-items: center; gap: 18px; margin-top: 14px; }
.review-editor-actions { display: flex; align-items: center; justify-content: flex-end; gap: 10px; margin-top: 18px; padding-top: 14px; border-top: 1px solid #edf0f5; }
.review-editor-actions > span { margin-right: auto; color: #7a8599; font-size: 13px; }
.review-case-editor .code-input :deep(textarea) { font-family: "JetBrains Mono", Consolas, monospace; }
.edit-preview-error { margin-top: 14px; }
.suggestion-diff { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin: 18px 0; }
.suggestion-diff pre, .raw-response { max-height: 360px; overflow: auto; padding: 12px; border-radius: 8px; background: #101828; color: #d0d5dd; white-space: pre-wrap; word-break: break-word; }
@container (max-width: 390px) {
  .generation-task-list > article { align-items: flex-start; flex-direction: column; }
  .task-actions { width: 100%; justify-content: space-between; }
}
@media (max-width: 900px) {
  .suggestion-diff, .technical-trace-grid, .quality-checks, .check-point-list, .recommended-api-list, .coverage-points, .result-case-list { grid-template-columns: 1fr; }
  .result-summary-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .result-dialog-title, .capability-gaps > header, .review-detail-header { align-items: flex-start; flex-direction: column; }
  .review-detail-actions { justify-content: flex-start; }
  .suggestion-quality-panel > header { align-items: flex-start; flex-direction: column; gap: 8px; }
  .suggestion-quality-panel > header p { text-align: left; }
  .review-editor-shell { grid-template-columns: 1fr; }
  .review-editor-nav { display: flex; gap: 8px; overflow-x: auto; border-right: 0; border-bottom: 1px solid #e1e6ef; }
  .review-editor-nav-title { display: none; }
  .review-editor-nav-item { min-width: 170px; margin: 0; }
  .review-form-grid, .review-request-line { grid-template-columns: 1fr; }
}
</style>
