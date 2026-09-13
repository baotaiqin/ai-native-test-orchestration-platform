<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ArrowDown, ArrowUp, Delete, EditPen, Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute } from 'vue-router'

import {
  archiveTestCase, createTestCase, createTestCaseVersion, getTestCases,
  getTestCaseVersions, previewCaseRuntime,
} from '@/api/test-cases'
import { getDatasets, previewDatasetIterations } from '@/api/datasets'
import { getProjects } from '@/api/projects'
import { getPrompts } from '@/api/prompt-center'
import { getRequirementTree } from '@/api/requirements'
import type { Project } from '@/types/project'
import type { Dataset, DatasetIterationPreview } from '@/types/dataset'
import type { PromptDefinition } from '@/types/prompt-center'
import type { RequirementTreeNode } from '@/types/requirement'
import type {
  Assertion, AssertionOperator, AssertionSource, DeterministicAssertionType,
  ActionType, ApiRequestTemplate, CaseAction, CasePriority, CaseType, FakerGenerator,
  CleanupConfig, PostAction, PreAction, RequestValueItem, ResponseExtractor, RuntimePreviewResult,
  RetryCondition, RetryPolicy, RuntimeResponseSnapshot, SuggestedCase, SuggestedStep, TestCaseAsset, TestCaseVersion,
  VariableAction,
} from '@/types/test-case'
import {
  API_STEP_RETRY_LIMIT_ERROR_MESSAGE,
  isV1ApiStepMaxRetries,
  retryAwareApiErrorMessage,
} from '@/utils/v1-retry-policy'
import { identityIsCurrent, isIdentityStorageEvent, readRequestIdentity } from '@/utils/request-context'
import { promptOptionLabel } from '@/utils/prompt-display'
import AssetRequirementLinksPanel from '@/views/requirements/components/AssetRequirementLinksPanel.vue'
import { RECORD_PAGE_SIZES, useClientPagination } from '@/composables/useClientPagination'
import { formatApiDateTime } from '@/utils/datetime'

interface CaseForm {
  title: string
  case_type: CaseType
  priority: CasePriority
  preconditions: string
  steps: string
  test_data: string
  expected_result: string
  tags: string
  confidence: number
  change_note: string
  request_method: ApiRequestTemplate['method']
  request_url: string
  request_query: string
  request_headers: string
  request_cookies: string
  request_body_type: ApiRequestTemplate['body']['type']
  request_body: string
  auth_type: ApiRequestTemplate['auth']['type']
  auth_token: string
  auth_username: string
  auth_password: string
  auth_key_name: string
  auth_key_value: string
  auth_placement: 'HEADER' | 'QUERY'
  timeout_ms: number
  follow_redirects: boolean
  retry_policy: RetryPolicy
  pre_actions: string
  extractors: string
  post_actions: string
  data_source_dataset_id: number | null
  data_source_version_id: number | null
  data_source_prefix: string
  data_source_mapping: string
  assertions: string
  cleanup: string
}

type ActionPhase = 'pre' | 'post'
type ActionOption = { value: ActionType; label: string }
type CaseEditorStep = 'basic' | 'request' | 'actions' | 'verification' | 'outcome'
type CaseEditorStepItem = { key: CaseEditorStep; title: string; description: string }

const actionPhases: ActionPhase[] = ['pre', 'post']
const actionOptions: Record<ActionPhase, ActionOption[]> = {
  pre: [
    { value: 'SET_VARIABLE', label: '设置变量' },
    { value: 'FAKER', label: '生成 Faker 数据' },
    { value: 'SQL_QUERY', label: '执行 SQL 查询' },
    { value: 'PYTHON_SCRIPT', label: '执行 Python 脚本' },
    { value: 'GET_TOKEN', label: '获取 Token' },
    { value: 'API_SETUP', label: 'API 数据准备' },
  ],
  post: [
    { value: 'EXTRACT_RESPONSE', label: '提取响应' },
    { value: 'SET_VARIABLE', label: '设置变量' },
    { value: 'PYTHON_SCRIPT', label: '执行 Python 脚本' },
    { value: 'REGISTER_RESOURCE', label: '注册资源' },
  ],
}

const projects = ref<Project[]>([])
const projectId = ref<number>()
const cases = ref<TestCaseAsset[]>([])
const {
  items: pagedCases, total: caseTotal, page: casePage,
  pageSize: casePageSize, changePage: changeCasePage,
  changePageSize: changeCasePageSize,
} = useClientPagination(cases)
const datasets = ref<Dataset[]>([])
const aiAssertionPrompts = ref<PromptDefinition[]>([])
const requirementOptions = ref<RequirementTreeNode[]>([])
const createRequirementIds = ref<number[]>([])
const requirementsLoading = ref(false)
const selected = ref<TestCaseAsset | null>(null)
const versions = ref<TestCaseVersion[]>([])
const caseVersionPages = useClientPagination(versions)
const loading = ref(false)
const editorVisible = ref(false)
const detailVisible = ref(false)
const saving = ref(false)
const editingId = ref<number>()
const runtimeVisible = ref(false)
const runtimeLoading = ref(false)
const runtimeContext = ref(`{
  "base_url": "https://api.example.test"
}`)
const runtimeResponse = ref(`{
  "status_code": 200,
  "json_body": {},
  "headers": {},
  "cookies": {}
}`)
const runtimeResult = ref<RuntimePreviewResult | null>(null)
const runtimeResponseTime = ref<number | null>(0)
const actionPhaseTab = ref<ActionPhase>('pre')
const editorStep = ref<CaseEditorStep>('basic')
const dataPreviewVisible = ref(false)
const dataPreview = ref<DatasetIterationPreview | null>(null)
const dataPreviewLoading = ref(false)
const route = useRoute()
const deepLinkNotice = ref<string | null>(null)
const focusedVersionId = ref<number | null>(null)
const reverseExpanded = ref<string[]>(route.query.link_source === 'requirement' ? ['requirements'] : [])
let casesSequence = 0
let detailSequence = 0
let alive = true

function positiveQueryId(value: unknown): number | null {
  const candidate = Array.isArray(value) ? value[0] : value
  if (typeof candidate !== 'string' || !/^[1-9]\d*$/.test(candidate)) return null
  const parsed = Number(candidate)
  return Number.isSafeInteger(parsed) ? parsed : null
}

const requestedProjectId = positiveQueryId(route.query.project_id)
const requestedCaseId = positiveQueryId(route.query.test_case_id)
const requestedVersionId = positiveQueryId(route.query.version_id)

const retryConditionOptions: Array<{ value: RetryCondition; label: string }> = [
  { value: 'TARGET_NETWORK_ERROR', label: '网络错误' },
  { value: 'TARGET_TIMEOUT', label: '请求超时' },
  { value: 'HTTP_5XX', label: '服务端 5xx 响应' },
]

function apiErrorMessage(error: unknown, fallback: string): string {
  return retryAwareApiErrorMessage(error, fallback)
}

/** Drafts let JSON-valued fields be edited naturally before a valid JSON value is committed. */
const actionJsonDrafts = reactive<Record<string, string>>({})
const actionJsonErrors = reactive<Record<string, string>>({})
const assertionExpectedDrafts = reactive<Record<string, string>>({})
const structuredJsonFields = new Set(['params', 'metadata', 'request', 'response'])

function emptyForm(): CaseForm {
  return {
    title: '', case_type: 'API', priority: 'P1', preconditions: '',
    steps: JSON.stringify([{ order: 1, action: '', expected: '' }], null, 2),
    test_data: '{}', expected_result: '', tags: '', confidence: 1,
    change_note: '人工创建',
    request_method: 'GET', request_url: '{{base_url}}/',
    request_query: '[]', request_headers: '[]', request_cookies: '[]',
    request_body_type: 'NONE', request_body: '', auth_type: 'NONE',
    auth_token: '', auth_username: '', auth_password: '', auth_key_name: '',
    auth_key_value: '', auth_placement: 'HEADER', timeout_ms: 30000,
    follow_redirects: true,
    retry_policy: defaultRetryPolicy(),
    pre_actions: '[]', extractors: '[]', post_actions: '[]',
    data_source_dataset_id: null, data_source_version_id: null,
    data_source_prefix: '', data_source_mapping: '{}',
    assertions: '[]',
    cleanup: '[]',
  }
}

function defaultRetryPolicy(): RetryPolicy {
  return {
    max_retries: 0,
    backoff_ms: 500,
    retry_on: retryConditionOptions.map((item) => item.value),
  }
}

function boundedInteger(value: unknown, fallback: number, min: number, max: number): number {
  const numeric = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(numeric)) return fallback
  return Math.min(max, Math.max(min, Math.trunc(numeric)))
}

function normalizeRetryPolicy(policy: Partial<RetryPolicy> | null | undefined): RetryPolicy {
  const rawMaxRetries = (policy as { max_retries?: unknown } | null | undefined)?.max_retries
  const sourceRetryOn = policy?.retry_on
  const retryOn = Array.isArray(sourceRetryOn)
    ? retryConditionOptions
      .filter((item) => sourceRetryOn.includes(item.value))
      .map((item) => item.value)
    : retryConditionOptions.map((item) => item.value)
  return {
    max_retries: (rawMaxRetries === undefined ? 0 : rawMaxRetries) as number,
    backoff_ms: boundedInteger(policy?.backoff_ms, 500, 0, 30000),
    retry_on: retryOn,
  }
}

function retryPolicyValidationMessage(policy: RetryPolicy): string {
  if (!isV1ApiStepMaxRetries(policy.max_retries)) {
    return API_STEP_RETRY_LIMIT_ERROR_MESSAGE
  }
  if (policy.max_retries > 0 && policy.retry_on.length === 0) {
    return '已开启重试，请至少选择一项重试条件'
  }
  return ''
}

const form = ref<CaseForm>(emptyForm())
const activeCount = computed(() => cases.value.filter((item) => item.status === 'ACTIVE').length)
const currentProject = computed(() => projects.value.find((item) => item.id === projectId.value))
function flattenRequirements(items: RequirementTreeNode[]): RequirementTreeNode[] {
  return items.flatMap((item) => [item, ...flattenRequirements(item.children)])
}
const flatRequirementOptions = computed(() => flattenRequirements(requirementOptions.value))
const retryPolicyError = computed(() => retryPolicyValidationMessage(form.value.retry_policy))
const retryPolicyNeedsCorrection = computed(() => !isV1ApiStepMaxRetries(form.value.retry_policy.max_retries))
const caseEditorSteps = computed<CaseEditorStepItem[]>(() => {
  const steps: CaseEditorStepItem[] = [
    { key: 'basic', title: '基础信息', description: '名称、类型与通用数据' },
  ]
  if (form.value.case_type === 'API') {
    steps.push(
      { key: 'request', title: '请求配置', description: '地址、参数、认证与重试' },
      { key: 'actions', title: '动作与清理', description: '前后置动作和资源回收' },
      { key: 'verification', title: '数据与断言', description: '数据集、提取器与校验规则' },
    )
  }
  steps.push({ key: 'outcome', title: '结果与说明', description: '预期结果、标签与版本说明' })
  return steps
})
const editorStepIndex = computed(() => Math.max(
  0,
  caseEditorSteps.value.findIndex((item) => item.key === editorStep.value),
))
const editorStepMeta = computed(() => caseEditorSteps.value[editorStepIndex.value] ?? caseEditorSteps.value[0])
const editorIsFirstStep = computed(() => editorStepIndex.value === 0)
const editorIsLastStep = computed(() => editorStepIndex.value === caseEditorSteps.value.length - 1)
const retryConditionsError = computed(() => (
  isV1ApiStepMaxRetries(form.value.retry_policy.max_retries)
    && form.value.retry_policy.max_retries > 0
    && form.value.retry_policy.retry_on.length === 0
    ? '已开启重试，请至少选择一项重试条件'
    : ''
))

function retryCountDisplay(value: unknown): string {
  if (typeof value === 'string') return value
  try { return JSON.stringify(value) ?? String(value) } catch { return String(value) }
}

function retryPolicySummary(value: unknown): string {
  if (value === 0) return '不重试'
  if (value === 1) return '最多额外一次'
  return `历史值 ${retryCountDisplay(value)}`
}

function actionField(action: CaseAction, key: string): unknown {
  return (action as unknown as Record<string, unknown>)[key]
}

function parseActions(raw: string): CaseAction[] {
  try {
    const value: unknown = JSON.parse(raw)
    return Array.isArray(value) ? value as CaseAction[] : []
  } catch {
    return []
  }
}

function actionJsonError(raw: string): string {
  try {
    const value: unknown = JSON.parse(raw)
    return Array.isArray(value) ? '' : '高级 JSON 必须是数组'
  } catch {
    return '高级 JSON 格式无效，修正后才能保存'
  }
}

const preActions = computed(() => parseActions(form.value.pre_actions))
const postActions = computed(() => parseActions(form.value.post_actions))
const preActionsError = computed(() => actionJsonError(form.value.pre_actions))
const postActionsError = computed(() => actionJsonError(form.value.post_actions))

function normalizeCleanup(item: Partial<CleanupConfig>): CleanupConfig {
  const cleanupType = item.cleanup_type ?? 'API'
  return {
    cleanup_id: item.cleanup_id ?? null,
    cleanup_type: cleanupType,
    policy: item.policy ?? 'ALWAYS',
    enabled: item.enabled !== false,
    timeout_ms: item.timeout_ms ?? 30000,
    method: item.method ?? 'DELETE',
    url: item.url ?? '{{base_url}}/resources/{{resource_id}}',
    query_params: item.query_params ?? [],
    headers: item.headers ?? [],
    cookies: item.cookies ?? [],
    body: item.body ?? { type: 'NONE', content: null },
    auth: {
      ...(item.auth ?? {}), type: item.auth?.type ?? 'NONE',
    },
    connection_id: cleanupType === 'SQL' ? (item.connection_id ?? null) : null,
    sql: cleanupType === 'SQL'
      ? (item.sql ?? 'DELETE FROM resources WHERE id = %(resource_id)s')
      : null,
    params: cleanupType === 'SQL' ? (item.params ?? {}) : {},
    resource_id_param: cleanupType === 'SQL' ? (item.resource_id_param ?? 'resource_id') : null,
  }
}

function serializeCleanup(item: CleanupConfig): CleanupConfig {
  const common = {
    cleanup_id: item.cleanup_id ?? null,
    cleanup_type: item.cleanup_type,
    policy: item.policy,
    enabled: item.enabled,
    timeout_ms: item.timeout_ms,
  }
  if (item.cleanup_type === 'API') {
    return {
      ...common,
      method: item.method ?? 'DELETE',
      url: item.url ?? '{{base_url}}/resources/{{resource_id}}',
      query_params: item.query_params ?? [],
      headers: item.headers ?? [],
      cookies: item.cookies ?? [],
      body: item.body ?? { type: 'NONE', content: null },
      auth: item.auth ?? { type: 'NONE' },
    } as CleanupConfig
  }
  return {
    ...common,
    connection_id: item.connection_id ?? null,
    sql: item.sql ?? 'DELETE FROM resources WHERE id = %(resource_id)s',
    params: item.params ?? {},
    resource_id_param: item.resource_id_param ?? 'resource_id',
  } as CleanupConfig
}

function parseCleanups(raw: string): CleanupConfig[] {
  try {
    const value: unknown = JSON.parse(raw)
    return Array.isArray(value) ? value.map((item) => normalizeCleanup(item as Partial<CleanupConfig>)) : []
  } catch { return [] }
}

function cleanupJsonError(raw: string): string {
  try {
    const value: unknown = JSON.parse(raw)
    return Array.isArray(value) ? '' : 'Cleanup 必须是数组'
  } catch { return 'Cleanup JSON 格式无效，修正后才能保存' }
}

const cleanups = computed(() => parseCleanups(form.value.cleanup))
const cleanupError = computed(() => cleanupJsonError(form.value.cleanup))

function writeCleanups(items: CleanupConfig[]): void {
  form.value.cleanup = JSON.stringify(items.map(serializeCleanup), null, 2)
}

function createCleanup(): CleanupConfig {
  return normalizeCleanup({ cleanup_id: `cleanup_${cleanups.value.length + 1}` })
}

function addCleanup(): void { writeCleanups([...cleanups.value, createCleanup()]) }
function removeCleanup(index: number): void {
  writeCleanups(cleanups.value.filter((_, itemIndex) => itemIndex !== index))
}
function moveCleanup(index: number, offset: -1 | 1): void {
  const items = [...cleanups.value]
  const target = index + offset
  if (target < 0 || target >= items.length) return
  const current = items[index]
  const next = items[target]
  if (!current || !next) return
  items[index] = next
  items[target] = current
  writeCleanups(items)
}
function updateCleanupField(index: number, key: keyof CleanupConfig, value: unknown): void {
  const items = [...cleanups.value]
  const current = items[index]
  if (!current) return
  items[index] = normalizeCleanup({ ...current, [key]: value })
  writeCleanups(items)
}
function updateCleanupAuth(index: number, key: string, value: unknown): void {
  const items = [...cleanups.value]
  const current = items[index]
  if (!current) return
  items[index] = normalizeCleanup({
    ...current,
    auth: {
      ...(current.auth ?? {}),
      type: current.auth?.type ?? 'NONE',
      [key]: value,
    },
  })
  writeCleanups(items)
}
function cleanupJsonText(cleanup: CleanupConfig, key: 'query_params' | 'headers' | 'cookies' | 'body' | 'params'): string {
  const value = cleanup[key]
  return JSON.stringify(value ?? (key === 'params' ? {} : []), null, 2)
}
function updateCleanupJson(index: number, key: 'query_params' | 'headers' | 'cookies' | 'body' | 'params', raw: string): void {
  try {
    const value: unknown = JSON.parse(raw)
    updateCleanupField(index, key, value)
  } catch { ElMessage.warning(`${key} 必须是有效 JSON`) }
}

function actionsFor(phase: ActionPhase): CaseAction[] {
  return phase === 'pre' ? preActions.value : postActions.value
}

function actionFieldKey(phase: ActionPhase): 'pre_actions' | 'post_actions' {
  return phase === 'pre' ? 'pre_actions' : 'post_actions'
}

function actionTypeOf(action: CaseAction): ActionType {
  const type = actionField(action, 'type')
  return typeof type === 'string' ? type as ActionType : 'SET_VARIABLE'
}

function actionTypeLabel(action: CaseAction): string {
  const type = actionTypeOf(action)
  return actionOptions.pre.concat(actionOptions.post).find((item) => item.value === type)?.label ?? type
}

function isLegacyVariableAction(action: CaseAction): action is VariableAction {
  return !Object.prototype.hasOwnProperty.call(action, 'type')
}

function actionOptionsFor(phase: ActionPhase): ActionOption[] {
  return actionOptions[phase]
}

function isAllowedActionType(phase: ActionPhase, type: ActionType): boolean {
  return actionOptionsFor(phase).some((item) => item.value === type)
}

function actionText(action: CaseAction, key: string): string {
  const value = actionField(action, key)
  if (value === null || value === undefined) return ''
  return typeof value === 'string' ? value : String(value)
}

function actionBoolean(action: CaseAction, key: string): boolean {
  const value = actionField(action, key)
  return value !== false
}

function actionNumber(action: CaseAction, key: string): number | undefined {
  const value = actionField(action, key)
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) return Number(value)
  return undefined
}

function jsonDraftKey(phase: ActionPhase, index: number, key: string): string {
  return `${phase}:${index}:${key}`
}

function actionJsonText(phase: ActionPhase, index: number, key: string, action: CaseAction): string {
  const draftKey = jsonDraftKey(phase, index, key)
  if (Object.prototype.hasOwnProperty.call(actionJsonDrafts, draftKey)) {
    return actionJsonDrafts[draftKey] ?? ''
  }
  const value = actionField(action, key)
  if (value === undefined) return ''
  if (typeof value === 'string') return value
  try { return JSON.stringify(value, null, 2) ?? '' } catch { return String(value) }
}

function clearActionDrafts(phase?: ActionPhase): void {
  Object.keys(actionJsonDrafts).forEach((key) => {
    if (!phase || key.startsWith(`${phase}:`)) delete actionJsonDrafts[key]
  })
  Object.keys(actionJsonErrors).forEach((key) => {
    if (!phase || key.startsWith(`${phase}:`)) delete actionJsonErrors[key]
  })
}

function writeActions(phase: ActionPhase, actions: CaseAction[]): void {
  form.value[actionFieldKey(phase)] = JSON.stringify(actions, null, 2)
}

function updateActionField(phase: ActionPhase, index: number, key: string, value: unknown): void {
  const actions = actionsFor(phase)
  const current = actions[index]
  if (!current) return
  const next = { ...(current as unknown as Record<string, unknown>) }
  if (value === undefined) delete next[key]
  else next[key] = value
  actions[index] = next as unknown as CaseAction
  writeActions(phase, actions)
}

function updateOptionalNumber(phase: ActionPhase, index: number, key: string, value: number | null): void {
  updateActionField(phase, index, key, value === null ? undefined : value)
}

function updateJsonDraft(phase: ActionPhase, index: number, key: string, value: string | number): void {
  const draftKey = jsonDraftKey(phase, index, key)
  const raw = String(value)
  actionJsonDrafts[draftKey] = raw
  if (!structuredJsonFields.has(key)) return
  const error = validateStructuredJsonText(key, raw)
  if (error) actionJsonErrors[draftKey] = error
  else delete actionJsonErrors[draftKey]
}

function commitJsonField(phase: ActionPhase, index: number, key: string): void {
  const draftKey = jsonDraftKey(phase, index, key)
  const raw = actionJsonDrafts[draftKey] ?? actionJsonText(phase, index, key, actionsFor(phase)[index] ?? {})
  if (structuredJsonFields.has(key)) {
    const error = validateStructuredJsonText(key, raw)
    if (error) {
      actionJsonErrors[draftKey] = error
      ElMessage.error(error)
      return
    }
    delete actionJsonErrors[draftKey]
    const parsed = raw.trim() ? JSON.parse(raw) as unknown : {}
    updateActionField(phase, index, key, parsed)
    delete actionJsonDrafts[draftKey]
    return
  }
  if (!raw.trim()) {
    updateActionField(phase, index, key, '')
    delete actionJsonDrafts[draftKey]
    return
  }
  try {
    updateActionField(phase, index, key, JSON.parse(raw) as unknown)
    delete actionJsonDrafts[draftKey]
  } catch {
    // A plain string is a useful value for SET_VARIABLE and defaults. Other
    // structured fields can still be corrected through the advanced JSON view.
    if (key === 'value' || key === 'default_value') {
      updateActionField(phase, index, key, raw)
      delete actionJsonDrafts[draftKey]
    }
  }
}

function validateStructuredJsonText(key: string, raw: string): string {
  if (!raw.trim()) return ''
  let parsed: unknown
  try { parsed = JSON.parse(raw) } catch { return `${key} 必须是有效 JSON` }
  if (key === 'params') {
    if (typeof parsed !== 'object' || parsed === null) return 'Params 必须是 JSON 对象或数组'
    return ''
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    const label = key === 'metadata' ? 'Metadata' : key === 'request' ? 'Request' : 'Response'
    return `${label} 必须是 JSON 对象`
  }
  return ''
}

function actionJsonDraftError(phase: ActionPhase, index: number, key: string): string {
  return actionJsonErrors[jsonDraftKey(phase, index, key)] ?? ''
}

function firstActionJsonError(): string {
  return Object.values(actionJsonErrors)[0] ?? ''
}

function structuredActionValueError(action: CaseAction, key: string): string {
  const value = actionField(action, key)
  if (value === undefined) return ''
  if (key === 'params') {
    if (typeof value !== 'object' || value === null) return 'Params 必须是 JSON 对象或数组'
    return ''
  }
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    const label = key === 'metadata' ? 'Metadata' : key === 'request' ? 'Request' : 'Response'
    return `${label} 必须是 JSON 对象`
  }
  return ''
}

function validateStructuredActionValues(phase: ActionPhase, actions: CaseAction[]): string {
  for (let index = 0; index < actions.length; index += 1) {
    const action = actions[index]
    if (!action) continue
    const type = actionTypeOf(action)
    const fields = type === 'SQL_QUERY'
      ? ['params']
      : type === 'REGISTER_RESOURCE'
        ? ['metadata']
        : ['GET_TOKEN', 'API_SETUP'].includes(type)
          ? ['request', 'response']
          : []
    for (const key of fields) {
      const error = structuredActionValueError(action, key)
      if (error) return `${phase === 'pre' ? 'Pre' : 'Post'} Actions 第 ${index + 1} 个动作：${error}`
    }
  }
  return ''
}

function createAction(type: ActionType): CaseAction {
  switch (type) {
    case 'FAKER':
      return { type, name: 'generated_value', generator: 'uuid' as FakerGenerator, enabled: true }
    case 'SQL_QUERY':
      return { type, connection_id: 1, sql: 'SELECT 1', params: {}, result_variable: 'sql_rows', max_rows: 100, enabled: true }
    case 'PYTHON_SCRIPT':
      return { type, script: '', enabled: true }
    case 'GET_TOKEN':
    case 'API_SETUP':
      return {
        type,
        name: type === 'GET_TOKEN' ? 'token' : 'resource_id',
        source: 'JSONPATH',
        expression: type === 'GET_TOKEN' ? '$.access_token' : '$.data.id', required: true,
        default_value: '',
        request: {
          method: 'POST', url: type === 'GET_TOKEN' ? '{{base_url}}/login' : '{{base_url}}/resources', query_params: [], headers: [], cookies: [],
          body: { type: 'JSON', content: type === 'GET_TOKEN' ? { username: '{{secret.AI_TEST_USERNAME}}', password: '{{secret.AI_TEST_PASSWORD}}' } : { name: 'generated-resource' } },
          auth: { type: 'NONE' }, timeout_ms: 30000, follow_redirects: false,
          retry_policy: { max_retries: 0, backoff_ms: 0, retry_on: [] },
        },
        response: { status_code: 200, json_body: {}, headers: {}, cookies: {} }, enabled: true,
      }
    case 'EXTRACT_RESPONSE':
      return { type, name: 'extracted_value', source: 'JSONPATH', expression: '$.data', required: true, default_value: '', enabled: true }
    case 'REGISTER_RESOURCE':
      return { type, name: 'resource', resource_type: 'entity', value: '', metadata: {}, cleanup_ref: null, enabled: true }
    case 'SET_VARIABLE':
    default:
      return { type: 'SET_VARIABLE', name: 'variable', value: '', enabled: true }
  }
}

function addAction(phase: ActionPhase): void {
  const firstType = actionOptionsFor(phase)[0]?.value ?? 'SET_VARIABLE'
  writeActions(phase, [...actionsFor(phase), createAction(firstType)])
  clearActionDrafts(phase)
}

function removeAction(phase: ActionPhase, index: number): void {
  writeActions(phase, actionsFor(phase).filter((_, itemIndex) => itemIndex !== index))
  clearActionDrafts(phase)
}

function moveAction(phase: ActionPhase, index: number, offset: -1 | 1): void {
  const actions = [...actionsFor(phase)]
  const targetIndex = index + offset
  if (targetIndex < 0 || targetIndex >= actions.length) return
  const current = actions[index]
  const target = actions[targetIndex]
  if (!current || !target) return
  actions[index] = target
  actions[targetIndex] = current
  writeActions(phase, actions)
  clearActionDrafts(phase)
}

function changeActionType(phase: ActionPhase, index: number, value: string | number | boolean): void {
  const type = String(value) as ActionType
  if (!isAllowedActionType(phase, type)) return
  const actions = actionsFor(phase)
  if (!actions[index]) return
  actions[index] = createAction(type)
  writeActions(phase, actions)
  clearActionDrafts(phase)
}

const deterministicAssertionOptions: { value: DeterministicAssertionType; label: string }[] = [
  { value: 'STATUS_CODE', label: 'Status Code' },
  { value: 'JSONPATH_EQUAL', label: 'JSONPath Equal' },
  { value: 'CONTAINS', label: 'Contains' },
  { value: 'REGEX', label: 'Regex' },
  { value: 'HEADER', label: 'Header' },
  { value: 'COOKIE', label: 'Cookie' },
  { value: 'JSON_SCHEMA', label: 'JSON Schema' },
  { value: 'RESPONSE_TIME', label: 'Response Time' },
  { value: 'EXISTS', label: 'Exists' },
  { value: 'NOT_EXISTS', label: 'Not Exists' },
  { value: 'ARRAY_LENGTH', label: 'Array Length' },
  { value: 'TYPE', label: 'Type' },
]
const assertionSourceOptions: { value: AssertionSource; label: string }[] = [
  { value: 'JSON_BODY', label: 'JSON Body' },
  { value: 'JSONPATH', label: 'JSONPath' },
  { value: 'RESPONSE_TEXT', label: 'Response Text' },
  { value: 'HEADER', label: 'Header' },
  { value: 'COOKIE', label: 'Cookie' },
]
const assertionOperators: { value: AssertionOperator; label: string }[] = [
  { value: 'EQ', label: 'Equal' }, { value: 'CONTAINS', label: 'Contains' },
  { value: 'LT', label: '<' }, { value: 'LTE', label: '<=' },
  { value: 'GT', label: '>' }, { value: 'GTE', label: '>=' },
]

function parseAssertions(raw: string): Assertion[] {
  try {
    const value: unknown = JSON.parse(raw)
    return Array.isArray(value) ? value as Assertion[] : []
  } catch { return [] }
}

const assertions = computed(() => parseAssertions(form.value.assertions))
const assertionsError = computed(() => {
  try {
    const value: unknown = JSON.parse(form.value.assertions)
    return Array.isArray(value) ? '' : 'Assertions 高级 JSON 必须是数组'
  } catch { return 'Assertions 高级 JSON 格式无效，修正后才能保存' }
})

function assertionTypeOf(assertion: Assertion): Assertion['type'] {
  return assertion.type ?? 'STATUS_CODE'
}

function assertionSourceOf(type: Assertion['type']): AssertionSource {
  if (type === 'STATUS_CODE') return 'STATUS_CODE'
  if (type === 'RESPONSE_TIME') return 'RESPONSE_TIME'
  if (type === 'HEADER') return 'HEADER'
  if (type === 'COOKIE') return 'COOKIE'
  if (type === 'JSONPATH_EQUAL' || type === 'JSON_SCHEMA') return 'JSON_BODY'
  if (type === 'EXISTS' || type === 'NOT_EXISTS' || type === 'ARRAY_LENGTH' || type === 'TYPE') return 'JSONPATH'
  return 'RESPONSE_TEXT'
}

function assertionExpectedText(assertion: Assertion): string {
  if (assertion.expected === undefined) return ''
  if (typeof assertion.expected === 'string') return assertion.expected
  try { return JSON.stringify(assertion.expected, null, 2) ?? '' } catch { return String(assertion.expected) }
}

function assertionExpectedDraft(index: number, assertion: Assertion): string {
  const key = String(index)
  return Object.prototype.hasOwnProperty.call(assertionExpectedDrafts, key)
    ? assertionExpectedDrafts[key] ?? ''
    : assertionExpectedText(assertion)
}

function clearAssertionExpectedDrafts(): void {
  for (const key of Object.keys(assertionExpectedDrafts)) delete assertionExpectedDrafts[key]
}

function createAssertion(type: Assertion['type'] = 'STATUS_CODE'): Assertion {
  if (type === 'AI_SEMANTIC') {
    return { kind: 'AI_SEMANTIC', type, name: 'semantic_assertion', enabled: true, prompt_id: null, criteria: '', confidence_threshold: 0.8 }
  }
  const source = assertionSourceOf(type)
  return {
    kind: 'DETERMINISTIC', type, name: `assertion_${assertions.value.length + 1}`, enabled: true,
    source, expression: source === 'JSONPATH' ? '$.data' : source === 'HEADER' || source === 'COOKIE' ? 'Content-Type' : '',
    operator: type === 'CONTAINS' ? 'CONTAINS' : 'EQ',
    expected: type === 'STATUS_CODE' ? 200 : type === 'RESPONSE_TIME' ? 1000 : type === 'JSON_SCHEMA' ? { type: 'object' } : type === 'TYPE' ? 'string' : '',
  }
}

function writeAssertions(value: Assertion[]): void {
  form.value.assertions = JSON.stringify(value, null, 2)
}

function updateAssertionField(index: number, key: string, value: unknown): void {
  const valueList = [...assertions.value]
  const current = valueList[index]
  if (!current) return
  valueList[index] = { ...current, [key]: value } as Assertion
  writeAssertions(valueList)
}

function updateAssertionExpected(index: number, raw: string): void {
  const current = assertions.value[index]
  if (!current) return
  assertionExpectedDrafts[String(index)] = raw
  let value: unknown = raw
  try { value = raw.trim() ? JSON.parse(raw) : '' } catch { /* string expected remains usable */ }
  updateAssertionField(index, 'expected', value)
}

function addAssertion(type: Assertion['type'] = 'STATUS_CODE'): void {
  clearAssertionExpectedDrafts()
  writeAssertions([...assertions.value, createAssertion(type)])
}

function removeAssertion(index: number): void {
  clearAssertionExpectedDrafts()
  writeAssertions(assertions.value.filter((_, itemIndex) => itemIndex !== index))
}

function moveAssertion(index: number, offset: -1 | 1): void {
  clearAssertionExpectedDrafts()
  const valueList = [...assertions.value]
  const targetIndex = index + offset
  if (targetIndex < 0 || targetIndex >= valueList.length) return
  const current = valueList[index]
  const target = valueList[targetIndex]
  if (!current || !target) return
  valueList[index] = target
  valueList[targetIndex] = current
  writeAssertions(valueList)
}

function changeAssertionType(index: number, value: string | number | boolean): void {
  clearAssertionExpectedDrafts()
  const nextType = String(value) as Assertion['type']
  if (nextType === 'AI_SEMANTIC' || deterministicAssertionOptions.some((item) => item.value === nextType)) {
    const current = assertions.value[index]
    if (!current) return
    const next = createAssertion(nextType)
    next.name = current.name
    next.enabled = current.enabled
    const valueList = [...assertions.value]
    valueList[index] = next
    writeAssertions(valueList)
  }
}

function assertionTypeLabel(assertion: Assertion): string {
  if (assertion.type === 'AI_SEMANTIC') return 'AI Semantic'
  return deterministicAssertionOptions.find((item) => item.value === assertion.type)?.label ?? assertion.type
}

function onAdvancedActionsInput(phase: ActionPhase): void {
  const raw = form.value[actionFieldKey(phase)]
  if (!actionJsonError(raw)) clearActionDrafts(phase)
}

async function loadCases(): Promise<void> {
  const currentProjectId = projectId.value
  const identity = readRequestIdentity()
  const sequence = ++casesSequence
  const previousSelectedId = selected.value?.id ?? null
  const restorePreviousSelection = detailVisible.value
  detailSequence += 1
  selected.value = null
  versions.value = []
  focusedVersionId.value = null
  if (route.query.link_source !== 'requirement') reverseExpanded.value = []
  detailVisible.value = false
  if (!currentProjectId || !identity) { cases.value = []; return }
  loading.value = true
  try {
    const result = await getTestCases(currentProjectId)
    if (!alive || sequence !== casesSequence || projectId.value !== currentProjectId || !identityIsCurrent(identity)) return
    cases.value = result
    const deepLinkTargetId = currentProjectId === requestedProjectId ? requestedCaseId : null
    const targetId = deepLinkTargetId ?? (restorePreviousSelection ? previousSelectedId : null)
    const target = targetId ? result.find((item) => item.id === targetId && item.project_id === currentProjectId) : null
    if (target) await showDetail(target, deepLinkTargetId ? requestedVersionId : null)
    else if (deepLinkTargetId) deepLinkNotice.value = '需求关联定位的原 TestCase 当前不可用；未显示其他资产的关联。'
  } catch (error) {
    if (alive && sequence === casesSequence && identityIsCurrent(identity)) {
      ElMessage.error(apiErrorMessage(error, '测试用例加载失败'))
    }
  } finally {
    if (alive && sequence === casesSequence && identityIsCurrent(identity)) loading.value = false
  }
}

function commitPendingJsonDrafts(): string {
  for (const draftKey of Object.keys(actionJsonDrafts)) {
    const [phase, indexText, key] = draftKey.split(':')
    const index = Number(indexText)
    if ((phase !== 'pre' && phase !== 'post') || !Number.isInteger(index) || !key) continue
    commitJsonField(phase, index, key)
    const error = actionJsonErrors[draftKey]
    if (error) return error
  }
  return firstActionJsonError()
}

function toContent(): SuggestedCase {
  const pendingJsonError = commitPendingJsonDrafts()
  if (pendingJsonError) throw new Error(`请先修正动作 JSON：${pendingJsonError}`)
  const steps = JSON.parse(form.value.steps) as SuggestedStep[]
  const testData = JSON.parse(form.value.test_data) as Record<string, unknown>
  if (!Array.isArray(steps) || !steps.length) throw new Error('步骤至少需要一项')
  const retryPolicy = normalizeRetryPolicy(form.value.retry_policy)
  if (form.value.case_type === 'API') {
    const retryError = retryPolicyValidationMessage(retryPolicy)
    if (retryError) throw new Error(retryError)
  }
  let request: ApiRequestTemplate | null = null
  if (form.value.case_type === 'API') {
    const bodyContent = form.value.request_body_type === 'NONE'
      ? null
      : ['JSON', 'FORM_URLENCODED', 'MULTIPART'].includes(form.value.request_body_type)
        ? JSON.parse(form.value.request_body)
        : form.value.request_body
    request = {
      method: form.value.request_method, url: form.value.request_url.trim(),
      query_params: JSON.parse(form.value.request_query) as RequestValueItem[],
      headers: JSON.parse(form.value.request_headers) as RequestValueItem[],
      cookies: JSON.parse(form.value.request_cookies) as RequestValueItem[],
      body: { type: form.value.request_body_type, content: bodyContent },
      auth: {
        type: form.value.auth_type, token: form.value.auth_token || null,
        username: form.value.auth_username || null, password: form.value.auth_password || null,
        key_name: form.value.auth_key_name || null, key_value: form.value.auth_key_value || null,
        placement: form.value.auth_placement,
      },
      timeout_ms: form.value.timeout_ms, follow_redirects: form.value.follow_redirects,
      retry_policy: retryPolicy,
    }
  }
  if (preActionsError.value || postActionsError.value) throw new Error('请先修正 Pre/Post Actions 的高级 JSON')
  if (form.value.case_type === 'API' && assertionsError.value) throw new Error(assertionsError.value)
  if (cleanupError.value) throw new Error(cleanupError.value)
  const draftError = firstActionJsonError()
  if (draftError) throw new Error(`请先修正动作 JSON：${draftError}`)
  const preActions = JSON.parse(form.value.pre_actions) as PreAction[]
  const postActions = JSON.parse(form.value.post_actions) as PostAction[]
  const configuredAssertions = form.value.case_type === 'API'
    ? JSON.parse(form.value.assertions) as Assertion[]
    : []
  const configuredCleanup = form.value.case_type === 'API'
    ? parseCleanups(form.value.cleanup).map(serializeCleanup)
    : []
  const structuredError = validateStructuredActionValues('pre', preActions)
    || validateStructuredActionValues('post', postActions)
  if (structuredError) throw new Error(structuredError)
  let dataSource: SuggestedCase['data_source'] = null
  if (form.value.data_source_dataset_id) {
    const mapping = parseJsonObject(form.value.data_source_mapping, '数据集列映射')
    dataSource = {
      dataset_id: form.value.data_source_dataset_id,
      dataset_version_id: form.value.data_source_version_id || null,
      prefix: form.value.data_source_prefix.trim(),
      column_mapping: mapping,
    }
  }
  return {
    title: form.value.title.trim(), case_type: form.value.case_type,
    priority: form.value.priority,
    preconditions: form.value.preconditions.split('\n').map((item) => item.trim()).filter(Boolean),
    steps, test_data: testData, expected_result: form.value.expected_result.trim(),
    tags: form.value.tags.split(',').map((item) => item.trim()).filter(Boolean),
    confidence: form.value.confidence,
    request,
    pre_actions: preActions,
    extractors: JSON.parse(form.value.extractors) as ResponseExtractor[],
    post_actions: postActions,
    data_source: dataSource,
    assertions: configuredAssertions,
    cleanup: configuredCleanup,
  }
}

function parseJsonObject(raw: string, label: string): Record<string, string> {
  let value: unknown
  try { value = JSON.parse(raw) as unknown } catch { throw new Error(`${label}必须是有效 JSON`) }
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new Error(`${label}必须是 JSON 对象`)
  for (const [key, item] of Object.entries(value)) {
    if (typeof item !== 'string' || !key.trim()) throw new Error(`${label}必须是“源列”:“目标列”对象`)
  }
  return value as Record<string, string>
}

function selectEditorStep(step: CaseEditorStep): void {
  if (!caseEditorSteps.value.some((item) => item.key === step)) return
  editorStep.value = step
}

function moveEditorStep(offset: -1 | 1): void {
  const next = caseEditorSteps.value[editorStepIndex.value + offset]
  if (next) editorStep.value = next.key
}

async function openCreate(): Promise<void> {
  editingId.value = undefined
  clearActionDrafts()
  clearAssertionExpectedDrafts()
  form.value = emptyForm()
  createRequirementIds.value = []
  editorStep.value = 'basic'
  editorVisible.value = true
  const currentProjectId = projectId.value
  if (!currentProjectId) return
  requirementsLoading.value = true
  try {
    const items = await getRequirementTree(currentProjectId)
    if (projectId.value === currentProjectId && editorVisible.value && !editingId.value) {
      requirementOptions.value = items
    }
  } catch {
    if (projectId.value === currentProjectId) {
      requirementOptions.value = []
      ElMessage.error('关联需求加载失败，仍可创建不关联需求的用例')
    }
  } finally {
    if (projectId.value === currentProjectId) requirementsLoading.value = false
  }
}

function openVersion(): void {
  const content = versions.value[0]?.content
  if (!selected.value || !content) return
  editingId.value = selected.value.id
  clearActionDrafts()
  clearAssertionExpectedDrafts()
  const request = content.request
  form.value = {
    title: content.title, case_type: content.case_type, priority: content.priority,
    preconditions: content.preconditions.join('\n'), steps: JSON.stringify(content.steps, null, 2),
    test_data: JSON.stringify(content.test_data, null, 2), expected_result: content.expected_result,
    tags: content.tags.join(', '), confidence: content.confidence, change_note: '',
    request_method: request?.method ?? 'GET', request_url: request?.url ?? '{{base_url}}/',
    request_query: JSON.stringify(request?.query_params ?? [], null, 2),
    request_headers: JSON.stringify(request?.headers ?? [], null, 2),
    request_cookies: JSON.stringify(request?.cookies ?? [], null, 2),
    request_body_type: request?.body.type ?? 'NONE',
    request_body: request?.body.type && ['JSON', 'FORM_URLENCODED', 'MULTIPART'].includes(request.body.type)
      ? JSON.stringify(request.body.content, null, 2)
      : String(request?.body.content ?? ''),
    auth_type: request?.auth.type ?? 'NONE', auth_token: request?.auth.token ?? '',
    auth_username: request?.auth.username ?? '', auth_password: request?.auth.password ?? '',
    auth_key_name: request?.auth.key_name ?? '', auth_key_value: request?.auth.key_value ?? '',
    auth_placement: request?.auth.placement ?? 'HEADER', timeout_ms: request?.timeout_ms ?? 30000,
    follow_redirects: request?.follow_redirects ?? true,
    retry_policy: normalizeRetryPolicy(request?.retry_policy),
    pre_actions: JSON.stringify(content.pre_actions ?? [], null, 2),
    extractors: JSON.stringify(content.extractors ?? [], null, 2),
    post_actions: JSON.stringify(content.post_actions ?? [], null, 2),
    data_source_dataset_id: content.data_source?.dataset_id ?? null,
    data_source_version_id: content.data_source?.dataset_version_id ?? null,
    data_source_prefix: content.data_source?.prefix ?? '',
    data_source_mapping: JSON.stringify(content.data_source?.column_mapping ?? {}, null, 2),
    assertions: JSON.stringify(content.assertions ?? [], null, 2),
    cleanup: JSON.stringify(content.cleanup ?? [], null, 2),
  }
  editorStep.value = 'basic'
  editorVisible.value = true
}

async function save(): Promise<void> {
  if (!projectId.value || !form.value.title.trim() || !form.value.expected_result.trim()) {
    ElMessage.warning('请填写名称和预期结果')
    return
  }
  saving.value = true
  try {
    const content = toContent()
    if (editingId.value) {
      if (!form.value.change_note.trim()) throw new Error('创建新版本时必须填写变更说明')
      await createTestCaseVersion(editingId.value, content, form.value.change_note)
      ElMessage.success('新版本已创建，旧版本保持不变')
    } else {
      await createTestCase({
        project_id: projectId.value,
        content,
        requirement_ids: createRequirementIds.value,
        change_note: form.value.change_note,
      })
      ElMessage.success('测试用例 V1 已创建')
    }
    editorVisible.value = false
    await loadCases()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '保存失败'))
  } finally { saving.value = false }
}

async function showDetail(row: TestCaseAsset, preferredVersionId: number | null = null): Promise<void> {
  const currentProjectId = projectId.value
  const identity = readRequestIdentity()
  const sequence = ++detailSequence
  selected.value = null
  versions.value = []
  focusedVersionId.value = null
  if (!currentProjectId || !identity || row.project_id !== currentProjectId) return
  selected.value = row
  detailVisible.value = true
  try {
    const result = await getTestCaseVersions(row.id)
    if (
      !alive || sequence !== detailSequence || !identityIsCurrent(identity)
      || projectId.value !== currentProjectId || selected.value?.id !== row.id
    ) return
    versions.value = result.filter((item) => item.case_id === row.id)
    focusedVersionId.value = preferredVersionId && versions.value.some((item) => item.id === preferredVersionId)
      ? preferredVersionId
      : row.current_version_id
    if (preferredVersionId) {
      deepLinkNotice.value = focusedVersionId.value === preferredVersionId
        ? `已从需求关联定位到测试用例「${row.name}」的指定版本；资产种类固定为测试用例。`
        : `已定位测试用例「${row.name}」，但关联记录的历史版本当前未返回。`
    }
  } catch (error) {
    if (alive && sequence === detailSequence && identityIsCurrent(identity)) {
      versions.value = []
      ElMessage.error(apiErrorMessage(error, '测试用例版本加载失败'))
    }
  }
}

async function archiveSelected(): Promise<void> {
  if (!selected.value) return
  await ElMessageBox.confirm('归档后不能再创建新版本，确认继续？', '归档测试用例', { type: 'warning' })
  await archiveTestCase(selected.value.id)
  ElMessage.success('测试用例已归档')
  detailVisible.value = false
  await loadCases()
}

function openRuntimePreview(): void {
  runtimeResult.value = null
  try {
    const parsed = JSON.parse(runtimeResponse.value) as RuntimeResponseSnapshot
    runtimeResponseTime.value = parsed.response_time_ms ?? parsed.elapsed_ms ?? 0
  } catch { runtimeResponseTime.value = 0 }
  runtimeVisible.value = true
}

async function previewRuntime(): Promise<void> {
  runtimeLoading.value = true
  try {
    const currentProjectId = projectId.value
    if (!currentProjectId) throw new Error('当前项目未选择，请先选择项目')
    const content = toContent()
    if (!content.request) throw new Error('当前不是 API 用例')
    const response = JSON.parse(runtimeResponse.value) as RuntimeResponseSnapshot
    response.response_time_ms = runtimeResponseTime.value
    response.elapsed_ms = runtimeResponseTime.value
    runtimeResult.value = await previewCaseRuntime({
      project_id: currentProjectId,
      request: content.request,
      context: JSON.parse(runtimeContext.value) as Record<string, unknown>,
      pre_actions: content.pre_actions ?? [], response,
      extractors: content.extractors ?? [], post_actions: content.post_actions ?? [],
      assertions: content.assertions ?? [],
    })
  } catch (error) {
    runtimeResult.value = null
    ElMessage.error(error instanceof Error ? error.message : '运行时预览失败')
  } finally { runtimeLoading.value = false }
}

async function previewCaseDataset(): Promise<void> {
  if (!form.value.data_source_dataset_id) { ElMessage.warning('请先选择数据集'); return }
  dataPreviewLoading.value = true
  try {
    dataPreview.value = await previewDatasetIterations({
      dataset_id: form.value.data_source_dataset_id,
      dataset_version_id: form.value.data_source_version_id || undefined,
      prefix: form.value.data_source_prefix.trim(),
      column_mapping: parseJsonObject(form.value.data_source_mapping, '数据集列映射'),
      limit: 10,
      offset: 0,
    })
    dataPreviewVisible.value = true
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : 'Iteration 预览失败')
  } finally { dataPreviewLoading.value = false }
}

async function loadCaseDatasets(): Promise<void> {
  if (!projectId.value) { datasets.value = []; return }
  try { datasets.value = (await getDatasets(projectId.value)).items }
  catch (error) { ElMessage.error(apiErrorMessage(error, '数据集加载失败')) }
}

async function loadAiAssertionPrompts(): Promise<void> {
  try {
    aiAssertionPrompts.value = await getPrompts(false, 'AI_ASSERTION')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, 'AI_ASSERTION Prompt 加载失败'))
    aiAssertionPrompts.value = []
  }
}

function onIdentityStorage(event: StorageEvent): void {
  if (!isIdentityStorageEvent(event)) return
  casesSequence += 1
  detailSequence += 1
  cases.value = []
  selected.value = null
  versions.value = []
  focusedVersionId.value = null
  detailVisible.value = false
  deepLinkNotice.value = '登录身份已变化，旧身份的用例与需求关联已清除。'
}

onMounted(async () => {
  window.addEventListener('storage', onIdentityStorage)
  const identity = readRequestIdentity()
  if (!identity) return
  const result = await getProjects(true)
  if (!alive || !identityIsCurrent(identity)) return
  projects.value = result.items
  projectId.value = requestedProjectId && projects.value.some((item) => item.id === requestedProjectId)
    ? requestedProjectId
    : projects.value[0]?.id
  await Promise.all([loadCaseDatasets(), loadAiAssertionPrompts()])
  if (route.query.create === '1' && currentProject.value?.status === 'ACTIVE') void openCreate()
})
watch(projectId, () => {
  requirementOptions.value = []
  createRequirementIds.value = []
  void loadCases()
  void loadCaseDatasets()
})
watch(() => form.value.case_type, () => {
  if (!caseEditorSteps.value.some((item) => item.key === editorStep.value)) editorStep.value = 'basic'
})
onBeforeUnmount(() => {
  alive = false
  casesSequence += 1
  detailSequence += 1
  window.removeEventListener('storage', onIdentityStorage)
})
</script>

<template>
  <div class="case-page">
    <header class="page-heading case-heading">
      <div><span class="eyebrow dark">测试资产中心</span><h1>测试用例</h1><p>统一管理人工与 AI 采纳的测试资产，所有修改均生成不可变版本。</p></div>
      <div class="case-actions">
        <el-select v-model="projectId" placeholder="选择项目" style="width: 220px"><el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" /></el-select>
        <el-button :icon="Refresh" :disabled="!projectId" @click="loadCases">刷新</el-button>
        <el-button type="primary" :icon="Plus" :disabled="!projectId || currentProject?.status !== 'ACTIVE'" @click="openCreate">新建用例</el-button>
      </div>
    </header>

    <el-alert v-if="deepLinkNotice" :title="deepLinkNotice" type="info" :closable="false" show-icon />

    <section class="case-summary"><div><strong>{{ activeCount }}</strong><span>有效用例</span></div><div><strong>{{ cases.length - activeCount }}</strong><span>已归档</span></div><div><strong>{{ cases.filter((item) => item.source === 'AI').length }}</strong><span>AI 生成</span></div></section>
    <el-card v-loading="loading" shadow="never">
      <el-table :data="pagedCases" stripe @row-click="(row: TestCaseAsset) => showDetail(row)">
        <el-table-column prop="code" label="编号" width="130" />
        <el-table-column prop="name" label="用例名称" min-width="260" />
        <el-table-column prop="case_type" label="类型" width="100" />
        <el-table-column label="来源" width="110"><template #default="{ row }"><el-tag :type="row.source === 'AI' ? 'success' : 'info'">{{ row.source === 'AI' ? 'AI 生成' : '人工创建' }}</el-tag></template></el-table-column>
        <el-table-column label="状态" width="110"><template #default="{ row }"><el-tag :type="row.status === 'ACTIVE' ? 'primary' : 'info'">{{ row.status === 'ACTIVE' ? '有效' : '已归档' }}</el-tag></template></el-table-column>
        <el-table-column label="最近更新" width="190"><template #default="{ row }">{{ formatApiDateTime(row.updated_at) }}</template></el-table-column>
      </el-table>
      <el-empty v-if="!loading && !cases.length" description="暂无测试用例，可人工创建或从需求 AI 建议中采纳" />
      <el-pagination v-if="caseTotal" class="records-pagination" background layout="total, sizes, prev, pager, next" :current-page="casePage" :page-size="casePageSize" :page-sizes="[...RECORD_PAGE_SIZES]" :total="caseTotal" @current-change="changeCasePage" @size-change="changeCasePageSize" />
    </el-card>

    <el-dialog v-model="editorVisible" :title="editingId ? '创建用例新版本' : '新建测试用例'" width="1180px" class="case-editor-dialog" destroy-on-close>
      <el-form label-position="top" class="case-editor-form">
        <div class="case-editor-shell">
          <nav class="case-editor-nav" aria-label="用例配置步骤">
            <div class="case-editor-nav-title">配置步骤</div>
            <button v-for="(step, index) in caseEditorSteps" :key="step.key" type="button" class="case-editor-nav-item" :class="{ active: editorStep === step.key }" :aria-current="editorStep === step.key ? 'step' : undefined" @click="selectEditorStep(step.key)">
              <span class="case-editor-nav-index">{{ index + 1 }}</span>
              <span><strong>{{ step.title }}</strong><small>{{ step.description }}</small></span>
            </button>
          </nav>
          <main class="case-editor-workspace">
            <header class="case-editor-workspace-heading">
              <div><span>步骤 {{ editorStepIndex + 1 }} / {{ caseEditorSteps.length }}</span><h3>{{ editorStepMeta?.title }}</h3><p>{{ editorStepMeta?.description }}</p></div>
              <el-tag v-if="form.case_type === 'API'" effect="plain">API Case</el-tag>
              <el-tag v-else type="info" effect="plain">{{ form.case_type === 'WEB' ? 'Web 用例' : '手工用例' }}</el-tag>
            </header>
            <section v-show="editorStep === 'basic'" class="case-editor-step-panel">
              <el-form-item label="用例名称"><el-input v-model="form.title" maxlength="255" placeholder="例如：Demo 健康检查" /></el-form-item>
              <el-form-item v-if="!editingId" label="关联需求（可选）">
                <el-select v-model="createRequirementIds" v-loading="requirementsLoading" multiple filterable collapse-tags collapse-tags-tooltip clearable placeholder="选择本用例覆盖的一个或多个需求">
                  <el-option v-for="item in flatRequirementOptions" :key="item.id" :label="`${item.code} · ${item.title}`" :value="item.id" :disabled="item.status !== 'ACTIVE' || !item.current_version_id" />
                </el-select>
                <span class="muted requirement-link-help">人工用例可在创建时建立覆盖关联；AI 用例会自动关联发起设计的需求。创建后两者进入相同的版本、运行和归档流程。</span>
              </el-form-item>
              <div class="form-grid"><el-form-item label="类型"><el-select v-model="form.case_type"><el-option label="API" value="API" /><el-option label="Web" value="WEB" /><el-option label="手工" value="MANUAL" /></el-select></el-form-item><el-form-item label="优先级"><el-select v-model="form.priority"><el-option v-for="item in ['P0', 'P1', 'P2', 'P3']" :key="item" :label="item" :value="item" /></el-select></el-form-item><el-form-item label="置信度"><el-input-number v-model="form.confidence" :min="0" :max="1" :step="0.05" /></el-form-item></div>
              <el-form-item label="前置条件（每行一项）"><el-input v-model="form.preconditions" type="textarea" :rows="3" placeholder="没有前置条件时可以留空" /></el-form-item>
              <el-form-item label="步骤 JSON"><el-input v-model="form.steps" type="textarea" :rows="7" class="code-input" /></el-form-item>
              <el-form-item label="测试数据 JSON"><el-input v-model="form.test_data" type="textarea" :rows="4" class="code-input" /></el-form-item>
            </section>
            <template v-if="form.case_type === 'API'">
              <section v-show="editorStep === 'request'" class="case-editor-step-panel">
          <el-divider content-position="left">API 请求模板</el-divider>
          <div class="request-line"><el-form-item label="方法"><el-select v-model="form.request_method"><el-option v-for="item in ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']" :key="item" :value="item" /></el-select></el-form-item><el-form-item label="URL（支持 Runtime Context 变量）"><el-input v-model="form.request_url" placeholder="{{base_url}}/api/orders" /></el-form-item></div>
          <el-tabs type="border-card">
            <el-tab-pane label="Query"><el-input v-model="form.request_query" type="textarea" :rows="6" class="code-input" /></el-tab-pane>
            <el-tab-pane label="Headers"><el-input v-model="form.request_headers" type="textarea" :rows="6" class="code-input" /></el-tab-pane>
            <el-tab-pane label="Cookies"><el-input v-model="form.request_cookies" type="textarea" :rows="6" class="code-input" /></el-tab-pane>
            <el-tab-pane label="Body"><div class="body-editor"><el-select v-model="form.request_body_type"><el-option v-for="item in ['NONE', 'JSON', 'FORM_URLENCODED', 'MULTIPART', 'RAW']" :key="item" :value="item" /></el-select><el-input v-if="form.request_body_type !== 'NONE'" v-model="form.request_body" type="textarea" :rows="7" class="code-input" /></div></el-tab-pane>
            <el-tab-pane label="认证"><div class="auth-editor"><el-select v-model="form.auth_type"><el-option label="无认证" value="NONE" /><el-option label="Bearer Token" value="BEARER" /><el-option label="基础认证" value="BASIC" /><el-option label="API Key" value="API_KEY" /></el-select><el-input v-if="form.auth_type === 'BEARER'" v-model="form.auth_token" placeholder="Token 或 {{secret.token}}" /><template v-if="form.auth_type === 'BASIC'"><el-input v-model="form.auth_username" placeholder="用户名" /><el-input v-model="form.auth_password" show-password placeholder="密码或密钥模板" /></template><template v-if="form.auth_type === 'API_KEY'"><el-input v-model="form.auth_key_name" placeholder="参数名称" /><el-input v-model="form.auth_key_value" show-password placeholder="值或密钥模板" /><el-radio-group v-model="form.auth_placement"><el-radio-button value="HEADER">请求头</el-radio-button><el-radio-button value="QUERY">查询参数</el-radio-button></el-radio-group></template></div></el-tab-pane>
          </el-tabs>
          <div class="request-settings"><el-form-item label="超时（毫秒）"><el-input-number v-model="form.timeout_ms" :min="100" :max="120000" :step="1000" /></el-form-item><el-checkbox v-model="form.follow_redirects">跟随重定向</el-checkbox></div>
          <section class="retry-policy-panel">
            <div class="retry-policy-heading"><div><strong>失败重试</strong><span class="muted">属于当前用例版本，运行时不会临时覆盖</span></div><el-tag :type="retryPolicyNeedsCorrection ? 'danger' : form.retry_policy.max_retries === 0 ? 'info' : 'warning'" effect="plain">{{ retryPolicySummary(form.retry_policy.max_retries) }}</el-tag></div>
            <el-alert title="V1 只允许 0 或 1：1 表示首次执行之外最多额外重试一次，总尝试最多两次。历史 2/3 会原样显示，必须显式选择 0/1 后才能创建新版本。" :type="retryPolicyNeedsCorrection ? 'error' : 'warning'" :closable="false" show-icon />
            <div class="retry-policy-grid"><el-form-item label="最大额外重试次数" :error="retryPolicyNeedsCorrection ? '必须显式选择数值 0 或 1' : ''"><el-select v-model="form.retry_policy.max_retries"><el-option label="0 · 不重试" :value="0" /><el-option label="1 · 最多额外一次（总尝试最多两次）" :value="1" /><el-option v-if="retryPolicyNeedsCorrection" :key="`legacy-${retryCountDisplay(form.retry_policy.max_retries)}`" :label="`历史值 ${retryCountDisplay(form.retry_policy.max_retries)} · 不可用于新版本`" :value="form.retry_policy.max_retries" disabled /></el-select></el-form-item><el-form-item label="重试间隔（毫秒）"><el-input-number v-model="form.retry_policy.backoff_ms" :min="0" :max="30000" :step="100" :disabled="form.retry_policy.max_retries === 0" /></el-form-item></div>
            <el-form-item label="遇到以下情况时重试" :error="retryConditionsError"><el-checkbox-group v-model="form.retry_policy.retry_on" :disabled="form.retry_policy.max_retries === 0" class="retry-policy-options"><el-checkbox v-for="option in retryConditionOptions" :key="option.value" :label="option.value">{{ option.label }}</el-checkbox></el-checkbox-group><span v-if="form.retry_policy.max_retries === 0" class="muted">当前未开启重试，以下条件不会生效。</span><span v-else class="muted">至少选择一项，重试才会按所选情况触发。</span></el-form-item>
          </section>
              </section>
              <section v-show="editorStep === 'actions'" class="case-editor-step-panel">
          <el-divider content-position="left">运行时动作与提取</el-divider>
          <el-tabs v-model="actionPhaseTab" type="border-card" class="action-tabs"><el-tab-pane label="Pre Actions" name="pre" /><el-tab-pane label="Post Actions" name="post" /></el-tabs>
          <div v-for="phase in actionPhases" v-show="actionPhaseTab === phase" :key="phase" class="action-editor">
            <div class="action-editor-heading"><div><strong>{{ phase === 'pre' ? 'Pre Actions' : 'Post Actions' }}</strong><span class="muted">{{ actionsFor(phase).length }} 个动作，按顺序执行</span></div><el-button type="primary" plain :icon="Plus" @click="addAction(phase)">添加动作</el-button></div>
            <el-alert v-if="(phase === 'pre' ? preActionsError : postActionsError)" :title="phase === 'pre' ? preActionsError : postActionsError" type="warning" :closable="false" show-icon />
            <el-empty v-if="!actionsFor(phase).length" description="暂无动作，点击右上角添加" />
            <div v-for="(action, index) in actionsFor(phase)" :key="`${phase}-${index}`" class="action-card">
              <div class="action-card-heading">
                <div class="action-card-title"><span class="action-index">{{ index + 1 }}</span><el-select :model-value="actionTypeOf(action)" style="width: 210px" @update:model-value="changeActionType(phase, index, $event)"><el-option v-for="option in actionOptionsFor(phase)" :key="option.value" :label="option.label" :value="option.value" /></el-select><el-tag v-if="isLegacyVariableAction(action)" type="warning" effect="plain">兼容旧结构</el-tag><el-tag v-else type="info" effect="plain">{{ actionTypeLabel(action) }}</el-tag></div>
                <div class="action-card-tools"><span class="enabled-label">启用 <el-switch :model-value="actionBoolean(action, 'enabled')" @update:model-value="updateActionField(phase, index, 'enabled', $event)" /></span><el-button text :icon="ArrowUp" :disabled="index === 0" title="上移" @click="moveAction(phase, index, -1)" /><el-button text :icon="ArrowDown" :disabled="index === actionsFor(phase).length - 1" title="下移" @click="moveAction(phase, index, 1)" /><el-button text type="danger" :icon="Delete" title="删除" @click="removeAction(phase, index)" /></div>
              </div>

              <div v-if="actionTypeOf(action) === 'SET_VARIABLE'" class="action-fields">
                <el-form-item label="变量名"><el-input :model-value="actionText(action, 'name')" placeholder="user_id" @update:model-value="updateActionField(phase, index, 'name', $event)" /></el-form-item>
                <el-form-item label="值（支持字符串或 JSON）"><el-input :model-value="actionJsonText(phase, index, 'value', action)" type="textarea" :rows="2" class="code-input" @update:model-value="updateJsonDraft(phase, index, 'value', $event)" @blur="commitJsonField(phase, index, 'value')" /></el-form-item>
              </div>

              <div v-else-if="actionTypeOf(action) === 'FAKER'" class="action-fields">
                <el-form-item label="变量名"><el-input :model-value="actionText(action, 'name')" placeholder="email" @update:model-value="updateActionField(phase, index, 'name', $event)" /></el-form-item>
                <el-form-item label="生成器"><el-select :model-value="actionText(action, 'generator')" @update:model-value="updateActionField(phase, index, 'generator', $event)"><el-option v-for="generator in ['uuid', 'email', 'username', 'first_name', 'last_name', 'integer', 'word', 'boolean']" :key="generator" :label="generator" :value="generator" /></el-select></el-form-item>
                <el-form-item label="Seed（可选）"><el-input-number :model-value="actionNumber(action, 'seed')" :controls="false" @update:model-value="updateOptionalNumber(phase, index, 'seed', $event)" /></el-form-item>
              </div>

              <div v-else-if="actionTypeOf(action) === 'SQL_QUERY'" class="action-fields action-fields-wide">
                <el-form-item label="Connection ID"><el-input-number :model-value="actionNumber(action, 'connection_id')" :min="1" @update:model-value="updateOptionalNumber(phase, index, 'connection_id', $event)" /></el-form-item>
                <el-form-item label="结果变量"><el-input :model-value="actionText(action, 'result_variable')" placeholder="sql_rows" @update:model-value="updateActionField(phase, index, 'result_variable', $event)" /></el-form-item>
                <el-form-item label="最大行数"><el-input-number :model-value="actionNumber(action, 'max_rows')" :min="1" :max="1000" @update:model-value="updateOptionalNumber(phase, index, 'max_rows', $event)" /></el-form-item>
                <el-form-item label="SQL" class="field-span-all"><el-input :model-value="actionText(action, 'sql')" type="textarea" :rows="3" class="code-input" @update:model-value="updateActionField(phase, index, 'sql', $event)" /></el-form-item>
                <el-form-item label="Params JSON" class="field-span-all" :error="actionJsonDraftError(phase, index, 'params')"><el-input :model-value="actionJsonText(phase, index, 'params', action)" type="textarea" :rows="2" class="code-input" @update:model-value="updateJsonDraft(phase, index, 'params', $event)" @blur="commitJsonField(phase, index, 'params')" /></el-form-item>
              </div>

              <div v-else-if="actionTypeOf(action) === 'PYTHON_SCRIPT'" class="action-fields-one"><el-form-item label="Python 脚本"><el-input :model-value="actionText(action, 'script')" type="textarea" :rows="5" class="code-input" placeholder="填写受运行器支持的脚本" @update:model-value="updateActionField(phase, index, 'script', $event)" /></el-form-item></div>

              <div v-else-if="['GET_TOKEN', 'API_SETUP'].includes(actionTypeOf(action))" class="action-fields action-fields-wide">
                <el-form-item label="变量名"><el-input :model-value="actionText(action, 'name')" placeholder="access_token" @update:model-value="updateActionField(phase, index, 'name', $event)" /></el-form-item>
                <el-form-item label="来源"><el-select :model-value="actionText(action, 'source')" @update:model-value="updateActionField(phase, index, 'source', $event)"><el-option label="JSONPATH" value="JSONPATH" /><el-option label="HEADER" value="HEADER" /><el-option label="COOKIE" value="COOKIE" /></el-select></el-form-item>
                <el-form-item label="表达式"><el-input :model-value="actionText(action, 'expression')" placeholder="$.access_token 或 Authorization" @update:model-value="updateActionField(phase, index, 'expression', $event)" /></el-form-item>
                <el-form-item label="默认值"><el-input :model-value="actionJsonText(phase, index, 'default_value', action)" type="textarea" :rows="2" class="code-input" @update:model-value="updateJsonDraft(phase, index, 'default_value', $event)" @blur="commitJsonField(phase, index, 'default_value')" /></el-form-item>
                <el-form-item label="必须提取"><el-switch :model-value="actionBoolean(action, 'required')" @update:model-value="updateActionField(phase, index, 'required', $event)" /></el-form-item>
                <el-form-item :label="actionTypeOf(action) === 'GET_TOKEN' ? '正式登录 Request' : '正式数据准备 Request'" class="field-span-all" :error="actionJsonDraftError(phase, index, 'request')"><el-input :model-value="actionJsonText(phase, index, 'request', action)" type="textarea" :rows="8" class="code-input" @update:model-value="updateJsonDraft(phase, index, 'request', $event)" @blur="commitJsonField(phase, index, 'request')" /></el-form-item>
                <el-form-item label="预览用模拟 Response" class="field-span-all" :error="actionJsonDraftError(phase, index, 'response')"><el-input :model-value="actionJsonText(phase, index, 'response', action)" type="textarea" :rows="5" class="code-input" @update:model-value="updateJsonDraft(phase, index, 'response', $event)" @blur="commitJsonField(phase, index, 'response')" /></el-form-item>
              </div>

              <div v-else-if="actionTypeOf(action) === 'EXTRACT_RESPONSE'" class="action-fields action-fields-wide">
                <el-form-item label="变量名"><el-input :model-value="actionText(action, 'name')" placeholder="order_id" @update:model-value="updateActionField(phase, index, 'name', $event)" /></el-form-item>
                <el-form-item label="来源"><el-select :model-value="actionText(action, 'source')" @update:model-value="updateActionField(phase, index, 'source', $event)"><el-option label="JSONPATH" value="JSONPATH" /><el-option label="HEADER" value="HEADER" /><el-option label="COOKIE" value="COOKIE" /></el-select></el-form-item>
                <el-form-item label="表达式"><el-input :model-value="actionText(action, 'expression')" placeholder="$.data.id" @update:model-value="updateActionField(phase, index, 'expression', $event)" /></el-form-item>
                <el-form-item label="默认值"><el-input :model-value="actionJsonText(phase, index, 'default_value', action)" type="textarea" :rows="2" class="code-input" @update:model-value="updateJsonDraft(phase, index, 'default_value', $event)" @blur="commitJsonField(phase, index, 'default_value')" /></el-form-item>
                <el-form-item label="必须提取"><el-switch :model-value="actionBoolean(action, 'required')" @update:model-value="updateActionField(phase, index, 'required', $event)" /></el-form-item>
              </div>

              <div v-else-if="actionTypeOf(action) === 'REGISTER_RESOURCE'" class="action-fields action-fields-wide">
                <el-form-item label="资源名"><el-input :model-value="actionText(action, 'name')" placeholder="created_order" @update:model-value="updateActionField(phase, index, 'name', $event)" /></el-form-item>
                <el-form-item label="资源类型"><el-input :model-value="actionText(action, 'resource_type')" placeholder="order" @update:model-value="updateActionField(phase, index, 'resource_type', $event)" /></el-form-item>
                <el-form-item label="值"><el-input :model-value="actionJsonText(phase, index, 'value', action)" type="textarea" :rows="2" class="code-input" @update:model-value="updateJsonDraft(phase, index, 'value', $event)" @blur="commitJsonField(phase, index, 'value')" /></el-form-item>
                <el-form-item label="绑定 Cleanup"><el-select :model-value="actionText(action, 'cleanup_ref')" clearable placeholder="可选：选择 Cleanup" @update:model-value="updateActionField(phase, index, 'cleanup_ref', $event || undefined)"><el-option v-for="cleanup in cleanups.filter((item) => item.cleanup_id)" :key="cleanup.cleanup_id" :label="cleanup.cleanup_id ?? ''" :value="cleanup.cleanup_id ?? ''" /></el-select></el-form-item>
                <el-form-item label="Metadata JSON" class="field-span-all" :error="actionJsonDraftError(phase, index, 'metadata')"><el-input :model-value="actionJsonText(phase, index, 'metadata', action)" type="textarea" :rows="3" class="code-input" @update:model-value="updateJsonDraft(phase, index, 'metadata', $event)" @blur="commitJsonField(phase, index, 'metadata')" /></el-form-item>
              </div>

              <el-alert v-else title="当前动作类型不属于此 phase，请通过高级 JSON 修正" type="error" :closable="false" show-icon />
            </div>

            <el-collapse class="advanced-actions"><el-collapse-item title="高级 JSON 查看 / 编辑" :name="phase"><el-alert title="有效 JSON 会同步到结构化编辑器；旧的 { name, value, enabled } 变量对象会原样保留，不会自动补 type。" type="info" :closable="false" show-icon /><el-input v-model="form[actionFieldKey(phase)]" type="textarea" :rows="8" class="code-input advanced-json" @input="onAdvancedActionsInput(phase)" /></el-collapse-item></el-collapse>
          </div>
          <el-divider content-position="left">Cleanup 资源生命周期</el-divider>
          <el-alert title="清理配置会随用例版本固化；密钥只允许引用，不保存明文。注册资源步骤可绑定下面的清理 ID。运行时预览仍只模拟，不会写入资源登记表。" type="info" :closable="false" show-icon />
          <el-alert v-if="cleanupError" :title="cleanupError" type="warning" :closable="false" show-icon />
          <div class="cleanup-editor">
            <div class="action-editor-heading"><div><strong>Cleanup 列表</strong><span class="muted">{{ cleanups.length }} 条，默认按注册顺序的逆序执行</span></div><el-button type="primary" plain :icon="Plus" @click="addCleanup">添加 Cleanup</el-button></div>
            <el-empty v-if="!cleanups.length" description="暂无 Cleanup；需要资源清理时添加并在 REGISTER_RESOURCE 中绑定" />
            <div v-for="(cleanup, index) in cleanups" :key="`${cleanup.cleanup_id ?? 'cleanup'}-${index}`" class="action-card cleanup-card">
              <div class="action-card-heading"><div class="action-card-title"><span class="action-index">{{ index + 1 }}</span><strong>{{ cleanup.cleanup_id || `Cleanup ${index + 1}` }}</strong><el-tag effect="plain">{{ cleanup.cleanup_type }}</el-tag></div><div class="action-card-tools"><span class="enabled-label">启用 <el-switch :model-value="cleanup.enabled" @update:model-value="updateCleanupField(index, 'enabled', $event)" /></span><el-button text :icon="ArrowUp" :disabled="index === 0" @click="moveCleanup(index, -1)" /><el-button text :icon="ArrowDown" :disabled="index === cleanups.length - 1" @click="moveCleanup(index, 1)" /><el-button text type="danger" :icon="Delete" @click="removeCleanup(index)" /></div></div>
              <div class="action-fields action-fields-wide"><el-form-item label="Cleanup ID"><el-input :model-value="cleanup.cleanup_id ?? ''" placeholder="delete_order" @update:model-value="updateCleanupField(index, 'cleanup_id', $event || null)" /></el-form-item><el-form-item label="类型"><el-select :model-value="cleanup.cleanup_type" @update:model-value="updateCleanupField(index, 'cleanup_type', $event)"><el-option label="API Cleanup" value="API" /><el-option label="SQL Cleanup" value="SQL" /></el-select></el-form-item><el-form-item label="策略"><el-select :model-value="cleanup.policy" @update:model-value="updateCleanupField(index, 'policy', $event)"><el-option label="始终" value="ALWAYS" /><el-option label="成功时" value="ON_SUCCESS" /><el-option label="失败/取消/超时时" value="ON_FAILURE" /><el-option label="不清理" value="NEVER" /></el-select></el-form-item><el-form-item label="超时（毫秒）"><el-input-number :model-value="cleanup.timeout_ms" :min="100" :max="120000" @update:model-value="updateCleanupField(index, 'timeout_ms', $event ?? 30000)" /></el-form-item></div>
              <div v-if="cleanup.cleanup_type === 'API'" class="action-fields action-fields-wide"><el-form-item label="方法"><el-select :model-value="cleanup.method" @update:model-value="updateCleanupField(index, 'method', $event)"><el-option v-for="method in ['DELETE', 'POST', 'PUT', 'PATCH']" :key="method" :label="method" :value="method" /></el-select></el-form-item><el-form-item label="URL（支持运行时上下文）" class="field-span-all"><el-input :model-value="cleanup.url ?? ''" placeholder="{{base_url}}/orders/{{resource_id}}" @update:model-value="updateCleanupField(index, 'url', $event)" /></el-form-item><el-form-item label="认证方式"><el-select :model-value="cleanup.auth?.type ?? 'NONE'" @update:model-value="updateCleanupAuth(index, 'type', $event)"><el-option v-for="type in ['NONE', 'BEARER', 'BASIC', 'API_KEY']" :key="type" :label="type" :value="type" /></el-select></el-form-item><el-form-item v-if="cleanup.auth?.type !== 'NONE'" label="密钥 ID（不回显值）"><el-input-number :model-value="cleanup.auth?.secret_id ?? undefined" :min="1" :controls="false" @update:model-value="updateCleanupAuth(index, 'secret_id', $event ?? null)" /></el-form-item><el-form-item v-if="cleanup.auth?.type === 'API_KEY'" label="API Key 名称"><el-input :model-value="cleanup.auth?.key_name ?? ''" @update:model-value="updateCleanupAuth(index, 'key_name', $event)" /></el-form-item><el-form-item label="查询参数 JSON" class="field-span-all"><el-input :model-value="cleanupJsonText(cleanup, 'query_params')" type="textarea" :rows="3" class="code-input" @change="updateCleanupJson(index, 'query_params', $event)" /></el-form-item><el-form-item label="请求头 JSON（敏感值只能填 {{variable}}）" class="field-span-all"><el-input :model-value="cleanupJsonText(cleanup, 'headers')" type="textarea" :rows="3" class="code-input" @change="updateCleanupJson(index, 'headers', $event)" /></el-form-item><el-form-item label="请求体 JSON" class="field-span-all"><el-input :model-value="cleanupJsonText(cleanup, 'body')" type="textarea" :rows="4" class="code-input" @change="updateCleanupJson(index, 'body', $event)" /></el-form-item></div>
              <div v-else class="action-fields action-fields-wide"><el-form-item label="Connection ID"><el-input-number :model-value="cleanup.connection_id ?? undefined" :min="1" :controls="false" @update:model-value="updateCleanupField(index, 'connection_id', $event ?? null)" /></el-form-item><el-form-item label="资源 ID 参数名"><el-input :model-value="cleanup.resource_id_param ?? 'resource_id'" @update:model-value="updateCleanupField(index, 'resource_id_param', $event)" /></el-form-item><el-form-item label="SQL（仅参数化 DML）" class="field-span-all"><el-input :model-value="cleanup.sql ?? ''" type="textarea" :rows="3" class="code-input" @update:model-value="updateCleanupField(index, 'sql', $event)" /></el-form-item><el-form-item label="Params JSON（资源 ID 通过参数绑定）" class="field-span-all"><el-input :model-value="cleanupJsonText(cleanup, 'params')" type="textarea" :rows="3" class="code-input" @change="updateCleanupJson(index, 'params', $event)" /></el-form-item></div>
            </div>
          </div>
              </section>
              <section v-show="editorStep === 'verification'" class="case-editor-step-panel">
          <el-divider content-position="left">数据驱动（可选）</el-divider>
          <el-alert title="仅 API 用例使用数据集；每行对应一次迭代，旧用例版本没有此字段时保持兼容。" type="info" :closable="false" show-icon />
          <div class="data-source-grid"><el-form-item label="数据集"><el-select v-model="form.data_source_dataset_id" clearable placeholder="不绑定数据集"><el-option v-for="dataset in datasets.filter((item) => item.status === 'ACTIVE')" :key="dataset.id" :label="dataset.name" :value="dataset.id" /></el-select></el-form-item><el-form-item label="版本（留空使用当前）"><el-input-number v-model="form.data_source_version_id" :min="1" :controls="false" /></el-form-item><el-form-item label="Context 前缀"><el-input v-model="form.data_source_prefix" placeholder="例如 data" /></el-form-item><el-form-item label="列映射 JSON" class="field-span-all"><el-input v-model="form.data_source_mapping" type="textarea" :rows="3" class="code-input" placeholder='{"username": "request.username"}' /></el-form-item></div>
          <el-button :loading="dataPreviewLoading" :disabled="!form.data_source_dataset_id" @click="previewCaseDataset">查看前几条 Iteration Context</el-button>
          <el-divider content-position="left">Assertions（按顺序执行）</el-divider>
          <el-alert title="确定性断言不会调用模型；AI Semantic 只向当前 AI_ASSERTION Prompt 发送脱敏、截断后的响应快照和 criteria。停用断言会标记为 SKIPPED。" type="info" :closable="false" show-icon />
          <div class="assertion-editor">
            <div class="action-editor-heading"><div><strong>断言列表</strong><span class="muted">{{ assertions.length }} 条，保存顺序就是执行顺序</span></div><el-button type="primary" plain :icon="Plus" @click="addAssertion()">添加断言</el-button></div>
            <el-alert v-if="assertionsError" :title="assertionsError" type="warning" :closable="false" show-icon />
            <el-empty v-if="!assertions.length" description="暂无断言，所有断言停用或列表为空时最终状态为 PASS" />
            <div v-for="(assertion, index) in assertions" :key="`${index}-${assertion.name}`" class="action-card assertion-card">
              <div class="action-card-heading"><div class="action-card-title"><span class="action-index">{{ index + 1 }}</span><el-select :model-value="assertionTypeOf(assertion)" style="width: 220px" @update:model-value="changeAssertionType(index, $event)"><el-option label="AI Semantic" value="AI_SEMANTIC" /><el-option v-for="option in deterministicAssertionOptions" :key="option.value" :label="option.label" :value="option.value" /></el-select><el-tag type="info" effect="plain">{{ assertionTypeLabel(assertion) }}</el-tag></div><div class="action-card-tools"><span class="enabled-label">启用 <el-switch :model-value="assertion.enabled !== false" @update:model-value="updateAssertionField(index, 'enabled', $event)" /></span><el-button text :icon="ArrowUp" :disabled="index === 0" title="上移" @click="moveAssertion(index, -1)" /><el-button text :icon="ArrowDown" :disabled="index === assertions.length - 1" title="下移" @click="moveAssertion(index, 1)" /><el-button text type="danger" :icon="Delete" title="删除" @click="removeAssertion(index)" /></div></div>
              <div class="action-fields assertion-fields"><el-form-item label="名称"><el-input :model-value="assertion.name" placeholder="status_is_ok" @update:model-value="updateAssertionField(index, 'name', $event)" /></el-form-item><template v-if="assertion.kind === 'DETERMINISTIC'"><el-form-item v-if="assertion.type !== 'STATUS_CODE' && assertion.type !== 'RESPONSE_TIME' && assertion.type !== 'HEADER' && assertion.type !== 'COOKIE'" label="来源"><el-select :model-value="assertion.source" @update:model-value="updateAssertionField(index, 'source', $event)"><el-option v-for="source in assertionSourceOptions" :key="source.value" :label="source.label" :value="source.value" /></el-select></el-form-item><el-form-item v-if="assertion.type === 'HEADER' || assertion.type === 'COOKIE'" label="名称表达式"><el-input :model-value="assertion.expression" :placeholder="assertion.type === 'HEADER' ? 'Content-Type' : 'session_id'" @update:model-value="updateAssertionField(index, 'expression', $event)" /></el-form-item><el-form-item v-else-if="assertion.type !== 'STATUS_CODE' && assertion.type !== 'RESPONSE_TIME' && assertion.type !== 'JSON_SCHEMA'" label="表达式"><el-input :model-value="assertion.expression" placeholder="$.data.id" @update:model-value="updateAssertionField(index, 'expression', $event)" /></el-form-item><el-form-item v-if="assertion.type !== 'EXISTS' && assertion.type !== 'NOT_EXISTS' && assertion.type !== 'TYPE' && assertion.type !== 'JSON_SCHEMA'" label="操作符"><el-select :model-value="assertion.operator" @update:model-value="updateAssertionField(index, 'operator', $event)"><el-option v-for="operator in assertionOperators" :key="operator.value" :label="operator.label" :value="operator.value" /></el-select></el-form-item><el-form-item v-if="assertion.type === 'JSON_SCHEMA'" label="Schema expected JSON" class="field-span-all"><el-input :model-value="assertionExpectedDraft(index, assertion)" type="textarea" :rows="4" class="code-input" placeholder='例如：{"type":"object"}' @update:model-value="updateAssertionExpected(index, String($event))" /></el-form-item><el-form-item v-else-if="assertion.type !== 'EXISTS' && assertion.type !== 'NOT_EXISTS'" label="Expected（期望值）"><div class="assertion-expected-control"><el-input :model-value="assertionExpectedDraft(index, assertion)" :type="assertion.type === 'REGEX' || assertion.type === 'CONTAINS' || assertion.type === 'HEADER' || assertion.type === 'COOKIE' ? 'text' : 'textarea'" :rows="2" placeholder="字符串可直接输入，例如 v1-demo" @update:model-value="updateAssertionExpected(index, String($event))" /><small>输入会即时保存；数字、布尔值、对象和数组会自动按 JSON 识别。</small></div></el-form-item></template><template v-else><el-form-item label="AI_ASSERTION Prompt"><el-select :model-value="assertion.prompt_id" placeholder="选择启用且类型匹配的 Prompt" @update:model-value="updateAssertionField(index, 'prompt_id', $event)"><el-option v-for="prompt in aiAssertionPrompts.filter((item) => item.enabled && item.current_version)" :key="prompt.id" :label="promptOptionLabel(prompt)" :value="prompt.id" /></el-select></el-form-item><el-form-item label="Criteria" class="field-span-all"><el-input :model-value="assertion.criteria ?? ''" type="textarea" :rows="3" placeholder="说明响应必须满足的业务语义" @update:model-value="updateAssertionField(index, 'criteria', $event)" /></el-form-item><el-form-item label="Confidence Threshold"><el-input-number :model-value="assertion.confidence_threshold ?? 0.8" :min="0" :max="1" :step="0.05" @update:model-value="updateAssertionField(index, 'confidence_threshold', $event)" /></el-form-item></template></div>
            </div>
            <el-collapse><el-collapse-item title="高级 JSON 查看 / 编辑" name="assertions"><el-input v-model="form.assertions" type="textarea" :rows="8" class="code-input" /></el-collapse-item></el-collapse>
          </div>
          <el-form-item label="Extractors JSON"><el-input v-model="form.extractors" type="textarea" :rows="5" class="code-input" /></el-form-item>
          <el-button class="runtime-button" @click="openRuntimePreview">Runtime Context 预览</el-button>
              </section>
            </template>
            <section v-show="editorStep === 'outcome'" class="case-editor-step-panel">
              <el-alert title="完成最后几项说明后即可保存；所有步骤中的内容会一起写入当前用例版本。" type="info" :closable="false" show-icon />
              <el-form-item label="预期结果"><el-input v-model="form.expected_result" type="textarea" :rows="4" placeholder="描述这个用例成功时应看到的业务结果" /></el-form-item>
              <el-form-item label="标签（逗号分隔）"><el-input v-model="form.tags" placeholder="例如：demo, smoke, api" /></el-form-item>
              <el-form-item :label="editingId ? '版本变更说明（必填）' : '创建说明'"><el-input v-model="form.change_note" maxlength="500" :placeholder="editingId ? '说明本版本改了什么' : '例如：人工创建'" /></el-form-item>
            </section>
            <div class="case-editor-step-actions">
              <el-button :disabled="editorIsFirstStep" @click="moveEditorStep(-1)">上一步</el-button>
              <span>{{ editorStepMeta?.title }}</span>
              <el-button v-if="!editorIsLastStep" type="primary" @click="moveEditorStep(1)">下一步</el-button>
              <el-button v-else type="primary" plain :loading="saving" :disabled="form.case_type === 'API' && Boolean(retryPolicyError)" @click="save">检查并保存</el-button>
            </div>
          </main>
        </div>
      </el-form>
      <template #footer><el-button @click="editorVisible = false">取消</el-button><el-button type="primary" :loading="saving" :disabled="form.case_type === 'API' && Boolean(retryPolicyError)" @click="save">{{ editingId ? '创建新版本' : '创建 V1' }}</el-button></template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="测试用例详情与版本" size="58%">
      <template v-if="selected"><div class="drawer-title"><div><code>{{ selected.code }}</code><h2>{{ selected.name }}</h2><el-tag>{{ selected.case_type }}</el-tag><el-tag :type="selected.source === 'AI' ? 'success' : 'info'">{{ selected.source === 'AI' ? 'AI 生成' : '人工创建' }}</el-tag></div><div><el-button v-if="selected.status === 'ACTIVE' && currentProject?.status === 'ACTIVE'" :icon="EditPen" @click="openVersion">创建新版本</el-button><el-button v-if="selected.status === 'ACTIVE' && currentProject?.status === 'ACTIVE'" type="danger" plain @click="archiveSelected">归档</el-button></div></div><el-timeline><el-timeline-item v-for="version in caseVersionPages.items.value" :key="version.id" :timestamp="formatApiDateTime(version.created_at)" placement="top"><el-card shadow="never" :class="{ 'focused-version-card': version.id === focusedVersionId }"><template #header><strong>V{{ version.version_no }} · {{ version.change_note || '无变更说明' }}</strong><el-tag v-if="version.id === focusedVersionId" size="small">关联定位版本</el-tag></template><el-descriptions :column="2" border><el-descriptions-item label="优先级">{{ version.content.priority }}</el-descriptions-item><el-descriptions-item label="类型">{{ version.content.case_type }}</el-descriptions-item><el-descriptions-item v-if="version.content.request" label="请求" :span="2"><el-tag>{{ version.content.request.method }}</el-tag> <code>{{ version.content.request.url }}</code></el-descriptions-item><el-descriptions-item label="预期结果" :span="2">{{ version.content.expected_result }}</el-descriptions-item></el-descriptions><pre v-if="version.content.request">{{ JSON.stringify(version.content.request, null, 2) }}</pre><pre>{{ JSON.stringify(version.content.steps, null, 2) }}</pre></el-card></el-timeline-item></el-timeline><el-pagination v-if="caseVersionPages.total.value" class="records-pagination" layout="total, sizes, prev, pager, next" :current-page="caseVersionPages.page.value" :page-size="caseVersionPages.pageSize.value" :page-sizes="[...RECORD_PAGE_SIZES]" :total="caseVersionPages.total.value" @current-change="caseVersionPages.changePage" @size-change="caseVersionPages.changePageSize" /><el-collapse v-model="reverseExpanded"><el-collapse-item title="关联需求（含历史）" name="requirements"><AssetRequirementLinksPanel v-if="reverseExpanded.includes('requirements')" :key="`TEST_CASE-${selected.id}`" asset-type="TEST_CASE" :asset-id="selected.id" :project-id="selected.project_id" :focused-version-id="focusedVersionId" /></el-collapse-item></el-collapse></template>
    </el-drawer>

    <el-dialog v-model="runtimeVisible" title="Runtime Context 与动作 Trace 预览" width="1000px">
      <el-alert v-if="!projectId" title="当前项目未选择，请先关闭预览并选择项目" type="warning" :closable="false" show-icon />
      <template v-else>
        <el-alert :title="`当前项目：${currentProject?.name ?? projectId}`" type="info" :closable="false" show-icon class="runtime-project" />
        <div class="runtime-grid"><el-form-item label="初始 Context JSON"><el-input v-model="runtimeContext" type="textarea" :rows="10" class="code-input" /></el-form-item><div><el-form-item label="模拟 Response JSON"><el-input v-model="runtimeResponse" type="textarea" :rows="8" class="code-input" /></el-form-item><el-form-item label="response_time_ms"><el-input-number v-model="runtimeResponseTime" :min="0" :max="86400000" :step="10" /></el-form-item></div></div>
        <el-alert v-if="runtimeResult" :title="`Final Status：${runtimeResult.final_status}`" :type="runtimeResult.final_status === 'PASS' ? 'success' : runtimeResult.final_status === 'REVIEW' ? 'warning' : 'error'" :closable="false" show-icon />
        <el-tabs v-if="runtimeResult" type="border-card" class="runtime-result-tabs"><el-tab-pane label="渲染后请求"><pre>{{ JSON.stringify(runtimeResult.rendered_request, null, 2) }}</pre></el-tab-pane><el-tab-pane label="断言结果"><el-table :data="runtimeResult.assertion_results" stripe><el-table-column prop="sequence" label="#" width="55" /><el-table-column prop="name" label="名称" min-width="150" /><el-table-column prop="type" label="类型" min-width="145" /><el-table-column prop="status" label="状态" width="90" /><el-table-column label="期望值 / 实际值" min-width="210"><template #default="{ row }"><div>期望：<code>{{ JSON.stringify(row.expected) }}</code></div><div>实际：<code>{{ JSON.stringify(row.actual) }}</code></div></template></el-table-column><el-table-column prop="message" label="消息" min-width="260" /><el-table-column label="AI 追踪" min-width="220"><template #default="{ row }"><div v-if="row.confidence !== null && row.confidence !== undefined">置信度={{ row.confidence }} · {{ row.reason }}</div><div v-if="row.ai_call_id">调用 #{{ row.ai_call_id }} · {{ row.actual_model }}</div><div v-if="row.fallback_used">已切换备用模型</div><div v-if="row.repair_used">已修复输出</div></template></el-table-column></el-table></el-tab-pane><el-tab-pane label="操作追踪"><el-table :data="runtimeResult.traces" stripe><el-table-column prop="sequence" label="#" width="65" /><el-table-column prop="phase" label="阶段" width="90" /><el-table-column prop="action_type" label="操作" width="180" /><el-table-column prop="status" label="状态" width="100" /><el-table-column label="详情" min-width="320"><template #default="{ row }"><pre>{{ JSON.stringify(row.detail, null, 2) }}</pre></template></el-table-column></el-table></el-tab-pane><el-tab-pane label="最终上下文"><pre>{{ JSON.stringify(runtimeResult.context, null, 2) }}</pre></el-tab-pane><el-tab-pane label="提取结果"><pre>{{ JSON.stringify(runtimeResult.extracted, null, 2) }}</pre></el-tab-pane></el-tabs>
      </template>
      <template #footer><el-button @click="runtimeVisible = false">关闭</el-button><el-button type="primary" :loading="runtimeLoading" :disabled="!projectId" @click="previewRuntime">执行预览</el-button></template>
    </el-dialog>

    <el-dialog v-model="dataPreviewVisible" title="数据集 Iteration Context 预览" width="950px"><el-alert title="这里只预览数据快照，不启动 Runner；row_index 从 1 开始。" type="info" :closable="false" show-icon /><el-table v-if="dataPreview" :data="dataPreview.items" stripe max-height="420"><el-table-column prop="row_index" label="row_index" width="100" /><el-table-column label="参数快照" min-width="300"><template #default="{ row }"><pre>{{ JSON.stringify(row.parameters, null, 2) }}</pre></template></el-table-column><el-table-column label="Iteration Context" min-width="360"><template #default="{ row }"><pre>{{ JSON.stringify(row.context, null, 2) }}</pre></template></el-table-column></el-table></el-dialog>
  </div>
</template>

<style scoped>
.case-heading,.case-actions,.case-summary,.drawer-title,.drawer-title>div,.request-settings{display:flex;align-items:center;gap:12px}.case-heading,.drawer-title{justify-content:space-between}.case-summary{margin:18px 0}.case-summary>div{min-width:150px;padding:16px 20px;border:1px solid #e4e7ed;border-radius:10px;background:#fff}.case-summary strong{display:block;font-size:26px}.case-summary span{color:#7a8599;font-size:13px}.form-grid,.request-line,.runtime-grid,.data-source-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.request-line{grid-template-columns:140px 1fr}.runtime-grid{grid-template-columns:1fr 1fr}.body-editor,.auth-editor{display:grid;gap:12px}.request-settings{margin-top:16px}.runtime-button{margin-top:14px}.code-input :deep(textarea),pre{font-family:JetBrains Mono,Consolas,monospace}pre{padding:12px;overflow:auto;background:#f7f8fa;border-radius:6px;font-size:12px}.drawer-title{margin-bottom:22px}.drawer-title h2{margin:6px 0}.case-actions{flex-wrap:wrap}.action-tabs{margin-bottom:14px}.action-editor{display:grid;gap:12px}.action-editor-heading,.action-card-heading,.action-card-title,.action-card-tools{display:flex;align-items:center;gap:10px}.action-editor-heading,.action-card-heading{justify-content:space-between}.action-editor-heading{padding:4px 0}.action-editor-heading>div{display:flex;align-items:baseline;gap:10px}.muted,.enabled-label{color:#7a8599;font-size:13px}.action-card{padding:14px;border:1px solid #dcdfe6;border-radius:8px;background:#fff}.action-card-heading{margin-bottom:14px}.action-card-title{flex-wrap:wrap}.action-index{display:inline-flex;width:25px;height:25px;align-items:center;justify-content:center;border-radius:50%;background:#eef2ff;color:#3b5ccc;font-size:12px}.action-card-tools{flex-wrap:wrap}.enabled-label{display:flex;align-items:center;gap:6px}.action-fields{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.action-fields-wide{grid-template-columns:repeat(3,1fr)}.action-fields-one{display:block}.field-span-all{grid-column:1 / -1}.data-source-grid{margin-top:16px}.advanced-actions{margin-top:2px}.advanced-json{margin-top:12px}.runtime-project{margin-bottom:14px}.runtime-result-tabs{margin-top:18px}.runtime-result-tabs :deep(.el-table pre){margin:0;max-height:160px}.runtime-result-tabs :deep(.el-table .cell){white-space:normal}@media(max-width:900px){.case-heading{align-items:flex-start;flex-direction:column}.form-grid,.request-line,.runtime-grid,.action-fields,.action-fields-wide,.data-source-grid{grid-template-columns:1fr}.case-summary{flex-wrap:wrap}.action-card-heading{align-items:flex-start;flex-direction:column}.action-card-tools{width:100%;justify-content:flex-end}}
.retry-policy-panel{display:grid;gap:12px;margin-top:16px;padding:16px;border:1px solid #e4e7ed;border-radius:10px;background:#fafbfc}.retry-policy-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.retry-policy-heading>div{display:flex;align-items:baseline;gap:10px}.retry-policy-grid{display:grid;grid-template-columns:repeat(2,minmax(0,220px));gap:14px}.retry-policy-options{display:flex;flex-wrap:wrap;gap:8px 18px}@media(max-width:900px){.retry-policy-grid{grid-template-columns:1fr}}
.assertion-editor{display:grid;gap:12px;margin-top:16px}.assertion-card{border-left:4px solid #6c5ce7}
.focused-version-card{outline:2px solid #6c7cff}
.case-editor-form{margin:-8px -20px -12px}.case-editor-shell{display:grid;height:min(70vh,720px);min-height:520px;grid-template-columns:235px minmax(0,1fr);overflow:hidden;border-top:1px solid #e7eaf1;border-bottom:1px solid #e7eaf1}.case-editor-nav{overflow-y:auto;padding:20px 14px;background:linear-gradient(180deg,#f7f9ff 0%,#f4f6fb 100%);border-right:1px solid #e1e6ef}.case-editor-nav-title{padding:0 12px 12px;color:#7a8599;font-size:13px;font-weight:700;letter-spacing:.08em}.case-editor-nav-item{display:flex;width:100%;align-items:flex-start;gap:11px;margin:3px 0;padding:13px 12px;border:1px solid transparent;border-radius:10px;background:transparent;color:#344054;text-align:left;cursor:pointer;transition:background .16s ease,border-color .16s ease,transform .16s ease}.case-editor-nav-item:hover{background:#fff;border-color:#dde3ef;transform:translateX(2px)}.case-editor-nav-item.active{background:#fff;border-color:#b9c7ff;box-shadow:0 7px 20px rgba(63,86,180,.09);color:#2945b8}.case-editor-nav-index{display:inline-flex;flex:0 0 26px;width:26px;height:26px;align-items:center;justify-content:center;border-radius:8px;background:#e6ebff;color:#3b5ccc;font-size:13px;font-weight:700}.case-editor-nav-item.active .case-editor-nav-index{background:#4263eb;color:#fff}.case-editor-nav-item strong,.case-editor-nav-item small{display:block}.case-editor-nav-item strong{font-size:14px;line-height:1.35}.case-editor-nav-item small{margin-top:4px;color:#7a8599;font-size:12px;line-height:1.35}.case-editor-workspace{display:flex;min-width:0;overflow-y:auto;flex-direction:column;padding:22px 26px 16px;background:#fff}.case-editor-workspace-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding-bottom:16px;border-bottom:1px solid #edf0f5}.case-editor-workspace-heading span{color:#6c7a94;font-size:13px}.case-editor-workspace-heading h3{margin:4px 0 2px;color:#1f2a44;font-size:21px}.case-editor-workspace-heading p{margin:0;color:#7a8599;font-size:14px}.case-editor-step-panel{flex:1;padding-top:20px}.case-editor-step-panel>.el-alert:first-child{margin-bottom:18px}.case-editor-step-actions{display:flex;align-items:center;justify-content:flex-end;gap:12px;margin-top:22px;padding-top:16px;border-top:1px solid #edf0f5}.case-editor-step-actions>span{margin-right:auto;color:#7a8599;font-size:13px}.assertion-expected-control{width:100%}.assertion-expected-control small{display:block;margin-top:6px;color:#7a8599;font-size:12px;line-height:1.45}:deep(.case-editor-dialog){max-width:calc(100vw - 32px)}:deep(.case-editor-dialog .el-dialog__body){overflow:hidden;padding-top:8px}:deep(.case-editor-dialog .el-dialog__footer){padding-top:14px}
.case-editor-step-panel :deep(.el-select){width:100%}.requirement-link-help{display:block;width:100%;margin-top:7px;line-height:1.5}
@media(max-width:900px){.case-editor-form{margin:-8px -12px -10px}.case-editor-shell{height:calc(100vh - 190px);min-height:440px;grid-template-columns:1fr;grid-template-rows:auto minmax(0,1fr)}.case-editor-nav{display:flex;gap:8px;overflow-x:auto;overflow-y:hidden;padding:12px;border-right:0;border-bottom:1px solid #e1e6ef}.case-editor-nav-title{display:none}.case-editor-nav-item{min-width:174px;margin:0;padding:10px}.case-editor-nav-item:hover{transform:none}.case-editor-workspace{padding:18px 16px}.case-editor-workspace-heading{align-items:flex-start}.case-editor-step-panel{padding-top:16px}}
</style>
